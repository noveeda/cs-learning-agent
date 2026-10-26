# 파일 역할: Mohler 단답 채점 데이터(영어 원문)로 운영 서술 채점 경로의 사람 일치도를 재는 개발용 검증 도구(계획 Task 3)
# - download: Hugging Face 미러의 원본 행을 Git 밖(ai-core/data/raw/mohler/)에 JSONL로 내려받음
# - split_questions: 문항 단위로 dev·report 절반씩 나눔(같은 문항의 답이 양쪽에 섞이지 않게)
# - grade: 운영 채점 경로(StuckModelGateway.grade_explanation)로 답마다 루브릭 항목 충족을 판정, 중단 후 이어하기
# - evaluate: dev에서 AI 통과 임계값을 고르고 report에서 한 번만 kappa·거짓 통과율 등을 잼
# - 이 결과는 영어 원문 개발용이고, M3 최종 근거는 한국어 사람 라벨이다(설계 2-4)
#
# 데이터 출처
# - 미러: Hugging Face `Viswa45/ASAG`(config raw, split open_ended, 2,273행), 미러 표기 라이선스 CC-BY-4.0
# - 원 논문: Michael Mohler, Razvan Bunescu, Rada Mihalcea. "Learning to Grade Short Answer Questions using
#   Semantic Similarity Measures and Dependency Graph Alignments." ACL-HLT 2011.
# - 원본·루브릭·결과는 모두 Git 밖에 두고, 저장소에는 이 스크립트만 둠
"""Mohler ASAG development check for the explanation-grading path."""
import argparse
import json
import random
import sys
import threading
import urllib.parse
import urllib.request
from pathlib import Path

AI_CORE = Path(__file__).resolve().parents[1]
# 저장소 루트에서 `python ai-core/eval/mohler.py`로 실행해도 serving·eval 패키지를 찾게 함
if str(AI_CORE) not in sys.path:
    sys.path.insert(0, str(AI_CORE))

from eval.metrics import cohen_kappa, wilson_interval  # noqa: E402
from serving.item_bank import Item  # noqa: E402

RAW = AI_CORE / 'data' / 'raw' / 'mohler'
EVAL_ROOT = AI_CORE / 'outputs' / 'eval'
MIRROR = 'Viswa45/ASAG'
ROWS_API = 'https://datasets-server.huggingface.co/rows'
# 원본 parquet을 읽을 라이브러리가 없어(새 의존성 금지) 행 JSON API를 쪽 단위로 받음; API의 쪽 크기 상한이 100행
PAGE = 100
COLUMNS = ('id', 'question', 'instructor_answer', 'student_answer', 'score_grader_1', 'score_grader_2', 'score_avg')
# 분할 seed는 결과를 보기 전에 고정(계획 작성일)
SPLIT_SEED = 20261026
# 사람 통과 기준(score_avg 0~5점, 이상이면 통과): 주 기준 4.0, 함께 보고 3.5·4.5 (계획 Task 3, 결과 보기 전에 고정)
HUMAN_THRESHOLDS = (4.0, 3.5, 4.5)
PRIMARY_HUMAN = 4.0
# AI 통과 기준 후보: 루브릭 충족 비율이 이 값 이상이면 통과; 표기는 계획의 1.0·0.75·0.67·0.5
# - 0.67은 3항목 중 2개(2/3)를 뜻하므로 실제 비교는 분수로 함(0.6667 < 0.67로 떨어지지 않게)
AI_CANDIDATES = (('1.0', 1.0), ('0.75', 3 / 4), ('0.67', 2 / 3), ('0.5', 1 / 2))
EPSILON = 1e-9


# 역할: 미러의 open_ended 행 전체를 쪽 단위로 받아 out_dir/open_ended.jsonl로 쓰고 경로를 반환
# - 이미 있으면 덮어쓰지 않음: 다른 워커가 같은 원본을 쓰고 있을 수 있기 때문
def download(out_dir=RAW):
    out = Path(out_dir) / 'open_ended.jsonl'
    if out.exists():
        raise FileExistsError(f'이미 있는 원본은 덮어쓰지 않습니다: {out}')
    rows, total = [], None
    while total is None or len(rows) < total:
        query = urllib.parse.urlencode({'dataset': MIRROR, 'config': 'raw', 'split': 'open_ended',
                                        'offset': len(rows), 'length': PAGE})
        with urllib.request.urlopen(f'{ROWS_API}?{query}', timeout=60) as response:
            page = json.load(response)
        total = page['num_rows_total']
        if not page['rows']:
            raise ValueError(f'{len(rows)}/{total}행에서 응답이 비었습니다.')
        rows += [{column: entry['row'][column] for column in COLUMNS} for entry in page['rows']]
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'x', encoding='utf-8') as file:
        file.writelines(json.dumps(r, ensure_ascii=False) + '\n' for r in rows)
    return out


