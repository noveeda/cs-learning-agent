## @file
# @brief 12B 하네스 역할(judge·ground·repair·scope) 미세조정 학습 데이터 생성기 (계획 Task 3, 설계 4·5장)
# - judge·scope 정답은 만드는 방식에서 정함(구성상 정답). ground·repair 정답은 26B 하네스 실행 녹화 중 검사를 통과한 출력
# - 제외 개념(content/finetune/holdout.json)이 질문·정답 개념·근거 문단·교란 문장·출력 어디에든 나오는 행은 1차 학습 파일에 넣지 않고
#   holdout_{역할}.jsonl로 따로 모음: 배포용 2차 학습(61개념 전부, 설계 7-1)에 씀
# - 측정 세트 질문과 정규화 후 같은 질문, 측정 판정 세트와 같은 문장은 버림(문단은 학습·측정이 공유)
# - 생성 모델은 26B(운영 8000번, 읽기 호출만, 동시 요청 --workers개). 결과는 Git 밖 --out 폴더에 이어 씀
# - 측정 세트 위치는 측정 생성기와 같은 HARNESS_SETS_DIR(없으면 ai-core/outputs/harness-sets). 파일이 없으면 멈춤(거르지 않고 지나가지 않게)
#
# 실행 순서(ai-core 폴더에서, RAG_DATA_DIR은 공용 색인 위치)
# 1. 질문 만들기: python -m finetune.build_data --stage questions --out <폴더>
# 2. 하네스 실행 녹화와 ground·repair 행: python -m finetune.build_data --stage harvest --out <폴더> --limit N --workers 8
#    (중단돼도 다시 실행하면 이미 녹화한 질문은 건너뜀. --limit 0이면 녹화 없이 ground·repair 행만 다시 만듦)
#    (시작 전 26B를 확인하고, 최근 40건 중 오류가 5건이면 멈춤. 오류 기록은 남기지 않아 다음 실행에서 다시 녹화)
#    (26B 여러 대: --shard K/N과 서버마다 VLLM_BASE_URL·--out을 따로 주고, 끝나면 records를 합쳐 --limit 0으로 다시 만듦)
# 3. 판정 행: python -m finetune.build_data --stage judge --out <폴더> --workers 8
# 4. 범위 행: python -m finetune.build_data --stage scope --out <폴더>
# 5. 학습·검증 분할: python -m finetune.build_data --stage split --out <폴더>
# 6. 제외 개념 측정 세트가 생긴 뒤: python -m finetune.build_data --stage refilter --out <폴더> --measured <기존 세트들> <제외 개념 세트>
import argparse
import json
import math
import random
import re
import subprocess
import sys
import threading
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from eval.harness_sets_judge import _NUMBER, KINDS, _clean, negate, number_perturbation
from eval.harness_sets_judge import OUT_DIR as HARNESS_SETS
from finetune.capture import RecordingClient, recorded_rows
from finetune.examples import judge_row, scope_row
from finetune.split import normalize_question
from serving.harness import has_han
from serving.harness_llm import HarnessGateway
from serving.stuck import LLMUnavailable

AI_CORE = Path(__file__).resolve().parents[1]
ROLES = ('judge', 'ground', 'repair', 'scope')
# 범위 행 종류별 개수(합 3,000): 측정 범위 세트의 종류 비율(직접 63%, 비교·지도 밖·후속 각 12%)을 따름. 'outside'는 코드 요청 포함
SCOPE_QUOTAS = {'direct': 1900, 'compare': 360, 'outside': 370, 'follow_up': 370}
# 제외 개념 몫 범위 행(합 약 390 = 3,000 × 8/61): 제외 개념이 든 직접·비교·후속 질문
HOLDOUT_SCOPE_QUOTAS = {'direct': 250, 'compare': 80, 'follow_up': 60}
# 판정 행 목표 수와, 교란 실패로 줄어드는 몫을 메우려고 교란 대상을 더 잡는 비율
JUDGE_TARGET = 6000
HOLDOUT_JUDGE_TARGET = round(JUDGE_TARGET * 8 / 61)
JUDGE_SLACK = 1.15
# 개념 제목에서 나오지 않는 제외 개념의 다른 이름. 제외 개념 목록에 없는 ID는 쓰이지 않음
EXTRA_TERMS = {
    'linked-list': ('링크드 리스트', 'linked list'),
    'bfs': ('너비 우선', '넓이 우선', 'breadth-first'),
    'btree-index': ('B-트리', 'B트리', 'B+트리', 'B-tree', 'B+tree'),
    'normalization': ('정규형',),
    'tcp-reliability': ('핸드셰이크', 'handshake'),
    'pipelining-hazards': ('파이프라인',),
}
# 학습 질문 길이 상한(측정 검색 세트와 같음)과, 같은 글자가 이만큼 이어지면 깨진 생성으로 보는 기준(실측: 'ㅜ' 수백 개)
QUESTION_MAX_CHARS = 200
_REPEATED = re.compile(r'(.)\1{9,}')
# 개념 제목을 이름 조각으로 나누는 기준: 괄호·가운뎃점·쉼표, 그리고 '캐시와 지역성'처럼 한글 뒤의 '과'·'와'
_TITLE_PARTS = re.compile(r'\s*[()·,]\s*|(?<=[가-힣])[과와]\s+')


