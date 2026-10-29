## @file
# @brief 하네스 모델별 측정(계획 Task 11, spec 8-2): 같은 하네스·같은 세트로 모델 하나를 재서 rows.jsonl과 report.md를 남김
# - 정확도 측정은 동시 요청(--workers)으로 돌림: 범위(scope), 판정(judge), 채점(grading), 생성 전체(answers)
# - 시간 측정(timing)은 단일 요청을 순서대로 보냄. 동시 요청은 서로 기다리는 시간이 섞여 첫 문장·전체 시간이 부풀기 때문
# - 노출 지표: 표시 없이 나간 문장을 26B 판정기(--audit-url, 운영 8000번 읽기 호출)로 다시 판정해 근거와 어긋난 비율을 냄
#   - 사용자 표본 기준 값은 사용자 확인이 끝난 뒤 따로 냄(이 도구는 26B 기준만)
# - 미세조정 비교(계획 Task 4): --holdout으로 학습·제외 개념 표를 나누고, --astra로 노출 문장을 외부 검사 모델로도 판정함
#   - 미세조정 전후 비율 차이는 bootstrap_diff로 95% 구간을 냄
# - 결과는 Git 밖 ai-core/outputs/harness-measure/<모델>/에 둠. 중단 뒤 다시 실행하면 rows.jsonl에 있는 항목은 건너뜀
#
# 실행 예(ai-core 폴더, 명령 전체는 docs/harness-measurement.md 4절)
#   python -m eval.harness_measure --base-url http://127.0.0.1:20320/v1 --model gemma-4-E4B-Q4_K_M \
#     --gpu 1 --workers 4 --out outputs/harness-measure/e4b-q4
import argparse
import fcntl
import json
import os
import random
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import httpx

AI_CORE = Path(__file__).resolve().parents[1]
if str(AI_CORE) not in sys.path:
    sys.path.insert(0, str(AI_CORE))

SETS_DIR = Path(os.environ.get('HARNESS_SETS_DIR') or AI_CORE / 'outputs' / 'harness-sets')
TASKS = ('scope', 'judge', 'grading', 'answers', 'timing')
# 운영 26B(8000번)에 겹쳐 보내는 요청 수 상한(--prod-slots): 운영 서버를 함께 쓰므로 측정이 학습자 요청을 밀어내지 않게 함
# - 여러 측정 프로세스(모델별 실행)가 함께 돌아도 합계가 넘지 않도록 프로세스 사이에 공유하는 잠금 파일 묶음으로 셈
PROD_SLOTS_DIR = Path(tempfile.gettempdir()) / 'cs-harness-prod-slots'


## @brief 비율; 분모가 0이면 None(지어낸 0%를 피함)
def _rate(k: int, n: int) -> float | None:
    return round(k / n, 4) if n else None


## @brief 양 끝을 포함한 선형 보간 백분위(q는 0~1); 값이 하나면 그 값
def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _spread(values: list[float]) -> dict | None:
    return {'median': round(statistics.median(values), 3), 'p90': round(_percentile(values, 0.9), 3)} if values else None