def load_jsonl(path):
    with open(path, encoding='utf-8') as file:
        return [json.loads(line) for line in file if line.strip()]


# 역할: 행 id에서 문항 ID를 냄; 'E01.Q01.A00'(과제·문항·답 번호) → 'E01.Q01'
# - 루브릭 파일(rubrics.jsonl)의 question_id도 이 규칙을 씀
def question_id(row_id):
    return row_id.rsplit('.', 1)[0]


# 역할: 문항 ID를 seed로 섞어 절반은 dev, 나머지는 report로 나눠 (dev_ids, report_ids)를 정렬된 목록으로 반환
# - 행 순서와 무관하게 같은 seed면 같은 분할(문항 ID를 먼저 정렬한 뒤 섞음)
def split_questions(rows, seed=SPLIT_SEED):
    ids = sorted({question_id(r['id']) for r in rows})
    random.Random(seed).shuffle(ids)
    half = len(ids) // 2
    return sorted(ids[:half]), sorted(ids[half:])


# 역할: rubrics.jsonl({question_id, rubric: [..]})을 {question_id: rubric} 사전으로 읽음
def load_rubrics(path):
    return {entry['question_id']: entry['rubric'] for entry in load_jsonl(path)}


# 역할: 답마다 운영 채점 경로로 루브릭 항목 충족을 판정해 [{id, met: list[bool]}]을 rows 순서로 반환
# - 문항·모범 답(instructor_answer → reference)·루브릭은 영어 원문 그대로 넘김
#   Item 검증(문항 500자·근거 400자 상한, 한국어 문항 ID 규칙)은 문항 은행용이라 건너뛰고 필요한 필드만 채움(model_construct)
# - out(JSONL)에 이미 있는 답은 다시 채점하지 않고, 새 판정은 한 줄씩 덧붙임(중단 후 재개)
# - 루브릭이 없는 문항의 답은 건너뜀; 모델 오류(LLMUnavailable)는 그대로 올려 실행을 멈춤(서버 장애를 판정으로 남기지 않음)
def grade(rows, rubrics, gateway, out, progress=None):
    out = Path(out)
    saved = {g['id']: g for g in load_jsonl(out)} if out.exists() else {}
    out.parent.mkdir(parents=True, exist_ok=True)
    grades = []
    with open(out, 'a', encoding='utf-8') as file:
        for r in rows:
            rubric = rubrics.get(question_id(r['id']))
            if rubric is None:
                continue
            if r['id'] not in saved:
                item = Item.model_construct(id=r['id'], concept='mohler', purpose='explanation', objective=0,
                                            format='explanation', question=r['question'],
                                            rationale=r['instructor_answer'], rubric=rubric)
                saved[r['id']] = {'id': r['id'], 'met': gateway.grade_explanation(item, r['student_answer'])}
                file.write(json.dumps(saved[r['id']]) + '\n')
                file.flush()
                if progress:
                    progress(f"{r['id']}: {saved[r['id']]['met']}")
            grades.append(saved[r['id']])
    return grades