## @brief 제외 개념을 가리키는 이름 조각 목록(정규화형): 제목 조각, 개념 ID(하이픈을 공백으로), EXTRA_TERMS
# - 질문·문장 글자를 이 조각과 대조해 제외 개념이 학습 예시에 새는 것을 막음. 두 글자 미만 조각은 오탐이 커서 뺌
def holdout_terms(concept_map, holdout: set[str]) -> list[str]:
    terms = set()
    for concept in concept_map.concepts:
        if concept.id in holdout:
            names = [*_TITLE_PARTS.split(concept.title), concept.id.replace('-', ' '), *EXTRA_TERMS.get(concept.id, ())]
            terms |= {normalize_question(name) for name in names}
    return sorted(t for t in terms if len(t) >= 2)


## @brief 글에 제외 개념 이름 조각이 있으면 True(공백·문장부호를 지운 소문자로 대조)
# - 4자 이하 영문 조각('osi', 'bfs')은 원문에서 단어 경계로 찾음: 공백을 지운 글에서는 'position' 안의 'osi'까지 걸리기 때문
def mentions_holdout(text: str, terms: list[str]) -> bool:
    normalized, lowered = normalize_question(text), text.lower()
    return any(re.search(rf'(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])', lowered)
               if term.isascii() and len(term) <= 4 else term in normalized for term in terms)


## @brief 학습 행 하나를 버릴 이유: 'measured'(측정 질문과 같은 질문) → 'han'(한자 섞임) 순으로 보고, 없으면 None
# - question: 행의 학생 질문(없으면 None). texts: 출력·교란 문장처럼 함께 볼 글
# - 1차·제외 개념 몫 모두 버림: 측정 오염과 한자 출력은 어느 학습에도 넣지 않음
def drop_reason(question, texts, measured) -> str | None:
    if question is not None and normalize_question(question) in measured:
        return 'measured'
    if any(has_han(t) for t in (question, *texts) if t):
        return 'han'
    return None


## @brief 행이 제외 개념에 닿으면 True: 가리키는 개념 ID가 제외 개념이거나, 질문·글에 제외 개념 이름이 있음
# - 이런 행은 버리지 않고 제외 개념 몫(holdout_{역할}.jsonl)으로 옮김
def touches_holdout(question, texts, concepts, holdout, terms) -> bool:
    return bool(set(concepts) & holdout) or any(mentions_holdout(t, terms) for t in (question, *texts) if t)


## @brief quotas({종류: 개수})대로 종류마다 seed로 골라, 원래 순서를 유지해 반환
def _pick(rows, quotas, rng):
    order = {id(r): n for n, r in enumerate(rows)}
    picked = []
    for kind, n in quotas.items():
        same = [r for r in rows if r['meta']['kind'] == kind]
        picked += rng.sample(same, min(n, len(same)))
    return sorted(picked, key=lambda r: order[id(r)])


## @brief 범위 결정 학습 행: 질문 목록(questions.jsonl 행)을 구성상 정답 행으로 바꿔 (1차 행, 제외 개념 몫 행, 버린 이유별 개수)를 반환
# - 정답: direct는 그 개념, compare는 두 개념, outside(코드 요청 포함)는 ["none"], follow_up은 직전 개념 + follow_up true
# - 비교 질문의 한쪽이나 후속 질문의 직전 개념이 제외 개념이면 1차 행에 넣지 않음(Review Focus 5)
# - quotas·holdout_quotas가 있으면 종류마다 seed로 그 수만큼 고름
def scope_rows(questions, concept_map, holdout, terms, measured, quotas=None, holdout_quotas=None, seed=0):
    rows, held, reasons = [], [], Counter()
    for q in questions:
        reason = drop_reason(q['question'], [], measured)
        if reason:
            reasons[reason] += 1
            continue
        row = {**scope_row(q['question'], q['previous'], q['expected'], q['kind'] == 'follow_up', concept_map),
               'meta': {'question_id': q['id'], 'kind': q['kind'], 'question': q['question']}}
        (held if touches_holdout(q['question'], [], [*q['expected'], *q['previous']], holdout, terms) else rows).append(row)
    rng = random.Random(seed)
    return (_pick(rows, quotas, rng) if quotas else rows), (_pick(held, holdout_quotas, rng) if holdout_quotas else held), reasons


## @brief 녹화 한 건을 행으로 쓸 때의 (버릴 이유, 제외 개념에 닿는지)
# - keys: 이 행이 쓰는 근거 키. 그 문단의 절이 제외 개념과 대응돼 있으면(trainable에 없으면) 제외 개념에 닿는 것으로 봄
def _record_check(rec, keys, holdout, terms, measured, trainable):
    touches = (touches_holdout(rec['question'], [], rec['record'].get('concepts') or [], holdout, terms)
               or any((rec['passages'][k]['book'], rec['passages'][k]['section_id']) not in trainable for k in keys))
    return ('measured' if normalize_question(rec['question']) in measured else None), touches


