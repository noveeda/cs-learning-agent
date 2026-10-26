# 파일 역할: 품질 하한 시험(설계 2-4) — 공식 데이터셋의 자료구조·운영체제 문항을 운영 서빙 모델에 풀려 정답률을 잼
# - select-cs-bench: CS-Bench 영어 test에서 코드 규칙으로 문항을 골라 ID 목록(quality_floor_ids/cs_bench.json, 커밋 대상)을 만듦
# - run: 고른 문항을 운영 설정(추론 끔, 온도 0)으로 한 문항씩 묻고 결과를 한 줄씩 기록(중단 후 이어하기)
# - summarize: 세트·영역·형식별 정답률을 회차 평균·범위로, 파싱 실패율과 보기만 조건을 따로 집계
# - 원본 데이터는 Git 밖(ai-core/data/raw/ 또는 인자로 준 경로), 결과는 ai-core/outputs/eval/ 아래에만 둠
#   KMMLU(CC-BY-ND)는 원문 그대로만 쓰고, 저장소에는 문항 ID와 분류(quality_floor_ids/kmmlu.json, 데이터 PR)만 둠
"""Quality-floor test: serving-model accuracy on CS-Bench and KMMLU data-structure/OS items."""
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI, OpenAIError

AI_CORE = Path(__file__).resolve().parents[1]
IDS_DIR = Path(__file__).resolve().parent / 'quality_floor_ids'
RAW = AI_CORE / 'data' / 'raw'
DEFAULT_DATA = {'cs_bench': RAW / 'cs_bench' / 'test.jsonl', 'kmmlu': RAW / 'kmmlu' / 'Computer-Science-test.csv'}
# git-ignored 폴더; 측정 결과 원본은 이 아래에만 씀
EVAL_ROOT = AI_CORE / 'outputs' / 'eval'

# 선별 규칙(계획 Task 5): 영역이 자료구조·운영체제이고 형식이 객관식·참거짓인 문항 → 영어 test 기준 860문항
# - 빈칸·서술 형식은 정답을 문자열 비교로 채점할 수 없어 뺌
CS_BENCH_AREAS = {'Data Structure and Algorithm': 'dsa', 'Operating System': 'os'}
CS_BENCH_FORMATS = {'Multiple-choice': 'multiple_choice', 'Assertion': 'assertion'}
LETTERS = ('A', 'B', 'C', 'D')
CHOICES = {'multiple_choice': list(LETTERS), 'assertion': ['True', 'False']}
# 문항 분류 중 실행 대상; KMMLU의 'other'(자료구조·운영체제 밖)는 고르지 않음
AREAS = ('dsa', 'os')

# 세트별 지시문; 세트 언어(영어 원문·한국어 원문)에 맞춰 같은 뜻으로 씀
# - 보기만 조건은 질문이 없다는 사실을 알리고 그래도 하나를 고르게 함(Balepur 2024): 단서·암기로 맞히는 비율을 재기 위함
INSTRUCTIONS = {
    'cs_bench': {'multiple_choice': 'Answer the computer science multiple-choice question. Choose the single correct option letter.',
                 'assertion': 'Decide whether the computer science statement is true or false.',
                 'options_only': 'The question is hidden; only the options are given. Choose the option letter most likely to be correct.'},
    'kmmlu': {'multiple_choice': '다음 컴퓨터 과학 객관식 문제의 정답 보기 기호 하나를 고른다.',
              'options_only': '질문은 가려져 있고 보기만 주어진다. 정답일 가능성이 가장 높은 보기 기호 하나를 고른다.'},
}
# 운영 보호: 답은 {"answer": "A"} 한 줄이라 32토큰이면 충분하고, 잘리면 파싱 실패로 셈
MAX_TOKENS = 32
TIMEOUT = 30
# 연결 오류·시간 초과에 한 문항당 다시 시도하는 횟수; 끝내 실패하면 실행을 멈추고 이어하기로 재개(서버 장애를 오답으로 세지 않음)
ATTEMPTS = 2