## @brief 측정 결과 행(rows.jsonl)으로 spec 8-2 지표를 계산하는 순수 함수
# - scope: 범위 정확도(전체·종류별). 흐름이 실제로 쓴 개념(후속이면 직전 개념)이 정답과 같아야 맞음
# - judge: 틀린 문장 검출률(틀림 행 중 통과 못 함)·오경보율(맞음 행 중 통과 못 함). 통과는 supported + 근거 구절 대조 성공
# - grading: 거짓 통과(사람 미통과 중 모델 통과)·거짓 불통과(사람 통과 중 모델 미통과). 모델 호출 실패는 빼고 errors로 셈
# - answers: 결과 종류 수, 확인 필요 비율, 근거 없음 비율, 한자 섞인 문장 수, 노출 지표
#   - 화면에 남는 문장은 답변 완료(answered) 답의 문장뿐. 원문 대체 답의 문장은 화면에서 지워짐
# - timing: 단일 요청 표본의 첫 문장·전체 시간 중앙값과 90% 값
def metrics(rows: list[dict]) -> dict:
    by_task = defaultdict(list)
    for row in rows:
        by_task[row['task']].append(row)
    result = {}

    scope = by_task['scope']
    if scope:
        correct = lambda r: r['concepts'] is not None and sorted(r['concepts']) == sorted(r['expected'])
        kinds = sorted({r['kind'] for r in scope})
        result['scope'] = {'n': len(scope), 'accuracy': _rate(sum(map(correct, scope)), len(scope)),
                           'errors': sum(r['concepts'] is None for r in scope),
                           'by_kind': {k: _rate(sum(correct(r) for r in scope if r['kind'] == k),
                                                sum(r['kind'] == k for r in scope)) for k in kinds}}

    judge = by_task['judge']
    if judge:
        wrong = [r for r in judge if r['label'] == 'unsupported']
        right = [r for r in judge if r['label'] == 'supported']
        kinds = sorted({r['perturbation'] for r in wrong})
        result['judge'] = {'n': len(judge), 'errors': sum(r.get('error') is not None for r in judge),
                           'detection': _rate(sum(not r['passed'] for r in wrong), len(wrong)),
                           'false_alarm': _rate(sum(not r['passed'] for r in right), len(right)),
                           'by_perturbation': {k: _rate(sum(not r['passed'] for r in wrong if r['perturbation'] == k),
                                                        sum(r['perturbation'] == k for r in wrong)) for k in kinds}}

    grading = by_task['grading']
    if grading:
        judged = [r for r in grading if r['model_pass'] is not None]
        human_fail = [r for r in judged if not r['human_pass']]
        human_pass = [r for r in judged if r['human_pass']]
        false_pass = sum(r['model_pass'] for r in human_fail)
        false_fail = sum(not r['model_pass'] for r in human_pass)
        result['grading'] = {'n': len(judged), 'errors': len(grading) - len(judged),
                             'human_fail': len(human_fail), 'false_pass': false_pass,
                             'false_pass_rate': _rate(false_pass, len(human_fail)),
                             'human_pass': len(human_pass), 'false_fail': false_fail,
                             'false_fail_rate': _rate(false_fail, len(human_pass))}

    answers = by_task['answer']
    if answers:
        outcomes = Counter(r['outcome'] for r in answers)
        shown = [s for r in answers if r['outcome'] == 'answered' for s in r['sentences']]
        unmarked = [s for s in shown if s['status'] == 'verified']
        audited = [s for s in unmarked if s['audit'] is not None]
        mismatch = sum(not s['audit'] for s in audited)
        sourced = len(answers) - outcomes['blocked']
        result['answers'] = {'n': len(answers), 'outcomes': dict(sorted(outcomes.items())),
                             'shown_sentences': len(shown),
                             'needs_check_rate': _rate(len(shown) - len(unmarked), len(shown)),
                             'no_source_rate': _rate(outcomes['no_source'], sourced),
                             'fallback_rate': _rate(outcomes['fallback'] + outcomes['generation_failed'], sourced),
                             'han': sum(s['han'] for s in shown),
                             'exposure': {'unmarked': len(unmarked), 'audited': len(audited), 'mismatch': mismatch,
                                          'audit_errors': len(unmarked) - len(audited),
                                          'rate': _rate(mismatch, len(audited))}}
        # 외부 검사(--astra)를 돌린 행이 있으면 같은 문장으로 노출 지표를 하나 더 냄(검사 실패·미검사는 errors)
        if any('astra' in s for s in unmarked):
            from eval.astra_audit import exposure

            result['answers']['exposure_astra'] = exposure([s.get('astra') for s in unmarked])

    if judge:
        result['judge']['by_origin'] = judge_by_origin(judge)

    timing = by_task['timing']
    if timing:
        result['timing'] = {'n': len(timing), 'first': _spread([r['first_s'] for r in timing if r.get('first_s') is not None]),
                            'total': _spread([r['total_s'] for r in timing if r.get('total_s') is not None])}
    return result


## @brief 행 하나가 다루는 개념 ID들: concept(검색 세트) → expected(범위 세트 정답) → concepts(흐름이 고른 개념) 순으로 씀
# - 정답이 있으면 정답을 씀: 모델이 고른 개념으로 나누면 범위를 틀린 행이 엉뚱한 쪽에 들어감
def _row_concepts(row: dict) -> list[str]:
    if row.get('concept'):
        return [row['concept']]
    return list(row.get('expected') or row.get('concepts') or [])