## @brief 판정 학습의 맞는 문장 후보: 녹화의 verified 문장(근거 문단 하나)과 그 문장을 통과시킨 26B 근거 구절
# - 반환: (1차 후보, 제외 개념 몫 후보, 버린 이유별 개수). 후보 행은 {id, sentence, passage, evidence, question}
# - evidence는 마지막 판정(수정 뒤 재판정 포함)의 근거 구절
# - judged: 측정 판정 세트 문장의 정규화 집합. 같은 문장은 'measured'로 버림
# - 같은 문장은 한 번만 씀('duplicate')
def verified_pool(records, holdout, terms, measured, trainable, judged):
    pool, held, reasons, seen = [], [], Counter(), set()
    for rec in records:
        for n, entry in enumerate(rec['record'].get('sentences') or []):
            if entry.get('status') != 'verified' or len(entry.get('sources') or []) != 1:
                continue
            text = entry['text']
            dropped, touches = _record_check(rec, entry['sources'], holdout, terms, measured, trainable)
            reason = (dropped or ('measured' if normalize_question(text) in judged else None)
                      or drop_reason(None, [text], measured) or ('duplicate' if text in seen else None))
            if reason:
                reasons[reason] += 1
                continue
            seen.add(text)
            (held if touches or touches_holdout(None, [text], [], holdout, terms) else pool).append(
                {'id': f"{rec['id']}-{n}", 'sentence': text, 'passage': rec['passages'][entry['sources'][0]]['text'],
                 'evidence': entry['judgments'][-1]['evidence'], 'question': rec['question']})
    return pool, held, reasons


## @brief 판정 학습 행: 맞는 문장 후보를 반반으로 나눠 앞 절반은 그대로, 뒤 절반은 교란해 정답(verdict·evidence)을 붙임
# - 정답 규칙: 그대로는 supported, number·negation·swap_concept 교란은 unsupported, addition(덧붙이기)은 partial
#   - evidence는 모두 원래 문장의 26B 근거 구절. 교란 문장도 같은 문단 구절을 보고 판정하게 하기 위함
# - 틀림 행 중 addition은 addition_share(기본 40%): 교란 대상의 앞 몫은 덧붙이기를, 나머지는 측정 세트와 같은 순서
#   (숫자가 있으면 숫자 바꾸기, 이어 반대로 말하기·개념 바꾸기를 차례로)를 시도하고, 끝에 비율에 맞게 개수를 맞춤
# - perturb(kind, row) -> str | None: addition·swap_concept 교란(실행에서는 26B). 실패하면 None
# - 교란은 workers개 동시에 부르되 행마다 seed에서 나온 난수를 따로 써서, 같은 입력이면 같은 결과가 나옴
# - limit: 전체 행 수 상한(맞음·틀림 합). 교란 대상을 limit/2의 JUDGE_SLACK배까지만 잡아 26B 호출을 아낌
def training_judge_rows(verified, perturb, seed=0, addition_share=0.4, workers=1, limit=None) -> list[dict]:
    rng = random.Random(seed)
    rows = [{**v, 'sentence': _clean(v['sentence'])} for v in verified]
    rows = [r for r in rows if r['sentence']]
    rng.shuffle(rows)
    half = len(rows) // 2
    sources = rows[half:][:math.ceil(limit / 2 * JUDGE_SLACK)] if limit else rows[half:]
    n_add = math.ceil(addition_share * len(sources))
    others = tuple(k for k in KINDS if k not in ('number', 'addition'))

    def broken(item):
        n, row = item
        row_rng = random.Random(f"{seed}:{row['id']}")
        rotated = others[n % len(others):] + others[:n % len(others)]
        order = (('addition',) if n < n_add else ()) + (('number',) if _NUMBER.search(row['sentence']) else ()) + rotated
        for kind in order:
            if kind == 'number':
                changed = number_perturbation(row['sentence'], row['passage'], row_rng)
            elif kind == 'negation':
                changed = negate(row['sentence'])
            else:
                changed = perturb(kind, row)
            changed = _clean(changed)
            if changed and changed != row['sentence']:
                return row, kind, changed
        return None

    with ThreadPoolExecutor(max(1, workers)) as pool:
        results = [r for r in pool.map(broken, enumerate(sources)) if r]
    additions = [r for r in results if r[1] == 'addition']
    rest = [r for r in results if r[1] != 'addition']
    count = min(half, math.floor(len(additions) / addition_share), math.floor(len(rest) / (1 - addition_share)))
    if limit:
        count = min(count, limit // 2)
    n_additions = round(addition_share * count)
    picked = additions[:n_additions] + rest[:count - n_additions]

    def row_of(row, kind, sentence):
        verdict = {None: 'supported', 'addition': 'partial'}.get(kind, 'unsupported')
        return {**judge_row(sentence, row['passage'], verdict, row['evidence']),
                'meta': {'source_id': row['id'], 'perturbation': kind, 'question': row.get('question')}}

    result = [row_of(r, None, r['sentence']) for r in rows[:count]] + [row_of(*b) for b in picked]
    rng.shuffle(result)
    return result


## @brief 녹화 응답을 그대로 돌려주는 가짜 OpenAI 클라이언트; 운영 게이트웨이 parse를 다시 돌리는 데 씀
class _Replay:
    def __init__(self, content):
        response = SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=content))])
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **_: response))
        self.models = SimpleNamespace(list=lambda timeout: SimpleNamespace(data=[SimpleNamespace(id='replay')]))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