# 역할: CS-Bench 행 목록에서 선별 규칙에 맞는 문항을 골라 [{id, area, format}]을 ID 순서로 반환
def select_cs_bench(rows):
    picked = [{'id': row['ID'], 'area': CS_BENCH_AREAS[row['Domain']], 'format': CS_BENCH_FORMATS[row['Format']]}
              for row in rows if row['Domain'] in CS_BENCH_AREAS and row['Format'] in CS_BENCH_FORMATS]
    return sorted(picked, key=lambda entry: entry['id'])


def load_cs_bench_rows(path):
    with open(path, encoding='utf-8') as file:
        return [json.loads(line) for line in file if line.strip()]


# 역할: KMMLU Computer-Science test CSV를 문항 목록으로 읽음
# - ID는 머리줄을 뺀 1부터의 행 번호로 'kmmlu-cs-<n>'; 원본에 문항 ID 열이 없어 분류 목록이 이 번호로 문항을 가리킴
# - 정답 1~4를 보기 기호 A~D로 바꾸고, 문항별 수험생 정답률(Human Accuracy)을 함께 둠
def load_kmmlu(path):
    with open(path, encoding='utf-8', newline='') as file:
        return [{'id': f'kmmlu-cs-{n}', 'format': 'multiple_choice', 'question': row['question'],
                 'options': {letter: row[letter] for letter in LETTERS}, 'answer': LETTERS[int(row['answer']) - 1],
                 'human_accuracy': float(row['Human Accuracy'])}
                for n, row in enumerate(csv.DictReader(file), start=1)]


# 역할: 세트의 원본과 ID 목록을 합쳐 실행할 문항 목록([{id, area, format, question, options, answer, human_accuracy?}])을 만듦
# - ID 목록에 있는데 원본에 없으면 오류: 다른 판본의 원본을 받아 문항이 조용히 빠지는 일을 막음
def build_items(set_name, data_path, ids_path):
    entries = [entry for entry in json.loads(Path(ids_path).read_text(encoding='utf-8')) if entry['area'] in AREAS]
    if set_name == 'cs_bench':
        source = {}
        for row in load_cs_bench_rows(data_path):
            options = {letter: row[letter] for letter in LETTERS} if row['Format'] == 'Multiple-choice' else None
            # 참거짓 문항의 정답은 원본에서 JSON 불리언이라 응답 선택지('True'/'False')와 같은 문자열로 맞춤
            source[row['ID']] = {'question': row['Question'], 'options': options, 'answer': str(row['Answer'])}
    else:
        source = {item['id']: item for item in load_kmmlu(data_path)}
    missing = [entry['id'] for entry in entries if entry['id'] not in source]
    if missing:
        raise ValueError(f'원본에 없는 문항 ID {len(missing)}개: {missing[:5]}')
    items = []
    for entry in entries:
        item = dict(source[entry['id']], id=entry['id'], area=entry['area'])
        item['format'] = entry.get('format', item.get('format', 'multiple_choice'))
        items.append(item)
    return items


# 역할: 조건에 맞는 문항만 남김; 참거짓 문항은 질문을 지우면 남는 내용이 없어 보기만 조건에서 뺌
def condition_items(items, options_only):
    return [item for item in items if not options_only or item['format'] == 'multiple_choice']


# 역할: 문항 하나의 대화 메시지를 만듦; 보기만 조건이면 질문 문장을 아예 넣지 않음
def build_messages(set_name, item, options_only):
    instruction = INSTRUCTIONS[set_name]['options_only' if options_only else item['format']]
    user = {} if options_only else {'question': item['question']}
    if item['options']:
        user['options'] = item['options']
    return [{'role': 'system', 'content': instruction}, {'role': 'user', 'content': json.dumps(user, ensure_ascii=False)}]