## @brief 행을 학습 개념·제외 개념 두 묶음으로 나눠 각각 metrics를 냄
# - 개념 하나라도 제외 개념이면 제외 쪽(비교 질문의 한쪽이 처음 보는 개념이면 그 점수는 학습 효과로 볼 수 없음)
# - 개념이 없는 행(지도 밖 질문, 판정·채점 세트)은 학습 쪽에 둠. 제외 개념 표는 범위·생성 지표만 의미가 있음
def metrics_by_group(rows: list[dict], holdout: set[str]) -> dict:
    groups = {'trained': [], 'holdout': []}
    for row in rows:
        groups['holdout' if set(_row_concepts(row)) & holdout else 'trained'].append(row)
    return {name: metrics(group) for name, group in groups.items()}


## @brief 0/1 결과 목록 두 개(미세조정 전·후)의 비율 차이(후 − 전)에 대한 95% 부트스트랩 구간
# - 두 목록을 각각 복원 추출로 n번 다시 뽑아 차이의 2.5%·97.5% 값을 냄. 고정 seed라 다시 돌려도 같은 구간
# - 구간이 0을 포함하면 "좋아졌다/나빠졌다"를 표본 운과 구별할 수 없다는 뜻
def bootstrap_diff(before: list[int], after: list[int], n: int = 2000, seed: int = 0) -> tuple[float, float]:
    rng = random.Random(seed)
    diffs = [sum(rng.choices(after, k=len(after))) / len(after) - sum(rng.choices(before, k=len(before))) / len(before)
             for _ in range(n)]
    return round(_percentile(diffs, 0.025), 4), round(_percentile(diffs, 0.975), 4)


# 교란 방식의 출처: 규칙(코드로 바꿈)과 모델(26B가 만듦). 학습 데이터 교란도 26B가 만들므로 모델 쪽만 오르면 모양을 외운 것
ORIGINS = {'rule': ('number', 'negation'), 'model': ('addition', 'swap_concept')}


## @brief 판정 행의 틀린 문장 검출률을 교란 출처(rule·model)별로 냄
def judge_by_origin(rows: list[dict]) -> dict:
    wrong = [r for r in rows if r.get('task', 'judge') == 'judge' and r['label'] == 'unsupported']
    result = {}
    for origin, kinds in ORIGINS.items():
        group = [r for r in wrong if r['perturbation'] in kinds]
        result[origin] = {'n': len(group), 'detection': _rate(sum(not r['passed'] for r in group), len(group))}
    return result


## @brief 하네스 기록기 대신 넣어 답 하나의 기록(harness.answer가 채우는 record)을 메모리로 받음
class _Capture:
    def __init__(self):
        self.records = []

    def write(self, record: dict) -> None:
        self.records.append(record)


## @brief 잠금 파일 n개 중 비어 있는 하나를 잡을 때까지 기다림; 잡은 동안 운영 서버 요청 한 칸을 차지함
@contextmanager
def _prod_slot(n: int):
    PROD_SLOTS_DIR.mkdir(parents=True, exist_ok=True)
    while True:
        for index in range(n):
            with open(PROD_SLOTS_DIR / f'slot-{index}', 'w') as file:
                try:
                    fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                try:
                    yield
                    return
                finally:
                    fcntl.flock(file, fcntl.LOCK_UN)
        time.sleep(0.05)


## @brief 요청 하나를 보내는 동안 운영 서버 칸 하나를 잡는 HTTP 전송 계층; 운영 주소로 가는 모든 호출(생성·판정·검사)에 씀
# - 비스트리밍 응답은 생성이 끝난 뒤 헤더가 오므로, 헤더를 받을 때까지 잡으면 서버가 일하는 시간을 덮음
class _SlotTransport(httpx.HTTPTransport):
    def __init__(self, slots: int):
        super().__init__()
        self.slots = slots

    def handle_request(self, request):
        with _prod_slot(self.slots):
            return super().handle_request(request)