## @brief 녹화 응답이 운영 HarnessGateway의 parse(역할별 검증 규칙)를 통과하면 True
# - recorded_rows의 keep에 씀: recorded_rows는 JSON 해석까지만 거르므로 근거 키·한자·개념 ID·판정 값 검사를 여기서 다시 함
# - 검사 규칙을 따로 옮겨 적지 않고 운영 게이트웨이 메서드에 녹화 응답을 다시 넣어 돌림: 운영 규칙이 바뀌어도 어긋나지 않게 하기 위함
def passes_gateway(record, concept_map) -> bool:
    if record.get('role') not in ROLES:
        return False
    payload = json.loads(record['messages'][1]['content'])
    gw = HarnessGateway(concept_map, threading.Lock(), client_factory=lambda: _Replay(record['content']))
    calls = {
        'scope': lambda: gw.scope(payload['question'], [p['id'] for p in payload['previous']], concept_map),
        'ground': lambda: gw.ground(payload['question'], {s['key']: s['text'] for s in payload['sources']},
                                    payload['concepts'], payload['prior']),
        'judge': lambda: gw.judge(payload['sentence'], payload['source']),
        'repair': lambda: gw.repair(payload['sentence'], payload['source'], payload['source_key']),
    }
    try:
        calls[record['role']]()
    except LLMUnavailable:
        return False
    return True


## @brief 근거 기반 생성 학습 행: 답의 모든 문장이 수정 없이 verified이고 26B 별도 검사(audits)도 모두 통과한 답의 ground 호출
# - 반환: (1차 행, 제외 개념 몫 행, 버린 이유별 개수)
# - 수정을 거친 문장이 있는 답은 뺌: ground 출력 자체에는 틀린 초안이 들어 있었기 때문
# - 녹화 출력의 문장이 하네스가 받아들인 초안과 글자 그대로 같아야 함(본문 근거 키 표기를 지운 출력 등은 'gateway')
def ground_rows(records, concept_map, holdout, terms, measured, trainable):
    rows, held, reasons = [], [], Counter()
    for rec in records:
        harness, audits = rec['record'], rec.get('audits') or []
        sentences = harness.get('sentences') or []
        reason, touches = _record_check(rec, list(rec['passages']), holdout, terms, measured, trainable)
        if not reason and harness.get('outcome') != 'answered':
            reason = 'not_answered'
        if not reason and any(s.get('status') != 'verified' or s.get('repairs') for s in sentences):
            reason = 'not_all_verified'
        if not reason and (len(audits) != len(sentences) or not all(a is True for a in audits)):
            reason = 'audit'
        found = [] if reason else recorded_rows([c for c in rec['calls'] if c['role'] == 'ground'],
                                                keep=lambda c: passes_gateway(c, concept_map))
        if not reason and (len(found) != 1 or [s['text'] for s in json.loads(found[0]['messages'][2]['content'])['sentences']]
                           != [s['draft'] for s in sentences]):
            reason = 'gateway'
        texts = [] if reason else [s['text'] for s in json.loads(found[0]['messages'][2]['content'])['sentences']]
        reason = reason or drop_reason(rec['question'], texts, measured)
        if reason:
            reasons[reason] += 1
            continue
        row = {**found[0], 'meta': {'question_id': rec['id'], 'kind': rec['kind'], 'question': rec['question']}}
        (held if touches or touches_holdout(None, texts, [], holdout, terms) else rows).append(row)
    return rows, held, reasons


## @brief 틀린 문장 수정 학습 행: 수정 뒤 재판정이 supported(근거 구절 대조 포함)로 끝난 문장의 repair 호출
# - 반환: (1차 행, 제외 개념 몫 행, 버린 이유별 개수)
# - 문장마다 따짐: 녹화에서 그 문장의 초안·근거 키로 부른 repair 중 운영 parse를 통과하고 최종 문장을 낸 응답 하나
def repair_rows(records, concept_map, holdout, terms, measured, trainable):
    rows, held, reasons = [], [], Counter()
    for rec in records:
        for entry in rec['record'].get('sentences') or []:
            if not entry.get('repairs'):
                continue
            key = entry['sources'][0]
            reason, touches = _record_check(rec, [key], holdout, terms, measured, trainable)
            reason = reason or ('rejudge_failed' if entry.get('status') != 'verified' else None)

            def keep(c, entry=entry, key=key):
                payload = json.loads(c['messages'][1]['content'])
                return (payload['sentence'] == entry['draft'] and payload['source_key'] == key
                        and json.loads(c['content'])['text'] == entry['text'] and passes_gateway(c, concept_map))

            found = [] if reason else recorded_rows([c for c in rec['calls'] if c['role'] == 'repair'], keep)
            if not reason and len(found) != 1:
                reason = 'gateway'
            reason = reason or drop_reason(None, [entry['draft'], entry['text']], measured)
            if reason:
                reasons[reason] += 1
                continue
            row = {**found[0], 'meta': {'question_id': rec['id'], 'kind': rec['kind'], 'question': rec['question']}}
            (held if touches or touches_holdout(None, [entry['draft'], entry['text']], [], holdout, terms) else rows).append(row)
    return rows, held, reasons