# 역할: 문항 하나를 운영 설정으로 묻고 {prediction, outcome, raw}를 반환
# - json_schema로 답을 형식별 선택지 하나로 묶음(가이드 디코딩)
# - outcome: correct / wrong / parse_failure; 잘린 출력·JSON 아님·선택지 밖 값은 오답이 아니라 parse_failure(Review Focus 5)
# - 연결 오류는 ATTEMPTS번까지 다시 묻고, 그래도 실패하면 예외를 그대로 올려 실행을 멈춤
def ask(client, model, set_name, item, options_only):
    choices = CHOICES[item['format']]
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['answer'],
              'properties': {'answer': {'type': 'string', 'enum': choices}}}
    kwargs = dict(model=model, temperature=0.0, max_tokens=MAX_TOKENS, timeout=TIMEOUT,
                  messages=build_messages(set_name, item, options_only),
                  response_format={'type': 'json_schema', 'json_schema': {'name': 'answer', 'strict': True, 'schema': schema}},
                  extra_body={'chat_template_kwargs': {'enable_thinking': False}})
    for attempt in range(1, ATTEMPTS + 1):
        try:
            choice = client.chat.completions.create(**kwargs).choices[0]
            break
        except OpenAIError:
            if attempt == ATTEMPTS:
                raise
            time.sleep(2)
    content = (choice.message.content or '').strip()
    prediction = None
    if choice.finish_reason == 'stop':
        try:
            value = json.loads(content).get('answer')
            prediction = value if value in choices else None
        except (ValueError, AttributeError):
            prediction = None
    if prediction is None:
        return {'prediction': None, 'outcome': 'parse_failure', 'raw': content[:200]}
    return {'prediction': prediction, 'outcome': 'correct' if prediction == item['answer'] else 'wrong', 'raw': None}