## @brief 측정 대상 모델 하나의 연결: 생성 게이트웨이, (선택) 다른 모델의 판정, 26B 노출 판정기
# - 게이트웨이마다 새 잠금을 줌: 운영 BUSY 잠금은 서버 프로세스 안 공유 자원이라 측정 도구에는 없고,
#   측정 스레드끼리 같은 잠금을 쓰면 동시 요청이 모두 LLMUnavailable이 되기 때문
# - 운영 주소(audit_url)로 가는 클라이언트는 _SlotTransport로 프로세스 합계 prod_slots개까지만 동시에 보냄
class Models:
    def __init__(self, base_url, concept_map, judge_url=None, audit_url=None, prod_slots=2):
        from openai import OpenAI

        def factory(url):
            http = (lambda: httpx.Client(transport=_SlotTransport(prod_slots), timeout=None)) if url == audit_url else None
            return lambda: OpenAI(base_url=url, api_key=os.getenv('VLLM_API_KEY', 'not-needed'), max_retries=0,
                                  **({'http_client': http()} if http else {}))

        self.concept_map, self.factory = concept_map, factory
        self.base_url, self.judge_url, self.audit_url = base_url, judge_url, audit_url

    ## @brief 질문·항목 하나에 쓸 하네스 게이트웨이; judge_url이 있으면 판정 역할만 그 모델로 보냄(26B 판정기 교차 측정)
    def gateway(self):
        from serving.harness_llm import HarnessGateway

        gateway = HarnessGateway(self.concept_map, threading.Lock(), self.factory(self.base_url))
        if self.judge_url:
            gateway.judge = HarnessGateway(self.concept_map, threading.Lock(), self.factory(self.judge_url)).judge
        return gateway

    ## @brief 26B 판정기로 문장 하나를 하네스와 같은 규칙(supported + 근거 구절 대조)으로 다시 판정; 호출 실패면 None
    def audit(self, text, keys, passages):
        from serving.harness import _judged
        from serving.harness_llm import HarnessGateway

        return _judged(HarnessGateway(self.concept_map, threading.Lock(), self.factory(self.audit_url)),
                       text, keys, passages, [])


def _scope_row(models, row):
    from serving.stuck import LLMUnavailable

    out = {'task': 'scope', 'id': row['id'], 'kind': row['kind'], 'expected': row['expected']}
    try:
        concepts, follow_up = models.gateway().scope(row['question'], row['previous'], models.concept_map)
    except LLMUnavailable as exc:
        return {**out, 'concepts': None, 'error': str(exc)}
    # 흐름(harness.answer)과 같은 규칙: 후속 질문이고 직전 개념이 있으면 직전 개념을 이어 씀
    used = list(row['previous']) if follow_up and row['previous'] else concepts
    return {**out, 'concepts': used, 'follow_up': follow_up}


def _judge_row(models, row):
    from types import SimpleNamespace

    from serving.harness import _judged

    judgments = []
    passed = _judged(models.gateway(), row['sentence'], ['p1'], {'p1': SimpleNamespace(text=row['passage'])}, judgments)
    return {'task': 'judge', 'id': row['id'], 'label': row['label'], 'perturbation': row['perturbation'],
            'passed': passed is True, 'error': judgments[0].get('error'), 'judgment': judgments[0]}


## @brief 서술 채점 한 건: 하네스판 채점(근거 구절이 학생 답에 있어야 충족)의 모든 항목 충족을 모델 통과로 봄(stuck._conclude와 같음)
def _grading_row(models, row):
    from serving.harness_stuck import HarnessStuckGateway
    from serving.item_bank import Item
    from serving.stuck import LLMUnavailable

    gateway = HarnessStuckGateway(models.concept_map, threading.Lock(), None, models.factory(models.base_url))
    item = Item.model_construct(id=row['id'], concept='mohler', purpose='explanation', objective=0,
                                format='explanation', question=row['question'], rationale=row['reference'],
                                rubric=row['rubric'])
    out = {'task': 'grading', 'id': row['id'], 'human_pass': row['human_pass'], 'split': row['split']}
    try:
        met = gateway.grade_explanation(item, row['answer'])
    except LLMUnavailable as exc:
        return {**out, 'model_pass': None, 'error': str(exc)}
    return {**out, 'model_pass': all(met), 'met': met}


## @brief 직전 개념이 있는 후속 질문용 근거 기록: 개념만 있고 문단·검증 문장은 없음(흐름이 그 개념으로 새로 검색함)
def _previous(row):
    from serving.harness import Grounding

    return Grounding(tuple(row['previous']), (), ()) if row.get('previous') else None