## @brief 측정 질문과 같은 질문에서 나온 행을 뺌; 제외 개념 몫 파일을 나중에 생긴 측정 세트(제외 개념 추가 세트)로 다시 거를 때 씀
# - 행의 질문은 meta.question(판정 행은 맞는 문장이 나온 하네스 질문)
def refilter_rows(rows, measured):
    return [r for r in rows if normalize_question(r['meta'].get('question') or '') not in measured]


## @brief 역할마다 valid_share(5%)를 seed 고정으로 검증용에 떼어 (학습, 검증)을 반환
# - 역할별로 따로 떼어, 적은 역할(repair)도 검증 손실에 반영되게 함
def split_rows(rows, seed=0, valid_share=0.05):
    train, valid = [], []
    for role in sorted({r['role'] for r in rows}):
        same = [r for r in rows if r['role'] == role]
        order = list(range(len(same)))
        random.Random(f'{seed}:{role}').shuffle(order)
        chosen = set(order[:round(len(same) * valid_share)])
        valid += [r for n, r in enumerate(same) if n in chosen]
        train += [r for n, r in enumerate(same) if n not in chosen]
    return train, valid


# ---- 실행(26B 호출·파일 입출력). 위 함수들은 테스트가, 아래는 실제 생성이 씀 ----

def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def _write_jsonl(path: Path, rows) -> None:
    path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')


## @brief jsonl 끝에 행 하나를 붙이는 함수; 여러 작업 스레드가 함께 써도 줄이 섞이지 않게 잠금으로 묶음
def _appender(path: Path):
    lock = threading.Lock()

    def append(row):
        with lock, path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')

    return append


## @brief meta.json에 단계 결과를 덧붙여 씀(만든 조건·행 수·거른 이유)
def _update_meta(out: Path, **values) -> None:
    path = out / 'meta.json'
    meta = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    meta.update(values)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


## @brief 26B 클라이언트와 모델 정보(id, vLLM이 알려 주는 가중치 경로 root)
def _client():
    from eval.harness_sets_judge import _client as judge_client

    client, model = judge_client()
    info = next((m for m in client.models.list().data if m.id == model), None)
    return client, model, {'id': model, 'root': getattr(info, 'root', None)}


