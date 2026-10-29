## @file
# @brief 근거 검사 하네스 측정 세트 생성기: 범위 결정(scope)·일치 판정(교란 문장)·서술 채점(Mohler 사람 채점) 세트 (계획 Task 10, spec 8-1)
# - 세 세트 모두 정답이 만드는 방식에서 정해짐: 범위는 "이 개념용으로 만든 질문", 판정은 "일부러 망가뜨린 방식", 채점은 사람 점수
# - 질문·교란 문장 생성 모델은 gemma-4-26B(8000번, 읽기 호출만). 외부 API로는 측정 데이터를 만들지 않음
# - 세트는 Git 밖 HARNESS_SETS_DIR(기본 ai-core/outputs/harness-sets/)에, 사용자 표본 시트는 ~/cs-handoff/에 둠
#
# 실행 순서(ai-core 폴더에서)
# 1. 범위 세트와 표본 시트: python -m eval.harness_sets_judge scope
# 2. 맞는 문장 모으기(색인 필요): CS_HARNESS 설정과 무관하게 26B 하네스로 범위 세트의 직접 질문을 처리
#    python -m eval.harness_sets_judge harvest --limit 120
# 3. 판정 세트와 표본 시트: python -m eval.harness_sets_judge perturb
# 4. 채점 세트: python -m eval.harness_sets_judge grading --data /home/infoserver/cs-eval-data/raw/mohler/open_ended.jsonl
#    --rubrics /home/infoserver/cs-eval-data/processed/mohler/rubrics.jsonl
import argparse
import json
import os
import random
import re
import sys
import threading
from pathlib import Path

AI_CORE = Path(__file__).resolve().parents[1]
if str(AI_CORE) not in sys.path:
    sys.path.insert(0, str(AI_CORE))

from eval import mohler  # noqa: E402

# 세트 위치: HARNESS_SETS_DIR(없으면 ai-core/outputs/harness-sets). worktree에서 실행해도 공용 위치에 쓸 수 있게 함
OUT_DIR = Path(os.environ.get('HARNESS_SETS_DIR') or AI_CORE / 'outputs' / 'harness-sets')
HANDOFF = Path.home() / 'cs-handoff'
KINDS = ('number', 'negation', 'addition', 'swap_concept')
# 값으로 쓰인 숫자: 영문자·숫자·소수점 바로 뒤의 숫자('L1', 'IPv4')는 이름의 일부라 빼고 셈
_NUMBER = re.compile(r'(?<![A-Za-z0-9.])\d+(?:\.\d+)?')
_WORD_CHAR = re.compile('[가-힣A-Za-z0-9]')
_LETTER = re.compile('[가-힣A-Za-z]')


## @brief 문장 앞뒤 공백과 앞에 붙은 ':' 같은 기호를 떼고, 내용이 모자라면 None
# - 기준은 하네스 생성 검증과 같음(한글·영문·숫자 5자 이상, 한글·영문 글자 1자 이상)
# - 실측: 26B에 strict JSON schema를 걸면 {"sentence":": "}처럼 ':'만 내거나 ': '를 앞에 붙임
def _clean(sentence: str | None) -> str | None:
    text = (sentence or '').strip().lstrip(':;-·•* ').strip()
    return text if len(_WORD_CHAR.findall(text)) >= 5 and _LETTER.search(text) else None


## @brief 생성 함수가 n개보다 적게 돌려주면 세트 개수가 어긋나므로 멈춤; 많으면 앞의 n개만 씀
def _take(questions, n):
    if len(questions) < n:
        raise ValueError(f'질문이 {n}개 필요한데 {len(questions)}개만 만들어졌습니다.')
    return list(questions[:n])