## @brief 답 하나의 기록을 측정 행으로: 문장마다 상태·한자·(답변 완료 답의 verified 문장만) 26B 판정
def _answer_row(models, retriever, row, record, task, audit=True):
    from serving.harness import has_han

    found = {p.id: p for p in retriever.by_ids([p['id'] for p in record.get('passages', [])])}
    passages = {p['key']: found[p['id']] for p in record.get('passages', []) if p['id'] in found}
    sentences = []
    for entry in record.get('sentences', []):
        if 'status' not in entry:  # 판정 중 끊긴 문장(원문 대체 직전 등)은 화면에 나가지 않음
            continue
        unmarked = record['outcome'] == 'answered' and entry['status'] == 'verified'
        sentences.append({'text': entry['text'], 'sources': entry['sources'], 'status': entry['status'],
                          'han': has_han(entry['text']),
                          'audit': models.audit(entry['text'], entry['sources'], passages) if unmarked and audit else None})
    # expected·concept는 학습/제외 개념 나누기(metrics_by_group)용, passages(키 → 문단 ID)는 나중 외부 검사(--astra)용
    return {'task': task, 'id': row['id'], 'kind': row.get('kind', 'retrieval'), 'outcome': record['outcome'],
            'expected': row.get('expected'), 'concept': row.get('concept'),
            'passages': {p['key']: p['id'] for p in record.get('passages', [])},
            'concepts': record.get('concepts'), 'timings': record.get('timings'), 'sentences': sentences,
            **({'error': record['error']} if 'error' in record else {})}


def _generation_row(models, retriever, row):
    from serving.harness import answer

    capture = _Capture()
    try:
        list(answer(row['question'], _previous(row), models.gateway(), retriever, models.concept_map, capture))
    except Exception:  # 범위·검색 예외는 기록(outcome=error)에 남았으므로 행으로 셈
        pass
    return _answer_row(models, retriever, row, capture.records[-1], 'answer')


## @brief 시간 표본 한 건: 운영 채팅 경로(chat_events, 그림·애니메이션 포함)로 첫 text_delta와 done까지 시간을 잼
def _timing_row(models, retriever, row, figures_dir):
    from serving.harness_chat import chat_events

    capture, first, total = _Capture(), None, None
    start = time.monotonic()
    try:
        for event in chat_events(row['question'], [], models.gateway(), retriever, models.concept_map, capture,
                                 figures_dir):
            if event['type'] == 'text_delta' and first is None:
                first = time.monotonic() - start
            if event['type'] == 'done':
                total = time.monotonic() - start
    except Exception:
        pass
    result = _answer_row(models, retriever, row, capture.records[-1], 'timing', audit=False)
    return {**result, 'first_s': round(first, 3) if first is not None else None,
            'total_s': round(total, 3) if total is not None else None}


## @brief 시간 표본: 검색 세트에서 개념을 돌아가며 하나씩 뽑음(개념 순서·개념 안 질문 순서는 seed로 섞음)
def timing_sample(rows: list[dict], n: int, seed: int = 0) -> list[dict]:
    rng = random.Random(seed)
    groups = defaultdict(list)
    for row in rows:
        groups[row['concept']].append(row)
    order = sorted(groups)
    rng.shuffle(order)
    for concept in order:
        rng.shuffle(groups[concept])
    picked, depth = [], 0
    while len(picked) < n and any(len(groups[c]) > depth for c in order):
        picked += [groups[c][depth] for c in order if len(groups[c]) > depth][:n - len(picked)]
        depth += 1
    return picked


## @brief GPU 하나의 사용 메모리를 1초 간격으로 gpu.csv에 기록하고 최대값(MiB)을 들고 있음
class GpuMonitor(threading.Thread):
    def __init__(self, index: int, out: Path):
        super().__init__(daemon=True)
        self.index, self.out, self.peak, self.stopped = index, out, 0, threading.Event()

    def run(self):
        with open(self.out, 'a', encoding='utf-8') as file:
            while not self.stopped.is_set():
                used = subprocess.run(['nvidia-smi', f'--id={self.index}', '--query-gpu=memory.used',
                                       '--format=csv,noheader,nounits'], capture_output=True, text=True).stdout.strip()
                if used.isdigit():
                    self.peak = max(self.peak, int(used))
                    file.write(f'{time.strftime("%Y-%m-%dT%H:%M:%S")},{used}\n')
                    file.flush()
                self.stopped.wait(1.0)