# 역할: 문항 목록을 runs회 차례로 묻고 결과를 out(JSONL)에 한 줄씩 덧붙임
# - 이미 기록된 (회차, 문항, 조건)은 건너뜀: 중단 뒤 같은 명령으로 이어서 실행하기 위함
# - 요청은 하나씩 보냄: 운영 서버(--max-num-seqs 1)와 GPU를 같이 쓰므로 동시에 보내지 않음
def run(client, model, set_name, items, runs, options_only, out, progress=None):
    out = Path(out)
    done = set()
    if out.exists():
        with open(out, encoding='utf-8') as file:
            done = {(r['run'], r['id'], r['options_only']) for r in map(json.loads, filter(str.strip, file))}
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'a', encoding='utf-8') as file:
        for run_no in range(1, runs + 1):
            for item in condition_items(items, options_only):
                if (run_no, item['id'], options_only) in done:
                    continue
                started = time.perf_counter()
                result = ask(client, model, set_name, item, options_only)
                record = {'set': set_name, 'run': run_no, 'options_only': options_only, 'id': item['id'],
                          'area': item['area'], 'format': item['format'], 'answer': item['answer'],
                          'human_accuracy': item.get('human_accuracy'), **result,
                          'seconds': round(time.perf_counter() - started, 3),
                          'at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
                file.write(json.dumps(record, ensure_ascii=False) + '\n')
                file.flush()
                if progress:
                    progress(f"{run_no} {item['id']}: {record['outcome']}")


# 역할: 기록 묶음 하나의 칸; 회차별 정답률(파싱 실패 포함 분모)의 평균·최솟값·최댓값과 파싱 실패율
# - changed_items: 회차마다 답(파싱 실패는 None)이 달라진 문항 수; 온도 0에서 반복이 실제로 다른 결과를 냈는지 보이기 위함
# - 수험생 정답률 평균은 첫 회차의 문항으로만 셈: 같은 문항을 회차 수만큼 겹쳐 세지 않기 위함
def _cell(records):
    by_run = {}
    for r in records:
        by_run.setdefault(r['run'], []).append(r)
    rates = [sum(r['outcome'] == 'correct' for r in rs) / len(rs) for _, rs in sorted(by_run.items())]
    failures = sum(r['outcome'] == 'parse_failure' for r in records)
    answers = {}
    for r in records:
        answers.setdefault(r['id'], set()).add(r.get('prediction'))
    cell = {'items': max(len(rs) for rs in by_run.values()), 'runs': len(rates),
            'accuracy_mean': round(sum(rates) / len(rates), 4), 'accuracy_min': round(min(rates), 4),
            'accuracy_max': round(max(rates), 4), 'parse_failures': failures,
            'parse_failure_rate': round(failures / len(records), 4),
            'changed_items': sum(len(values) > 1 for values in answers.values())}
    human = [r['human_accuracy'] for r in by_run[min(by_run)] if r.get('human_accuracy') is not None]
    if human:
        cell['human_accuracy_mean'] = round(sum(human) / len(human), 4)
    return cell


# 역할: 실행 기록을 '<세트>/standard|options_only'마다 전체·영역별·형식별 칸으로 집계
def summarize(records):
    groups = {}
    for r in records:
        groups.setdefault(f"{r['set']}/{'options_only' if r['options_only'] else 'standard'}", []).append(r)
    summary = {}
    for key, rows in sorted(groups.items()):
        by = lambda field: {value: _cell([r for r in rows if r[field] == value]) for value in sorted({r[field] for r in rows})}
        summary[key] = {'overall': _cell(rows), 'by_area': by('area'), 'by_format': by('format')}
    return summary


# 역할: 명령행 진입점(select-cs-bench, run, summarize)
# - run의 --out은 ai-core/outputs/eval/ 아래만 허용(결과 원본의 Git 유입 방지); 이미 있으면 이어서 씀
# - 시작 전에 모델 목록을 한 번 조회해 서버가 없으면 종료: 연결 실패를 결과로 남기지 않기 위함
def main(argv=None):
    parser = argparse.ArgumentParser(description='품질 하한 시험(CS-Bench·KMMLU)')
    commands = parser.add_subparsers(dest='command', required=True)
    select = commands.add_parser('select-cs-bench', help='CS-Bench 선별 ID 목록 생성')
    select.add_argument('--data', type=Path, default=DEFAULT_DATA['cs_bench'], help='CS-Bench English test.jsonl')
    select.add_argument('--out', type=Path, default=IDS_DIR / 'cs_bench.json')
    run_cmd = commands.add_parser('run', help='운영 모델로 문항 풀이')
    run_cmd.add_argument('--set', required=True, choices=sorted(DEFAULT_DATA))
    run_cmd.add_argument('--data', type=Path, help='원본 경로(기본: ai-core/data/raw/ 아래)')
    run_cmd.add_argument('--runs', type=int, default=3)
    run_cmd.add_argument('--options-only', action='store_true', help='질문을 지우고 보기만 줌(객관식만)')
    run_cmd.add_argument('--base-url', default=os.getenv('VLLM_BASE_URL', 'http://127.0.0.1:8000/v1'))
    run_cmd.add_argument('--out', required=True, type=Path, help='ai-core/outputs/eval/ 아래 JSONL 경로')
    summary_cmd = commands.add_parser('summarize', help='실행 기록 집계')
    summary_cmd.add_argument('records', nargs='+', type=Path)
    args = parser.parse_args(argv)

    if args.command == 'select-cs-bench':
        picked = select_cs_bench(load_cs_bench_rows(args.data))
        # 문항 하나를 한 줄로 써서 목록이 바뀔 때 diff에서 어느 문항이 바뀌었는지 바로 보이게 함
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text('[\n' + ',\n'.join(json.dumps(entry) for entry in picked) + '\n]\n', encoding='utf-8')
        print(f'{args.out}: {len(picked)}문항')
        return
    if args.command == 'summarize':
        records = []
        for path in args.records:
            with open(path, encoding='utf-8') as file:
                records += [json.loads(line) for line in file if line.strip()]
        print(json.dumps(summarize(records), ensure_ascii=False, indent=2))
        return

    out = args.out.resolve()
    if not out.is_relative_to(EVAL_ROOT):
        parser.error(f'--out은 {EVAL_ROOT} 아래여야 합니다: {out}')
    if args.runs < 1:
        parser.error('--runs는 1 이상이어야 합니다.')
    items = build_items(args.set, args.data or DEFAULT_DATA[args.set], IDS_DIR / f'{args.set}.json')

    client = OpenAI(base_url=args.base_url, api_key=os.getenv('VLLM_API_KEY', 'not-needed'), max_retries=0)
    try:
        model = os.getenv('VLLM_MODEL') or client.models.list(timeout=10).data[0].id
    except (OpenAIError, IndexError) as exc:
        parser.error(f'모델 서버에 연결하지 못했습니다: {exc}')
    print(f'시작 {datetime.now(timezone.utc).isoformat(timespec="seconds")} {args.set} {len(items)}문항 × {args.runs}회 '
          f'options_only={args.options_only} model={model}', flush=True)
    run(client, model, args.set, items, args.runs, args.options_only, out, progress=lambda line: print(line, flush=True))
    print(f'끝 {datetime.now(timezone.utc).isoformat(timespec="seconds")} {out}', flush=True)


if __name__ == '__main__':
    sys.exit(main())