## @brief 범위 결정 측정 세트: 행 {id, question, previous, expected, kind[, variant]}
# - write_questions(kind, concepts, n) -> list[str]: 종류별 질문 n개를 만드는 함수(실행에서는 26B, 테스트에서는 가짜)
#   - kind: 'direct'(개념 하나), 'compare'(개념 둘), 'outside'(지도 밖), 'code'(코드 오류 찾기·작성 요청), 'follow_up'(개념어 없는 후속)
# - direct: 개념마다 per_concept개, 정답은 그 개념
# - compare: 선수 관계(개념 → 선수 개념) 쌍 중 compares개를 seed로 뽑음. 함께 배우는 쌍이라 "차이" 질문이 자연스러움
# - outside: 정답 [](차단). 그중 code_requests개는 코드 요청(variant='code'): 61개념 설명이 아니므로 차단 대상(spec 5-1)
# - follow_up: 직전 개념을 seed로 붙이고 정답은 그 직전 개념(후속 질문은 직전 개념을 이어 씀)
# - only: 직접 질문을 만들 개념 ID 집합(없으면 전체). 제외 개념 추가 세트(미세조정 계획 Task 4)용
# - id_prefix: 행 ID 앞부분. 추가 세트는 기존 세트와 ID가 겹치지 않게 다른 접두어를 씀
def scope_set(concept_map, write_questions, per_concept=5, compares=60, outside=60, code_requests=20, follow_ups=60,
              seed=0, only=None, id_prefix='scope') -> list[dict]:
    rng = random.Random(seed)
    rows = []

    def add(kind, question, expected, previous=(), **extra):
        rows.append({'id': f'{id_prefix}-{len(rows):04d}', 'question': question, 'previous': list(previous),
                     'expected': list(expected), 'kind': kind, **extra})

    by_id = {c.id: c for c in concept_map.concepts}
    for concept in concept_map.concepts:
        if only is not None and concept.id not in only:
            continue
        for question in _take(write_questions('direct', [concept], per_concept), per_concept):
            add('direct', question, [concept.id])
    edges = sorted((c.id, p.id) for c in concept_map.concepts for p in c.prerequisites)
    for a, b in rng.sample(edges, min(compares, len(edges))):
        add('compare', _take(write_questions('compare', [by_id[a], by_id[b]], 1), 1)[0], [a, b])
    for question in _take(write_questions('outside', [], outside - code_requests), outside - code_requests):
        add('outside', question, [])
    for question in _take(write_questions('code', [], code_requests), code_requests):
        add('outside', question, [], variant='code')
    ids = sorted(by_id)
    for question in _take(write_questions('follow_up', [], follow_ups), follow_ups):
        previous = rng.choice(ids)
        add('follow_up', question, [previous], [previous])
    return rows


## @brief 숫자 바꾸기 교란: 문장의 숫자 하나를 근거 문단에 없는 다른 값으로 바꿈; 숫자가 없으면 None
# - 문단에 이미 있는 값으로 바꾸면(예: 4 → 5인데 문단에 5도 있음) 우연히 맞는 문장이 될 수 있어 피함
def number_perturbation(sentence: str, passage: str, rng: random.Random) -> str | None:
    matches = list(_NUMBER.finditer(sentence))
    if not matches:
        return None
    match = rng.choice(matches)
    taken = set(_NUMBER.findall(passage)) | {match.group()}
    decimals = len(match.group().partition('.')[2])
    value = float(match.group())
    for candidate in (value + 1, value + 2, value * 2, value + 10):
        text = f'{candidate:.{decimals}f}'
        if text not in taken:
            return sentence[:match.start()] + text + sentence[match.end():]
    return None


# 문장 끝 서술어 → 반대 서술어(부정 교란). 맞는 문장을 확실히 틀리게 만드는 단순 규칙만 둠
_NEGATIONS = (('없습니다', '있습니다'), ('있습니다', '없습니다'), ('없다', '있다'), ('있다', '없다'),
              ('합니다', '하지 않습니다'), ('한다', '하지 않는다'), ('됩니다', '되지 않습니다'), ('된다', '되지 않는다'))