## @brief 항목 목록을 workers개 스레드로 돌려 행을 rows.jsonl에 한 줄씩 덧붙임; 이미 있는 (task, id)는 건너뜀
def _run(items, task, work, out_path, done, workers, limit=None):
    todo = [item for item in items[:limit] if (task, item['id']) not in done]
    lock = threading.Lock()
    count = [0]

    def one(item):
        row = work(item)
        with lock, open(out_path, 'a', encoding='utf-8') as file:
            file.write(json.dumps(row, ensure_ascii=False) + '\n')
            count[0] += 1
            if count[0] % 25 == 0 or count[0] == len(todo):
                print(f'{task}: {count[0]}/{len(todo)}', flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, todo))


def _load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


## @brief 외부 검사(--astra): rows.jsonl의 노출 대상 문장(답변 완료 답의 verified 문장)을 검사 모델로 판정해 문장마다 astra를 채움
# - 이미 판정한 문장(astra가 True/False)은 건너뛰고, 실패(None)나 미검사 문장만 다시 보냄. 중단 뒤 다시 실행할 수 있음
# - 문단 원문은 행의 passages(키 → 문단 ID)로 색인에서 다시 읽음. 이 필드가 없는 예전 행은 검사하지 않음(미검사로 셈)
# - 모든 행을 메모리에서 고친 뒤 임시 파일에 쓰고 바꿔 끼워, 도중에 멈춰도 rows.jsonl이 반쯤 쓰인 채 남지 않음
def astra_pass(rows_path: Path, retriever, runner) -> int:
    from eval.astra_audit import audit

    rows = _load(rows_path)
    targets = []
    for row in rows:
        if row['task'] != 'answer' or row['outcome'] != 'answered' or 'passages' not in row:
            continue
        texts = {p.id: p.text for p in retriever.by_ids(list(row['passages'].values()))}
        for sentence in row['sentences']:
            if sentence['status'] == 'verified' and sentence.get('astra') is None:
                passage = '\n\n'.join(texts[row['passages'][key]] for key in sentence['sources']
                                       if row['passages'].get(key) in texts)
                # 문단을 색인에서 찾지 못하면 보내지 않음: 빈 문단으로 판정하면 불일치로 나와 노출 지표가 부풂(미검사로 셈)
                if passage:
                    targets.append((sentence, passage))
    for (sentence, _), verdict in zip(targets, audit([{'sentence': s['text'], 'passage': p} for s, p in targets],
                                                     runner=runner)):
        sentence['astra'] = verdict
    temp = rows_path.with_suffix('.tmp')
    temp.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
    temp.replace(rows_path)
    return len(targets)


## @brief 학습·제외 개념 표(--holdout): 묶음마다 범위 정확도·노출 지표(26B·외부 검사)·확인 필요 비율
def _group_table(groups: dict) -> list[str]:
    lines = ['', '## 학습 개념 / 제외 개념', '', '| 지표 | 학습 개념 | 제외 개념 |', '|---|---|---|']

    def value(group, *path):
        for key in path:
            group = group.get(key) if isinstance(group, dict) else None
        return group

    for name, path in (('범위 정확도', ('scope', 'accuracy')), ('범위 행 수', ('scope', 'n')),
                       ('노출 지표(26B)', ('answers', 'exposure', 'rate')),
                       ('노출 판정 문장 수(26B)', ('answers', 'exposure', 'audited')),
                       ('노출 지표(외부 검사)', ('answers', 'exposure_astra', 'rate')),
                       ('노출 판정 문장 수(외부 검사)', ('answers', 'exposure_astra', 'audited')),
                       ('확인 필요 비율', ('answers', 'needs_check_rate'))):
        lines.append(f"| {name} | {value(groups['trained'], *path)} | {value(groups['holdout'], *path)} |")
    return lines