# 역할: 순위 상관(Spearman); 동점은 평균 순위로 두고 순위끼리의 Pearson 상관을 냄
def spearman(x, y):
    def ranks(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        result, i = [0.0] * len(values), 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            for k in range(i, j + 1):
                result[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return result

    rx, ry = ranks(x), ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    var = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return cov / var if var else 0.0


# 역할: 한 분할의 사람 판정(human)·AI 판정(ai) 비교 지표
# - 정답 쪽으로 치우친 점수(평균 4.17)에서 kappa가 낮게 나오는 역설에 대비해 일치율·민감도·특이도를 함께 냄
# - 거짓 통과: 사람 미통과인데 AI가 통과시킨 답(학습자가 모르는데 넘어가는 위험)
def _compare(human, ai):
    human_fail = sum(not h for h in human)
    false_pass = sum(a and not h for h, a in zip(human, ai))
    human_pass = len(human) - human_fail
    low, high = wilson_interval(false_pass, human_fail)
    return {'n': len(human), 'kappa': round(cohen_kappa(human, ai), 4),
            'agreement': round(sum(h == a for h, a in zip(human, ai)) / len(human), 4),
            'sensitivity': round(sum(a and h for h, a in zip(human, ai)) / human_pass, 4) if human_pass else None,
            'specificity': round((human_fail - false_pass) / human_fail, 4) if human_fail else None,
            'human_fail': human_fail, 'false_pass': false_pass,
            'false_pass_rate': round(false_pass / human_fail, 4) if human_fail else None,
            'false_pass_interval': [round(low, 4), round(high, 4)]}


# 역할: 사람 통과 기준마다 dev에서 AI 임계값을 고르고 report에서 지표를 한 번 재서 반환
# - 임계값은 dev kappa가 가장 큰 후보; 같으면 후보 순서상 앞(더 엄격한 값)을 고름
# - report 지표: kappa·일치율·민감도·특이도·거짓 통과율과 Wilson 95% 구간, 충족 비율과 score_avg의 Spearman,
#   사람 채점자 둘 사이 kappa(같은 통과 기준; AI-사람 일치도의 현실적 상한)
# - 채점 결과가 없는 답(루브릭 없음 등)은 빼고, 남은 개수를 counts에 남김
def evaluate(rows, grades, dev_ids, report_ids):
    ratio = {g['id']: sum(g['met']) / len(g['met']) for g in grades if g['met']}
    graded = [r for r in rows if r['id'] in ratio]
    dev = [r for r in graded if question_id(r['id']) in set(dev_ids)]
    report = [r for r in graded if question_id(r['id']) in set(report_ids)]
    passes = lambda rs, threshold: [ratio[r['id']] + EPSILON >= threshold for r in rs]
    by_human = {}
    for human_threshold in HUMAN_THRESHOLDS:
        human = lambda rs, key='score_avg': [r[key] >= human_threshold for r in rs]
        dev_kappa = {label: round(cohen_kappa(human(dev), passes(dev, value)), 4) for label, value in AI_CANDIDATES}
        label, value = max(AI_CANDIDATES, key=lambda candidate: dev_kappa[candidate[0]])
        result = _compare(human(report), passes(report, value))
        result['spearman'] = round(spearman([ratio[r['id']] for r in report], [r['score_avg'] for r in report]), 4)
        result['human_kappa'] = round(cohen_kappa(human(report, 'score_grader_1'), human(report, 'score_grader_2')), 4)
        by_human[str(human_threshold)] = {'ai_threshold': float(label), 'dev_kappa': dev_kappa, 'report': result}
    return {'primary': by_human[str(PRIMARY_HUMAN)], 'by_human_threshold': by_human,
            'counts': {'rows': len(rows), 'graded': len(graded), 'dev_answers': len(dev), 'report_answers': len(report),
                       'dev_questions': len(dev_ids), 'report_questions': len(report_ids)}}


# 역할: 명령행 진입점(download, grade, evaluate)
# - grade·evaluate의 결과 경로는 ai-core/outputs/eval/ 아래만 허용(결과 원본의 Git 유입 방지)
def main(argv=None):
    parser = argparse.ArgumentParser(description='Mohler 영어 원문 서술 채점 개발 검증')
    commands = parser.add_subparsers(dest='command', required=True)
    download_cmd = commands.add_parser('download')
    download_cmd.add_argument('--out-dir', type=Path, default=RAW)
    grade_cmd = commands.add_parser('grade', help='운영 채점 경로로 채점(이어하기)')
    grade_cmd.add_argument('--data', type=Path, default=RAW / 'open_ended.jsonl')
    grade_cmd.add_argument('--rubrics', type=Path, required=True, help='rubrics.jsonl({question_id, rubric})')
    grade_cmd.add_argument('--out', type=Path, required=True, help='ai-core/outputs/eval/ 아래 JSONL')
    evaluate_cmd = commands.add_parser('evaluate')
    evaluate_cmd.add_argument('--data', type=Path, default=RAW / 'open_ended.jsonl')
    evaluate_cmd.add_argument('--grades', type=Path, required=True)
    evaluate_cmd.add_argument('--seed', type=int, default=SPLIT_SEED)
    evaluate_cmd.add_argument('--out', type=Path, required=True, help='ai-core/outputs/eval/ 아래 JSON')
    args = parser.parse_args(argv)

    if args.command == 'download':
        out = download(args.out_dir)
        print(f'{out}: {len(load_jsonl(out))}행')
        return
    if not args.out.resolve().is_relative_to(EVAL_ROOT):
        parser.error(f'--out은 {EVAL_ROOT} 아래여야 합니다: {args.out}')
    rows = load_jsonl(args.data)
    if args.command == 'grade':
        from serving.concept_map import load_concept_map
        from serving.stuck_llm import StuckModelGateway

        rubrics = load_rubrics(args.rubrics)
        missing = sorted({question_id(r['id']) for r in rows} - set(rubrics))
        print(f'루브릭 {len(rubrics)}문항, 루브릭 없는 문항 {len(missing)}개{": " + ", ".join(missing) if missing else ""}', flush=True)
        gateway = StuckModelGateway(load_concept_map(), threading.Lock())
        grades = grade(rows, rubrics, gateway, args.out, progress=lambda line: print(line, flush=True))
        print(f'{args.out}: {len(grades)}개 채점')
        return
    dev_ids, report_ids = split_questions(rows, args.seed)
    result = evaluate(rows, load_jsonl(args.grades), dev_ids, report_ids)
    result.update(seed=args.seed, dev_ids=dev_ids, report_ids=report_ids)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, 'x', encoding='utf-8') as file:
        json.dump(result, file, indent=2)
    print(json.dumps(result['primary'], indent=2))


if __name__ == '__main__':
    sys.exit(main())