## @brief 부정 교란: 문장 끝 서술어를 반대로 바꿈('있다'↔'없다', '한다'→'하지 않는다', 'X이다'→'X가/이 아니다'); 규칙이 없으면 None
# - 'X이다'는 X의 끝 글자 받침에 맞춰 '이'·'가'를 고름('트리이다' → '트리가 아니다', '구조체이다'와 같은 받침 없는 경우도 같음)
def negate(sentence: str) -> str | None:
    body = sentence.rstrip(' .!?')
    tail = sentence[len(body):]
    for before, after in _NEGATIONS:
        if body.endswith(before):
            return body[:-len(before)] + after + tail
    for copula, negative in (('입니다', '아닙니다'), ('이다', '아니다')):
        if body.endswith(copula) and len(body) > len(copula):
            stem = body[:-len(copula)]
            last = stem[-1]
            final = '가' if '가' <= last <= '힣' and (ord(last) - 0xAC00) % 28 == 0 else '이'
            return f'{stem}{final} {negative}{tail}'
    return None


## @brief 판정 측정 세트: 맞는 문장(26B 하네스 verified)과 망가뜨린 문장을 반반으로 둔 행 목록
# - 행: {id, sentence, passage, label: 'supported'|'unsupported', perturbation: None|'number'|'negation'|'addition'|'swap_concept', source_id}
# - verified_rows를 seed로 섞어 앞 절반은 그대로(supported), 뒤 절반은 교란(unsupported)
#   - 숫자가 있는 문장은 숫자 바꾸기를 먼저 시도함(가장 확실하게 틀린 문장이 되는 방식)
#   - 나머지 방식은 행마다 차례로 돌려 고르고, 적용되지 않으면(바꿀 서술어 없음 등) 다음 방식을 시도함
#   - number·negation은 코드 규칙, addition·swap_concept은 perturb(kind, row)(실행에서는 26B)
#   - 결과가 원문과 같거나 내용이 없으면(_clean) 버림. 같은 원문 문장이 supported와 unsupported에 함께 들어가지 않음
# - 교란에 실패한 행 때문에 개수가 어긋나면 적은 쪽에 맞춰 반반을 지킴
def perturbation_set(verified_rows, perturb, seed=0) -> list[dict]:
    rng = random.Random(seed)
    rows = [{**r, 'sentence': _clean(r['sentence'])} for r in verified_rows]
    rows = [r for r in rows if r['sentence']]
    rng.shuffle(rows)
    half = len(rows) // 2
    broken, others = [], KINDS[1:]
    for n, row in enumerate(rows[half:]):
        rotated = others[n % len(others):] + others[:n % len(others)]
        for kind in (('number',) if _NUMBER.search(row['sentence']) else ()) + rotated:
            if kind == 'number':
                changed = number_perturbation(row['sentence'], row['passage'], rng)
            elif kind == 'negation':
                changed = negate(row['sentence'])
            else:
                changed = perturb(kind, row)
            changed = _clean(changed)
            if changed and changed != row['sentence']:
                broken.append((row, kind, changed))
                break
    count = min(half, len(broken))
    result = [{'sentence': r['sentence'], 'passage': r['passage'], 'label': 'supported', 'perturbation': None,
               'source_id': r['id']} for r in rows[:count]]
    result += [{'sentence': s, 'passage': r['passage'], 'label': 'unsupported', 'perturbation': kind, 'source_id': r['id']}
               for r, kind, s in broken[:count]]
    rng.shuffle(result)
    return [{'id': f'judge-{i:04d}', **row} for i, row in enumerate(result)]


## @brief 하네스 기록(HarnessLog JSONL 행)에서 verified 문장을 맞는 문장 후보로 뽑음: {id, sentence, passage, passage_id}
# - 근거 문단이 하나인 문장만 씀: 판정 세트의 한 행은 문장 하나 ↔ 문단 하나(spec 5-4)
# - passage_text(id) -> str | None: 문단 id로 원문을 돌려줌(실행에서는 색인). 원문이 없으면 건너뜀
# - 같은 문장은 한 번만 씀
def verified_rows_from_logs(records, passage_text) -> list[dict]:
    rows, seen = [], set()
    for record in records:
        keys = {p['key']: p['id'] for p in record.get('passages') or []}
        for sentence in record.get('sentences') or []:
            sources = sentence.get('sources') or []
            if sentence.get('status') != 'verified' or len(sources) != 1 or sources[0] not in keys:
                continue
            text = passage_text(keys[sources[0]])
            if text and sentence['text'] not in seen:
                seen.add(sentence['text'])
                rows.append({'id': f'v{len(rows):04d}', 'sentence': sentence['text'], 'passage': text,
                             'passage_id': keys[sources[0]]})
    return rows