## @brief report.md: 지표 표와 측정 조건; 문서(docs/harness-measurement.md)에 옮길 원본
# - groups(metrics_by_group 결과)가 있으면 학습·제외 개념 표를 덧붙임
def report(result: dict, meta: dict, groups: dict | None = None) -> str:
    lines = [f"# 하네스 측정: {meta['model']}", '', '```json', json.dumps(meta, ensure_ascii=False, indent=2), '```', '',
             '| 지표 | 값 |', '|---|---|']

    def add(name, value):
        lines.append(f'| {name} | {value} |')

    if 'scope' in result:
        add('범위 정확도', result['scope']['accuracy'])
        for kind, value in result['scope']['by_kind'].items():
            add(f'범위 정확도 · {kind}', value)
    if 'judge' in result:
        add('판정 검출률', result['judge']['detection'])
        add('판정 오경보율', result['judge']['false_alarm'])
        for origin, value in result['judge']['by_origin'].items():
            add(f'판정 검출률 · {origin}', value['detection'])
    if 'grading' in result:
        add('채점 거짓 통과율', result['grading']['false_pass_rate'])
        add('채점 거짓 불통과율', result['grading']['false_fail_rate'])
    if 'answers' in result:
        answers = result['answers']
        add('노출 지표(26B 판정 기준)', f"{answers['exposure']['rate']} ({answers['exposure']['mismatch']}/{answers['exposure']['audited']})")
        if 'exposure_astra' in answers:
            astra = answers['exposure_astra']
            add('노출 지표(외부 검사)', f"{astra['rate']} ({astra['mismatch']}/{astra['audited']}, 검사 실패 {astra['errors']})")
        add('확인 필요 비율', answers['needs_check_rate'])
        add('근거 없음 비율', answers['no_source_rate'])
        add('원문 대체 비율', answers['fallback_rate'])
        add('한자 섞인 문장', answers['han'])
    if 'timing' in result:
        for name in ('first', 'total'):
            spread = result['timing'][name]
            add(f'시간 {name} 중앙값 / 90%(초)', f"{spread['median']} / {spread['p90']}" if spread else None)
    if groups:
        lines += _group_table(groups)
    lines += ['', '```json', json.dumps(result, ensure_ascii=False, indent=2), '```', '']
    return '\n'.join(lines)