## @brief 절 대응표를 마지막으로 바꾼 커밋(색인을 만든 기준); git이 없으면 빈 문자열
def _map_commit() -> str:
    from rag.section_map import MAP_PATH

    try:
        return subprocess.run(['git', '-C', str(MAP_PATH.parent), 'log', '-1', '--format=%h', '--', MAP_PATH.name],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ''


## @brief 질문 단계: 61개념마다 직접 질문, 선수 관계 쌍마다 비교 질문, 지도 밖·코드·후속 질문을 26B로 만듦
# - 측정 세트와 같은 생성기(eval.harness_sets_judge._model_writer)를 씀. 이미 있는 질문 수를 세어 모자란 만큼만 더 만듦(이어 실행)
# - 제외 개념 질문도 같은 비율로 만듦: 행을 만들 때 제외 개념 몫(holdout_*)으로 갈라 2차 학습에 씀
# - 측정 질문(정규화 후 같은 것)은 버림. 후속 질문의 직전 개념은 61개념 중에서 고름
def _questions_stage(args, concept_map, measured_raw, measured):
    from eval.harness_sets_judge import _model_writer
    from serving.item_bank import load_item_bank

    path = args.out / 'questions.jsonl'
    existing = _read_jsonl(path)
    have = Counter((q['kind'], q.get('variant'), tuple(q['expected']) if q['kind'] in ('direct', 'compare') else ())
                   for q in existing)
    by_id = {c.id: c for c in concept_map.concepts}
    ids = sorted(by_id)
    edges = sorted({(c.id, p.id) for c in concept_map.concepts for p in c.prerequisites})
    tasks = [('direct', None, [c], args.per_concept - have[('direct', None, (c,))]) for c in ids]
    tasks += [('compare', None, [a, b], args.per_edge - have[('compare', None, (a, b))]) for a, b in edges]
    for kind, variant, total in (('outside', None, args.outside), ('outside', 'code', args.code), ('follow_up', None, args.follow_ups)):
        need = total - have[(kind, variant, ())]
        tasks += [(kind, variant, [], min(40, need - start)) for start in range(0, max(need, 0), 40)]
    tasks = [t for t in tasks if t[3] > 0]
    client, model, info = _client()
    banned = measured_raw | {' '.join(q['question'].split()) for q in existing}
    banned |= {' '.join(item.question.split()) for item in load_item_bank(concept_map=concept_map)}
    write = _model_writer(client, model, concept_map, banned)
    append, lock, counter = _appender(path), threading.Lock(), [len(existing)]

    def run(task):
        kind, variant, task_ids, need = task
        made = 0
        for _ in range(4):
            try:
                questions = write('code' if variant == 'code' else kind, [by_id[i] for i in task_ids], need - made)
            except Exception as exc:  # 한 묶음의 생성 실패로 전체를 멈추지 않음(다시 실행하면 모자란 만큼 다시 만듦)
                print(f'질문 생성 실패({kind}, {task_ids}): {exc}', file=sys.stderr)
                return
            for question in questions:
                if (normalize_question(question) in measured or len(question) > QUESTION_MAX_CHARS
                        or _REPEATED.search(question)):
                    continue
                with lock:
                    qid = f'q{counter[0]:05d}'
                    counter[0] += 1
                previous = [random.Random(f'{args.seed}:{qid}').choice(ids)] if kind == 'follow_up' else []
                expected = previous if kind == 'follow_up' else task_ids
                append({'id': qid, 'kind': kind, 'question': question, 'previous': previous, 'expected': expected,
                        **({'variant': variant} if variant else {})})
                made += 1
            if made >= need or not questions:
                return

    with ThreadPoolExecutor(args.workers) as pool:
        for n, _ in enumerate(pool.map(run, tasks), 1):
            if n % 20 == 0 or n == len(tasks):
                print(f'[질문 {n}/{len(tasks)}]', flush=True)
    questions = _read_jsonl(path)
    _update_meta(args.out, model=info, questions=dict(Counter(q['kind'] for q in questions)))
    print(f'{path}: {len(questions)}개 {dict(Counter(q["kind"] for q in questions))}')


## @brief 하네스 기록을 받아 두는 로그(파일에 쓰지 않음); answer()의 log 인자 자리에 씀
class _Capture:
    record = None

    def write(self, record):
        self.record = record


## @brief 질문 하나를 26B 하네스로 처리하며 호출을 녹화하고, 생성 학습 후보면 문장마다 26B 별도 검사를 붙여 records.jsonl 행을 만듦
# - 게이트웨이마다 새 잠금을 씀(운영 BUSY와 무관): 이 프로세스의 동시 요청 수는 작업 스레드 수(--workers)로 정함
# - 별도 검사는 측정 도구의 노출 지표 검사와 같은 호출(serving.harness._judged, 판정 + 근거 구절 대조)
# - 녹화는 학습 행이 될 ground·repair 호출만 남김: judge·scope 학습 행은 구성으로 만들고, 판정 결과는 하네스 기록에 있음
def _harvest_one(question, concept_map, retriever):
    from serving.harness import _judged, answer
    from serving.stuck_llm import _default_client

    sink, capture = [], _Capture()
    gateway = HarnessGateway(concept_map, threading.Lock(), client_factory=lambda: RecordingClient(_default_client(), sink))
    try:
        list(answer(question['question'], None, gateway, retriever, concept_map, capture))
    except Exception as exc:  # 기록은 answer()가 finally에서 남김(outcome='error')
        print(f"{question['id']} 실패: {exc}", file=sys.stderr)
    record = capture.record or {'outcome': 'error', 'sentences': []}
    found = {p.id: p for p in retriever.by_ids([p['id'] for p in record.get('passages', [])])}
    passages = {p['key']: {'id': p['id'], 'book': found[p['id']].book, 'section_id': found[p['id']].section_id,
                           'text': found[p['id']].text} for p in record.get('passages', []) if p['id'] in found}
    sentences = record.get('sentences', [])
    audits = None
    if (record['outcome'] == 'answered' and sentences
            and all(s.get('status') == 'verified' and not s.get('repairs') for s in sentences)):
        checker = HarnessGateway(concept_map, threading.Lock())
        texts = {k: SimpleNamespace(text=p['text']) for k, p in passages.items()}
        audits = [_judged(checker, s['text'], s['sources'], texts, []) for s in sentences]
    return {**{k: question[k] for k in ('id', 'kind', 'question', 'expected')}, 'record': record, 'passages': passages,
            'calls': [c for c in sink if c['role'] in ('ground', 'repair')],
            'call_roles': dict(Counter(c['role'] for c in sink)), 'audits': audits}


## @brief 녹화 대상을 'K/N' 분할로 나눔: seed 순서 인덱스 i가 i % N == K인 것만 남김(기본 '0/1'은 전체)
# - 26B 서버 여러 대가 같은 질문을 두 번 녹화하거나 빠뜨리지 않게, 섞은 뒤 순서로만 나눔
def shard_of(items, shard='0/1'):
    k, n = (int(x) for x in shard.split('/'))
    return [item for i, item in enumerate(items) if i % n == k]


## @brief 녹화를 workers개 동시에 돌려 결과를 write로 넘기고, 최근 window건 중 오류가 max_errors건 이상이면 남은 질문을 건너뛰고 멈춤
# - 반환: ({'ok': 쓴 건수, 'error': 오류 건수}, 멈췄는지)
# - 오류(outcome='error') 기록은 쓰지 않음: 서버가 내려간 동안의 실패가 '녹화 끝'으로 남으면 다시 실행해도 그 질문을 건너뜀
# - 26B가 재시작되면 질문마다 곧바로 실패해 수천 건이 몇 분 만에 오류로 끝나므로, 최근 오류 비율로 끊음
#   기준은 오류 10% 초과(최근 40건 중 5건). 정상 녹화 1,300건에서는 오류가 0건이었음
def run_guarded(todo, work, write, workers, window=40, max_errors=5):
    recent, counts, lock, stop = deque(maxlen=window), Counter(), threading.Lock(), threading.Event()

    def run(item):
        if stop.is_set():
            return
        result = work(item)
        error = result['record']['outcome'] == 'error'
        with lock:
            recent.append(error)
            counts['error' if error else 'ok'] += 1
            if sum(recent) >= max_errors:
                stop.set()
        if not error:
            write(result)

    with ThreadPoolExecutor(workers) as pool:
        for n, _ in enumerate(pool.map(run, todo), 1):
            if n % 25 == 0 or n == len(todo):
                print(f'[녹화 {n}/{len(todo)}] 오류 {counts["error"]}', flush=True)
    return dict(counts), stop.is_set()


## @brief 하네스 단계: 직접·비교 질문을 seed 순서로 --limit개까지 녹화(이미 녹화한 질문은 건너뜀)하고, 전체 녹화로 ground·repair 행을 다시 만듦
# - 질문 순서를 섞어 두어, 일부만 녹화해도 61개념(제외 개념 포함)이 고르게 섞임
def _harvest_stage(args, concept_map, holdout, terms, measured, trainable):
    from serving.harness_chat import build_retriever

    path = args.out / 'records.jsonl'
    done = {r['id'] for r in _read_jsonl(path)}
    candidates = [q for q in _read_jsonl(args.out / 'questions.jsonl') if q['kind'] in ('direct', 'compare')]
    random.Random(args.seed).shuffle(candidates)
    pending = [q for q in shard_of(candidates, args.shard) if q['id'] not in done]
    todo = pending[:args.limit]
    print(f'남은 질문 {len(pending)}개 중 {len(todo)}개 녹화({args.shard})', flush=True)
    if todo:
        try:  # 시작 전 서버 확인: 내려간 서버에 질문을 쏟아 오류만 쌓지 않게 함
            _client()
        except Exception as exc:
            sys.exit(f'26B 서버에 연결하지 못해 녹화를 시작하지 않습니다: {exc}')
    retriever, append = build_retriever(), _appender(path)
    counts, stopped = run_guarded(todo, lambda q: _harvest_one(q, concept_map, retriever), append, args.workers)
    if stopped:
        sys.exit(f'최근 녹화에 오류가 몰려 멈췄습니다({counts}). 서버를 확인한 뒤 다시 실행하면 오류 난 질문부터 이어 녹화합니다.')
    records = _read_jsonl(path)
    result = {}
    for role, build in (('ground', ground_rows), ('repair', repair_rows)):
        rows, held, reasons = build(records, concept_map, holdout, terms, measured, trainable)
        _write_jsonl(args.out / f'{role}.jsonl', rows)
        _write_jsonl(args.out / f'holdout_{role}.jsonl', held)
        result[role] = {'rows': len(rows), 'holdout_rows': len(held), 'dropped': dict(reasons)}
        print(f'{role}: {len(rows)}행, 제외 개념 몫 {len(held)}행, 거름 {dict(reasons)}')
    outcomes = Counter(r['record']['outcome'] for r in records)
    _update_meta(args.out, harvest={'records': len(records), 'outcomes': dict(outcomes),
                                    'calls': dict(sum((Counter(r['call_roles']) for r in records), Counter()))}, **result)


## @brief 판정 단계: 녹화의 verified 문장으로 1차 판정 행과 제외 개념 몫 판정 행을 따로 만듦
# - 26B 교란 결과는 perturb-cache.jsonl에 남겨 다시 실행하면 재사용함
# - 1차 행의 교란 문장에 제외 개념 이름이 들어가면 실패로 보고 다음 방식으로 넘어감(제외 개념 몫은 상관없음)
def _judge_stage(args, concept_map, holdout, terms, measured, trainable):
    from eval.harness_sets_judge import _model_perturb

    judged = {normalize_question(r['sentence']) for r in _read_jsonl(args.judged_set)}
    pool, held, reasons = verified_pool(_read_jsonl(args.out / 'records.jsonl'), holdout, terms, measured, trainable, judged)
    cache_path = args.out / 'perturb-cache.jsonl'
    cache = {(c['kind'], c['sentence']): c['result'] for c in _read_jsonl(cache_path)}
    client, model, info = _client()
    model_perturb, append = _model_perturb(client, model), _appender(cache_path)

    # 한자는 _model_perturb가 이미 거름
    def cached(kind, row):
        key = (kind, row['sentence'])
        if key not in cache:
            result = model_perturb(kind, row)
            cache[key] = result
            append({'kind': kind, 'sentence': row['sentence'], 'result': result})
        return cache[key]

    def primary(kind, row):
        result = cached(kind, row)
        return None if result and mentions_holdout(result, terms) else result

    summary = {'pool': len(pool), 'holdout_pool': len(held), 'dropped': dict(reasons), 'perturb_model': info,
               'judged_set': str(args.judged_set)}
    for name, candidates, perturb, target in (('judge', pool, primary, args.judge_target),
                                              ('holdout_judge', held, cached, HOLDOUT_JUDGE_TARGET)):
        rows = training_judge_rows(candidates, perturb, seed=args.seed, workers=args.workers, limit=target)
        _write_jsonl(args.out / f'{name}.jsonl', rows)
        kinds = Counter(r['meta']['perturbation'] or 'supported' for r in rows)
        summary[name] = {'rows': len(rows), 'kinds': dict(kinds)}
        print(f'{name}: {len(rows)}행 (후보 {len(candidates)}), {dict(kinds)}')
    _update_meta(args.out, judge=summary)
    print(f'거름 {dict(reasons)}')


## @brief 명령 진입점: --stage에 따라 한 단계를 실행하고 결과와 조건을 --out 폴더(meta.json 포함)에 씀
def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m finetune.build_data', description='하네스 역할 미세조정 학습 데이터 생성')
    parser.add_argument('--stage', choices=('questions', 'harvest', 'judge', 'scope', 'split', 'refilter'), required=True)
    parser.add_argument('--out', type=Path, default=AI_CORE / 'outputs' / 'finetune')
    parser.add_argument('--workers', type=int, default=8, help='26B 동시 요청 수(상한 12: 26B가 max-num-seqs 8로 재시작된 뒤 검색·임베딩 대기분까지 채우도록 허락)')
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--shard', default='0/1', help="harvest: 'K/N'이면 seed 순서 인덱스 i %% N == K인 질문만 녹화(26B 여러 대 분할)")
    parser.add_argument('--measured', type=Path, nargs='*',
                        default=[HARNESS_SETS / 'scope.jsonl', HARNESS_SETS / 'retrieval.jsonl'],
                        help='측정 세트 jsonl(question 필드). 제외 개념 추가 측정 세트가 생기면 함께 넘김')
    parser.add_argument('--judged-set', type=Path, default=HARNESS_SETS / 'judge.jsonl', help='측정 판정 세트(같은 문장을 버림)')
    parser.add_argument('--limit', type=int, default=500, help='harvest: 이번 실행에서 새로 녹화할 질문 수')
    parser.add_argument('--per-concept', type=int, default=110, help='questions: 학습 개념마다 직접 질문 수')
    parser.add_argument('--per-edge', type=int, default=6, help='questions: 선수 관계 쌍마다 비교 질문 수')
    parser.add_argument('--outside', type=int, default=250)
    parser.add_argument('--code', type=int, default=120)
    parser.add_argument('--follow-ups', type=int, default=430, help='questions: 후속 질문 수(약 13%%는 직전 개념이 제외 개념)')
    parser.add_argument('--judge-target', type=int, default=JUDGE_TARGET)
    args = parser.parse_args(argv)
    if args.workers > 12:
        parser.error('26B 동시 요청은 12까지입니다.')

    from rag.section_map import MAP_PATH
    from finetune.split import load_holdout, measured_questions, trainable_passages
    from serving.concept_map import load_concept_map

    args.out.mkdir(parents=True, exist_ok=True)
    concept_map, holdout = load_concept_map(), load_holdout()
    terms = holdout_terms(concept_map, holdout)
    measured = measured_questions(args.measured)
    measured_raw = {' '.join(r['question'].split()) for p in args.measured for r in _read_jsonl(p)}
    trainable = trainable_passages(json.loads(MAP_PATH.read_text(encoding='utf-8')), holdout)
    _update_meta(args.out, seed=args.seed, holdout=sorted(holdout), holdout_terms=terms, map_commit=_map_commit(),
                 measured_sets=[str(p) for p in args.measured])
    if args.stage == 'questions':
        _questions_stage(args, concept_map, measured_raw, measured)
    elif args.stage == 'harvest':
        _harvest_stage(args, concept_map, holdout, terms, measured, trainable)
    elif args.stage == 'judge':
        _judge_stage(args, concept_map, holdout, terms, measured, trainable)
    elif args.stage == 'scope':
        rows, held, reasons = scope_rows(_read_jsonl(args.out / 'questions.jsonl'), concept_map, holdout, terms, measured,
                                         SCOPE_QUOTAS, HOLDOUT_SCOPE_QUOTAS, args.seed)
        _write_jsonl(args.out / 'scope.jsonl', rows)
        _write_jsonl(args.out / 'holdout_scope.jsonl', held)
        kinds, held_kinds = Counter(r['meta']['kind'] for r in rows), Counter(r['meta']['kind'] for r in held)
        _update_meta(args.out, scope={'rows': len(rows), 'kinds': dict(kinds), 'holdout_rows': len(held),
                                      'holdout_kinds': dict(held_kinds), 'dropped': dict(reasons)})
        print(f'scope: {len(rows)}행 {dict(kinds)}, 제외 개념 몫 {len(held)}행 {dict(held_kinds)}, 거름 {dict(reasons)}')
    elif args.stage == 'refilter':
        result = {}
        for role in ROLES:
            path = args.out / f'holdout_{role}.jsonl'
            rows = _read_jsonl(path)
            kept = refilter_rows(rows, measured)
            _write_jsonl(path, kept)
            result[role] = {'before': len(rows), 'after': len(kept)}
        _update_meta(args.out, refilter=result)
        print(f'refilter: {result}')
    else:
        rows = [r for role in ROLES for r in _read_jsonl(args.out / f'{role}.jsonl')]
        train, valid = split_rows(rows, seed=args.seed)
        _write_jsonl(args.out / 'train.jsonl', train)
        _write_jsonl(args.out / 'valid.jsonl', valid)
        counts = {role: {'train': sum(r['role'] == role for r in train), 'valid': sum(r['role'] == role for r in valid)}
                  for role in ROLES}
        _update_meta(args.out, split=counts)
        print(f'train {len(train)}행, valid {len(valid)}행, {counts}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