## @brief 서술 채점 측정 세트: Mohler 사람 채점 행을 하네스 채점 입력 형식으로 바꿈(사람 점수·학생 답 원문 그대로)
# - 행: {id, question_id, question, reference, rubric, answer, human_scores, human_avg, human_pass, split}
# - human_pass는 eval/mohler.py의 주 기준(score_avg 4.0 이상). split은 mohler.split_questions의 문항 묶음 분할(dev·report)
# - rubrics({question_id: [항목]})에 없는 문항은 채점할 수 없으므로 뺌
def grading_set(mohler_rows, rubrics, threshold=mohler.PRIMARY_HUMAN) -> list[dict]:
    dev, _ = mohler.split_questions(mohler_rows)
    dev = set(dev)
    result = []
    for row in mohler_rows:
        question_id = mohler.question_id(row['id'])
        if question_id not in rubrics:
            continue
        result.append({'id': row['id'], 'question_id': question_id, 'question': row['question'],
                       'reference': row['instructor_answer'], 'rubric': rubrics[question_id],
                       'answer': row['student_answer'], 'human_scores': [row['score_grader_1'], row['score_grader_2']],
                       'human_avg': row['score_avg'], 'human_pass': row['score_avg'] >= threshold,
                       'split': 'dev' if question_id in dev else 'report'})
    return result


def _cell(text, limit=None):
    text = ' '.join(str(text).split()).replace('|', '\\|')
    return text if limit is None or len(text) <= limit else text[:limit] + '…'


## @brief 범위 세트 사용자 표본 시트: 개념마다 직접 질문 1개 + 다른 종류 5개씩, 확인 칸이 있는 표
def scope_sheet(rows, concept_map, out: Path, seed=0) -> None:
    rng = random.Random(seed)
    titles = {c.id: c.title for c in concept_map.concepts}
    names = lambda ids: ', '.join(f'{titles.get(i, i)}(`{i}`)' for i in ids) or '없음(차단)'  # noqa: E731
    lines = ['# 범위 결정 측정 세트 표본 (사용자 확인용)', '',
             '- 하네스의 첫 단계(질문이 61개 개념 중 무엇을 묻는지, 지도 밖인지 판정)를 잴 질문 세트입니다.',
             '- 질문은 gemma-4-26B가 "이 개념용 질문"으로 만들었고, 정답은 만든 규칙에서 정해집니다.',
             '- 확인할 것: 질문이 정말 그 개념을 묻는지(직접·비교), 지도 밖 질문이 정말 61개 개념 밖인지, 후속 질문에 개념어가 없는지.',
             '- 이상한 행은 "확인" 칸에 X와 이유를 적어 주세요.', '',
             f'- 전체: {len(rows)}개 (' + ', '.join(f'{k} {sum(r["kind"] == k for r in rows)}'
                                                   for k in ('direct', 'compare', 'outside', 'follow_up')) + ')', '',
             '## 개념별 직접 질문 (개념당 1개)', '', '| ID | 개념 | 질문 | 확인 |', '|---|---|---|---|']
    for concept in concept_map.concepts:
        candidates = [r for r in rows if r['kind'] == 'direct' and r['expected'] == [concept.id]]
        if candidates:
            row = rng.choice(candidates)
            lines.append(f'| {row["id"]} | {names(row["expected"])} | {_cell(row["question"])} |  |')
    for kind, title in (('compare', '비교 질문(선수 관계 쌍)'), ('outside', '지도 밖 질문(코드 요청 포함)'),
                        ('follow_up', '후속 질문(직전 개념을 이어 씀)')):
        lines += ['', f'## {title} 5개', '', '| ID | 직전 개념 | 질문 | 정답 | 확인 |', '|---|---|---|---|---|']
        candidates = [r for r in rows if r['kind'] == kind]
        for row in rng.sample(candidates, min(5, len(candidates))):
            label = names(row['expected']) + (' · 코드 요청' if row.get('variant') == 'code' else '')
            lines.append(f'| {row["id"]} | {names(row["previous"]) if row["previous"] else "-"} | '
                         f'{_cell(row["question"])} | {label} |  |')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('\n'.join(lines) + '\n', encoding='utf-8')