## @brief 명령 진입점: 지정한 측정을 차례로 돌리고 rows.jsonl·metrics.json·report.md·meta.json을 out에 씀
def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m eval.harness_measure', description='하네스 모델별 측정(spec 8-2)')
    parser.add_argument('--base-url', required=True, help='측정 모델의 OpenAI 호환 주소(…/v1)')
    parser.add_argument('--model', required=True, help='그 서버가 내는 모델 이름(서버 목록과 다르면 멈춤)')
    parser.add_argument('--judge-url', help='판정 역할만 보낼 다른 모델 주소(26B 판정기 교차 측정)')
    parser.add_argument('--judge-model', help='--judge-url 서버의 모델 이름')
    parser.add_argument('--audit-url', default='http://127.0.0.1:8000/v1', help='노출 지표용 26B 판정기 주소')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--tasks', default=','.join(TASKS), help=f'쉼표로 고름: {",".join(TASKS)}')
    parser.add_argument('--workers', type=int, default=4, help='정확도 측정 동시 요청 수(시간 측정은 항상 1)')
    parser.add_argument('--gpu', type=int, help='메모리를 기록할 GPU 번호')
    parser.add_argument('--timing-n', type=int, default=60)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--limit', type=int, help='측정마다 앞에서 N개만(연결 확인용)')
    parser.add_argument('--extra-scope', type=Path, action='append', default=[],
                        help='범위·생성 측정에 덧붙일 세트(예: 제외 개념 추가 세트 holdout-scope.jsonl). 여러 번 줄 수 있음')
    parser.add_argument('--holdout', type=Path, help='제외 개념 목록(holdout.json). 주면 report.md에 학습·제외 개념 표를 따로 냄')
    parser.add_argument('--astra', action='store_true', help='노출 대상 문장을 외부 검사 모델로도 판정(rows에 astra 필드)')
    parser.add_argument('--astra-model', default=None, help='외부 검사 모델 이름(없으면 eval.astra_audit.DEFAULT_MODEL)')
    parser.add_argument('--prod-slots', type=int, default=2, help='운영 주소(--audit-url)로 동시에 보내는 요청 수 합계 상한(모든 측정 프로세스 합)')
    args = parser.parse_args(argv)
    tasks = [t for t in args.tasks.split(',') if t]
    unknown = sorted(set(tasks) - set(TASKS))
    if unknown:
        parser.error(f'모르는 측정: {unknown}')

    from openai import OpenAI

    from serving.concept_map import load_concept_map
    from serving.harness_chat import build_retriever

    for url, name in ((args.base_url, args.model), (args.judge_url, args.judge_model)):
        if url:
            served = [m.id for m in OpenAI(base_url=url, api_key='not-needed').models.list().data]
            if name not in served[:1]:
                parser.error(f'{url}의 첫 모델이 {name}이 아님: {served}')

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    rows_path = out / 'rows.jsonl'
    done = {(r['task'], r['id']) for r in _load(rows_path)} if rows_path.exists() else set()
    concept_map = load_concept_map()
    models = Models(args.base_url, concept_map, args.judge_url, args.audit_url, args.prod_slots)
    retriever = build_retriever()
    monitor = GpuMonitor(args.gpu, out / 'gpu.csv') if args.gpu is not None else None
    if monitor:
        monitor.start()
    started = time.strftime('%Y-%m-%dT%H:%M:%S')
    scope_rows = [row for path in [SETS_DIR / 'scope.jsonl', *args.extra_scope] for row in _load(path)]
    try:
        for task in tasks:
            if task == 'scope':
                _run(scope_rows, 'scope', lambda r: _scope_row(models, r), rows_path, done, args.workers, args.limit)
            elif task == 'judge':
                _run(_load(SETS_DIR / 'judge.jsonl'), 'judge', lambda r: _judge_row(models, r), rows_path, done, args.workers, args.limit)
            elif task == 'grading':
                _run(_load(SETS_DIR / 'grading.jsonl'), 'grading', lambda r: _grading_row(models, r), rows_path, done,
                     args.workers, args.limit)
            elif task == 'answers':
                _run(scope_rows, 'answer', lambda r: _generation_row(models, retriever, r),
                     rows_path, done, args.workers, args.limit)
            else:
                sample = timing_sample(_load(SETS_DIR / 'retrieval.jsonl'), args.timing_n, args.seed)
                _run(sample, 'timing', lambda r: _timing_row(models, retriever, r, out / 'figures'), rows_path, done, 1, args.limit)
    finally:
        if monitor:
            monitor.stopped.set()
            monitor.join()
    if args.astra:
        from functools import partial

        from eval.astra_audit import DEFAULT_MODEL, codex_runner

        args.astra_model = args.astra_model or DEFAULT_MODEL
        checked = astra_pass(rows_path, retriever, partial(codex_runner, model=args.astra_model))
        print(f'외부 검사: {checked}문장', flush=True)
    rows = _load(rows_path)
    result = metrics(rows)
    groups = None
    if args.holdout:
        from finetune.split import load_holdout

        groups = metrics_by_group(rows, load_holdout(args.holdout))
        (out / 'metrics-by-group.json').write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding='utf-8')
    meta = {'model': args.model, 'base_url': args.base_url, 'judge': args.judge_model, 'judge_url': args.judge_url,
            'audit_url': args.audit_url, 'prod_slots': args.prod_slots, 'tasks': tasks, 'workers': args.workers, 'timing_n': args.timing_n,
            'seed': args.seed, 'sets_dir': str(SETS_DIR), 'min_score': retriever.min_score,
            'extra_scope': [str(p) for p in args.extra_scope], 'holdout': str(args.holdout) if args.holdout else None,
            'astra_model': args.astra_model if args.astra else None,
            'started': started, 'finished': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'gpu': args.gpu, 'gpu_peak_mib': monitor.peak if monitor else None}
    previous = json.loads((out / 'meta.json').read_text()) if (out / 'meta.json').exists() else {}
    meta['gpu_peak_mib'] = max(filter(None, [meta['gpu_peak_mib'], previous.get('gpu_peak_mib')]), default=None)
    meta['runs'] = previous.get('runs', []) + [{'tasks': tasks, 'started': started, 'finished': meta['finished']}]
    (out / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'metrics.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'report.md').write_text(report(result, meta, groups), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