## @brief 판정 세트 사용자 표본 시트: 맞는 문장·망가뜨린 문장 반반으로 n개
# - 위에는 한눈에 보는 요약 표, 아래에는 행마다 문장과 근거 문단 원문 전체(인용 블록)를 둠
#   - 문단(최대 1200자)을 표 칸에 넣으면 읽기 어렵고, 앞부분만 보이면 문장과 대조할 수 없어 행별 블록으로 나눔
def judge_sheet(rows, out: Path, n=50, seed=0) -> None:
    rng = random.Random(seed)
    picked = []
    for label in ('supported', 'unsupported'):
        group = [r for r in rows if r['label'] == label]
        picked += rng.sample(group, min(n // 2, len(group)))
    picked.sort(key=lambda r: r['id'])
    kinds = {None: '-', 'number': '숫자 바꾸기', 'negation': '반대로 말하기', 'addition': '없는 내용 덧붙이기',
             'swap_concept': '개념 바꾸기'}
    answer = lambda row: '맞음' if row['label'] == 'supported' else '틀림'  # noqa: E731
    lines = [f'# 일치 판정 측정 세트 표본 {len(picked)}개 (사용자 확인용)', '',
             '- 하네스의 판정 단계(한국어 문장 하나가 교재 문단과 맞는지)를 잴 세트입니다.',
             '- "맞음" 문장은 26B 하네스가 판정을 통과시킨 문장이고, "틀림" 문장은 그 문장을 일부러 망가뜨린 것입니다.',
             '- 확인할 것: "맞음"이 정말 문단과 맞는지, "틀림"이 정말 문단과 어긋나는지(우연히 맞게 바뀌지 않았는지).',
             '- 아래 요약 표의 "확인" 칸에 이상한 행만 X와 이유를 적어 주세요. 근거 문단 원문은 표 아래 행별로 있습니다.', '',
             f'- 전체: {len(rows)}개 (맞음 {sum(r["label"] == "supported" for r in rows)}, '
             f'틀림 {sum(r["label"] == "unsupported" for r in rows)})', '',
             '| ID | 정답 | 망가뜨린 방식 | 문장 | 확인 |', '|---|---|---|---|---|']
    for row in picked:
        lines.append(f'| {row["id"]} | {answer(row)} | {kinds[row["perturbation"]]} | {_cell(row["sentence"])} |  |')
    for row in picked:
        label = answer(row) + (f' · {kinds[row["perturbation"]]}' if row['perturbation'] else '')
        quote = '\n>\n'.join(f'> {" ".join(p.split())}' for p in row['passage'].split('\n\n') if p.strip())
        lines += ['', f'## {row["id"]} · {label}', '', f'- 문장: {row["sentence"]}', '- 근거 문단(원문 전체):', '', quote]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')


## @brief 세트 옆에 <이름>.meta.json으로 만든 조건(모델·seed·색인·대응표 커밋 등)을 남김; 대응표가 검수로 바뀌면 같은 조건으로 다시 만들기 위함
def _write_meta(path: Path, **meta) -> None:
    from datetime import datetime

    meta['created'] = datetime.now().isoformat(timespec='seconds')
    path.with_suffix('.meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


## @brief 26B(OpenAI 호환 주소)에 요청을 한 번 보내고 응답 JSON을 돌려줌
# - 측정 세트 생성 전용. 운영 모델 잠금(BUSY)과 무관한 별도 프로세스에서 읽기 호출만 함
# - schema가 있으면 JSON schema로 묶음(질문 목록). 없으면 지시로만 JSON을 요청하고 응답의 첫 {부터 마지막 }까지를 해석함
#   - 문자열 하나를 받는 교란 요청은 schema를 걸면 26B가 {"sentence":": "}를 자주 내서 schema 없이 받음
def _model_json(client, model, system, user, schema, temperature):
    extra = ({'response_format': {'type': 'json_schema', 'json_schema': {'name': 'out', 'strict': True, 'schema': schema}}}
             if schema else {})
    response = client.chat.completions.create(
        model=model, temperature=temperature, max_tokens=2048, timeout=120,
        messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps(user, ensure_ascii=False)}],
        extra_body={'chat_template_kwargs': {'enable_thinking': False}}, **extra)
    content = response.choices[0].message.content
    return json.loads(content if schema else content[content.index('{'):content.rindex('}') + 1])


_QUESTION_TASKS = {
    'direct': '아래 CS 개념 하나를 배우는 대학생이 학습 도우미에게 물을 법한 한국어 질문을 만든다. '
              '정의, 동작 원리, 쓰임, 장단점, 예시 요청처럼 각도를 다양하게 하고, 개념 이름은 한국어·영어·줄임말을 섞어 쓴다.',
    'compare': '아래 두 CS 개념의 차이나 관계를 묻는 대학생의 한국어 질문을 만든다. 두 개념이 모두 드러나야 한다.',
    'outside': '자료구조·운영체제·데이터베이스·네트워크·컴퓨터구조의 아래 개념 목록에 없는 주제를 묻는 대학생의 한국어 질문을 만든다. '
               '절반은 일상·진로·다른 과목 질문, 절반은 목록에 없는 CS 주제(머신러닝, 웹 프레임워크 사용법, 프로그래밍 언어 문법 등)로 한다.',
    'code': '학생이 학습 도우미에게 보내는 코드 관련 요청을 한국어로 만든다. 짧은 코드 조각의 오류를 찾아 달라거나, '
            '특정 기능의 코드를 작성해 달라는 요청이다. 코드 조각을 넣는다면 5줄 이하로 한다.',
    'follow_up': '학습 도우미의 설명을 들은 학생이 이어서 하는 짧은 한국어 후속 요청을 만든다. 개념 이름이나 CS 용어를 쓰지 않는다. '
                 '예: 더 쉽게 설명해 줘, 예시 하나만 더 들어 줘.',
}


## @brief 26B로 범위 세트 질문을 만드는 write_questions 구현; 중복·문항 은행과 같은 문장·한자 섞인 질문은 버리고 다시 요청
def _model_writer(client, model, concept_map, banned):
    from serving.harness import has_han

    seen = set(banned)
    titles = [c.title for c in concept_map.concepts]

    def write(kind, concepts, n):
        result = []
        for _ in range(8):
            need = min(10, n - len(result))
            if need <= 0:
                break
            user = {'count': need, 'concepts': [{'title': c.title, 'description': c.description} for c in concepts]}
            if kind == 'outside':
                user['not_these_concepts'] = titles
            schema = {'type': 'object', 'additionalProperties': False, 'required': ['questions'],
                      'properties': {'questions': {'type': 'array', 'minItems': need, 'maxItems': need,
                                                   'items': {'type': 'string', 'maxLength': 400}}}}
            system = (_QUESTION_TASKS[kind] + ' 질문마다 표현을 다르게 하고 학생 말투로 쓴다. 한자와 중국어는 쓰지 않는다. '
                      'count개를 JSON schema에 맞게 응답한다.')
            for question in _model_json(client, model, system, user, schema, 0.9)['questions']:
                key = ' '.join(question.split())
                if key and key not in seen and not has_han(key) and len(result) < n:
                    seen.add(key)
                    result.append(key)
        return result

    return write


## @brief 26B로 덧붙이기·개념 바꾸기 교란 문장을 만드는 perturb 구현; 실패하면 None(다른 방식으로 넘어감)
def _model_perturb(client, model):
    from serving.harness import has_han

    tasks = {'addition': '문장 끝에 근거 문단에 없는 그럴듯한 사실 하나를 덧붙인 한국어 문장 하나를 만든다. 원래 내용은 바꾸지 않는다.',
             'swap_concept': '문장의 핵심 개념어 하나를 비슷하지만 다른 CS 개념어로 바꿔, 문단과 어긋나는 한국어 문장 하나를 만든다.'}

    def perturb(kind, row):
        try:
            sentence = _model_json(client, model, tasks[kind] + ' 한자와 중국어는 쓰지 않는다. {"sentence": "..."} JSON만 출력한다.',
                                   {'sentence': row['sentence'], 'passage': row['passage']}, None, 0.7)['sentence']
        except Exception as exc:  # 측정 세트 생성 보조라 한 문장 실패로 전체를 멈추지 않음
            print(f'교란 실패({kind}, {row["id"]}): {exc}', file=sys.stderr)
            return None
        return None if has_han(sentence) else sentence

    return perturb


def _client():
    from openai import OpenAI

    client = OpenAI(base_url=os.getenv('VLLM_BASE_URL', 'http://127.0.0.1:8000/v1'),
                    api_key=os.getenv('VLLM_API_KEY', 'not-needed'), max_retries=2)
    return client, os.getenv('VLLM_MODEL') or client.models.list().data[0].id


## @brief 명령 진입점: scope·harvest·perturb·grading 세트를 만들어 OUT_DIR에 쓰고, 표본 시트를 HANDOFF에 씀
def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m eval.harness_sets_judge', description='하네스 범위·판정·채점 측정 세트 생성')
    commands = parser.add_subparsers(dest='command', required=True)
    scope_cmd = commands.add_parser('scope')
    scope_cmd.add_argument('--seed', type=int, default=0)
    scope_cmd.add_argument('--only-concepts', type=Path,
                           help='제외 개념 목록(holdout.json). 주면 그 개념의 직접 질문만 만들고 기존 scope.jsonl 질문과 겹치지 않게 함')
    scope_cmd.add_argument('--per-concept', type=int, default=5)
    scope_cmd.add_argument('--out', type=Path, help='세트 파일(없으면 OUT_DIR/scope.jsonl)')
    harvest_cmd = commands.add_parser('harvest', help='26B 하네스로 범위 세트 직접 질문을 처리해 verified 문장 기록을 모음(색인 필요)')
    harvest_cmd.add_argument('--limit', type=int, default=120)
    harvest_cmd.add_argument('--seed', type=int, default=0)
    perturb_cmd = commands.add_parser('perturb')
    perturb_cmd.add_argument('--seed', type=int, default=0)
    perturb_cmd.add_argument('--map-commit', default='', help='색인을 만든 절 대응표 커밋(메타데이터 기록용)')
    for command in (harvest_cmd, perturb_cmd):
        command.add_argument('--index', type=Path, help='색인 파일(없으면 RAG_DATA_DIR/index.sqlite)')
    grading_cmd = commands.add_parser('grading')
    grading_cmd.add_argument('--data', type=Path, required=True, help='Mohler open_ended.jsonl(사람 채점 원본)')
    grading_cmd.add_argument('--rubrics', type=Path, required=True, help='rubrics.jsonl({question_id, rubric})')
    args = parser.parse_args(argv)

    from serving.concept_map import load_concept_map

    concept_map = load_concept_map()
    if args.command == 'grading':
        rows = grading_set(mohler.load_jsonl(args.data), mohler.load_rubrics(args.rubrics))
        _write_jsonl(OUT_DIR / 'grading.jsonl', rows)
        _write_meta(OUT_DIR / 'grading.jsonl', data=str(args.data), rubrics=str(args.rubrics),
                    human_threshold=mohler.PRIMARY_HUMAN, language='en(원문, 번역하지 않음)')
        print(f'{OUT_DIR / "grading.jsonl"}: {len(rows)}행 (사람 통과 {sum(r["human_pass"] for r in rows)})')
        return 0
    if args.command == 'scope':
        from serving.item_bank import load_item_bank

        client, model = _client()
        banned = {' '.join(item.question.split()) for item in load_item_bank(concept_map=concept_map)}
        out = args.out or OUT_DIR / 'scope.jsonl'
        if args.only_concepts:
            # 제외 개념 추가 세트: 직접 질문만, 기존 범위 세트 질문은 다시 쓰지 않음(같은 질문이 두 세트에 들어가지 않게)
            from finetune.split import load_holdout

            only = load_holdout(args.only_concepts)
            banned |= {' '.join(r['question'].split()) for r in mohler.load_jsonl(OUT_DIR / 'scope.jsonl')}
            rows = scope_set(concept_map, _model_writer(client, model, concept_map, banned), per_concept=args.per_concept,
                             compares=0, outside=0, code_requests=0, follow_ups=0, seed=args.seed, only=only,
                             id_prefix='holdout-scope')
            _write_jsonl(out, rows)
            _write_meta(out, model=model, seed=args.seed, only_concepts=sorted(only), per_concept=args.per_concept)
            print(f'{out}: {len(rows)}행 (개념 {len(only)}개 × {args.per_concept})')
            return 0
        rows = scope_set(concept_map, _model_writer(client, model, concept_map, banned), per_concept=args.per_concept,
                         seed=args.seed)
        _write_jsonl(out, rows)
        _write_meta(out, model=model, seed=args.seed)
        scope_sheet(rows, concept_map, HANDOFF / 'scope-sample.md', seed=args.seed)
        print(f'{out}: {len(rows)}행, 표본 시트 {HANDOFF / "scope-sample.md"}')
        return 0
    from rag.embed import Embedder
    from rag.fetch import DATA_DIR
    from rag.search import Retriever

    index = args.index or DATA_DIR / 'index.sqlite'
    if args.command == 'harvest':
        from serving.harness import answer
        from serving.harness_llm import HarnessGateway
        from serving.harness_log import HarnessLog

        direct = [r for r in mohler.load_jsonl(OUT_DIR / 'scope.jsonl') if r['kind'] == 'direct']
        picked = random.Random(args.seed).sample(direct, min(args.limit, len(direct)))
        log, retriever = HarnessLog(OUT_DIR / 'harvest-logs'), Retriever(index, Embedder.from_env())
        for n, row in enumerate(picked, 1):
            gateway = HarnessGateway(concept_map, threading.Lock())
            try:
                list(answer(row['question'], None, gateway, retriever, concept_map, log))
            except Exception as exc:  # 한 질문의 모델·검색 오류는 기록에 남기고 다음 질문으로 넘어감
                print(f'[{n}/{len(picked)}] {row["id"]} 실패: {exc}', file=sys.stderr)
            print(f'[{n}/{len(picked)}] {row["id"]}', flush=True)
        return 0
    records = [r for path in sorted((OUT_DIR / 'harvest-logs').glob('*.jsonl')) for r in mohler.load_jsonl(path)]
    retriever = Retriever(index, embedder=None)
    verified = verified_rows_from_logs(records, lambda pid: next((p.text for p in retriever.by_ids([pid])), None))
    client, model = _client()
    rows = perturbation_set(verified, _model_perturb(client, model), seed=args.seed)
    _write_jsonl(OUT_DIR / 'judge.jsonl', rows)
    _write_meta(OUT_DIR / 'judge.jsonl', model=model, seed=args.seed, index=str(index), map_commit=args.map_commit,
                harvest_records=len(records), verified_candidates=len(verified))
    judge_sheet(rows, HANDOFF / 'judge-sample.md', seed=args.seed)
    print(f'{OUT_DIR / "judge.jsonl"}: {len(rows)}행 (맞는 문장 후보 {len(verified)}), 표본 시트 {HANDOFF / "judge-sample.md"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
