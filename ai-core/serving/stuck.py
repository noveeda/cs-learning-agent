# 파일 역할: 막힘 세션 흐름(S1~S8) 엔진; 요청 한 번에 한 단계만 실행하고 상태 저장은 Spring이 담당
"""Stuck-session flow: diagnose missing prerequisites, remediate bottom-up, check understanding, return."""
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel, ConfigDict, Field, model_validator

from serving.concept_map import Concept, ConceptMap
from serving.grading import grade
from serving.item_bank import Item

# 운영 상한(합의한 초기값, 예비 실험 후 조정): 학습자의 진단 부담과 반복 보충 시간을 제한
MAX_DIAGNOSIS_ITEMS = 5   # 한 세션에서 푸는 진단 문항 수
MAX_DEPTH = 3             # 막힌 개념에서 선수 관계를 따라 내려가는 깊이
MAX_RETRIES = 2           # 같은 빈틈에 대해 다른 방식으로 다시 보충하는 횟수


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


# 역할: 막힘이 시작된 곳; 로드맵 학습(개념이 정해짐) 또는 붙여넣은 문장(개념 특정 필요)
class Entry(StrictModel):
    kind: Literal['roadmap', 'excerpt']
    concept: str | None = None
    excerpt: str | None = Field(default=None, max_length=2000)
    # 보충이 끝난 뒤 돌아갈 화면 위치; 복귀(S7)와 재개(M4)의 기준
    return_to: str = Field(min_length=1, max_length=300)

    @model_validator(mode='after')
    def required_fields(self):
        if self.kind == 'roadmap' and not self.concept:
            raise ValueError('로드맵 진입에는 개념 ID가 필요합니다.')
        if self.kind == 'excerpt' and not (self.excerpt or '').strip():
            raise ValueError('붙여넣은 문장이 비어 있습니다.')
        return self


# 역할: 진단한 선수 개념 하나의 결과; 빈틈 판단 근거로 학습자에게 보여 주고 M1·M2 측정에 사용
# - answer: 학습자가 낸 답; 진단 결과 화면에서 "어떤 답으로 빈틈이 됐는지" 보여 주기 위해 보관(건너뛰기·문항 없음은 None)
class Probe(StrictModel):
    concept: str
    depth: int
    result: Literal['known', 'gap', 'self_known', 'no_items']
    item: str | None = None
    answer: str | None = None


# 역할: 보충 대상 하나의 진행 기록; attempts는 설명을 받은 횟수(첫 보충 + 재보충)
class Fix(StrictModel):
    concept: str
    attempts: int = 0
    outcome: Literal['pending', 'passed', 'unresolved', 'unchecked'] = 'pending'


# 역할: 측정 기록(S8) 한 줄; 시각·문항·학습자 답·판정을 남겨 O·M 지표를 계산할 수 있게 함
class Event(StrictModel):
    at: float
    kind: str
    concept: str | None = None
    item: str | None = None
    detail: dict = Field(default_factory=dict)


Stage = Literal['identify', 'diagnose', 'remediate', 'check', 'explain', 'decide', 'return', 'complete', 'out_of_scope']


# 역할: Spring이 체크포인트로 저장하는 세션 전체 상태; 정답 키는 넣지 않고 문항 ID만 보관
class StuckState(StrictModel):
    entry: Entry
    stage: Stage
    target: str | None = None
    frontier: list[tuple[str, int]] = Field(default_factory=list)
    probes: list[Probe] = Field(default_factory=list)
    pending: str | None = None
    pending_depth: int = 0
    fixes: list[Fix] = Field(default_factory=list)
    cursor: int = 0
    check_passed: bool | None = None
    opted_out: bool = False
    # 화면에 보여 줄 현재 내용; 새로고침 뒤에도 같은 화면을 복구하도록 상태에 저장
    view: dict = Field(default_factory=dict)
    log: list[Event] = Field(default_factory=list)


# 역할: 화면에서 보내는 입력 한 건
class StepInput(StrictModel):
    type: Literal['start', 'answer', 'skip_known', 'opt_out', 'continue']
    entry: Entry | None = None
    item: str | None = None
    answer: str | None = Field(default=None, max_length=2000)


class LLMUnavailable(RuntimeError):
    pass


# 역할: 흐름이 쓰는 LLM 역할 4가지의 계약; 실제 구현은 PR6, 테스트는 가짜 구현을 주입
class Gateway(Protocol):
    def identify(self, excerpt: str, concept_map: ConceptMap) -> str | None: ...
    def explain(self, concept: Concept, attempt: int, entry: Entry) -> str: ...
    def grade_explanation(self, item: Item, answer: str) -> list[bool]: ...
    def summarize(self, state: StuckState, concept_map: ConceptMap) -> str: ...


# 역할: LLM 역할이 아직 연결되지 않았음을 알리는 기본 구현; 상태를 바꾸지 않고 재시도 가능한 오류로 끝냄
class UnavailableGateway:
    def _fail(self, *_):
        raise LLMUnavailable('보충 설명·서술 채점 기능은 아직 연결되지 않았습니다.')
    identify = explain = grade_explanation = summarize = _fail


@dataclass(frozen=True)
class _Context:
    gateway: Gateway
    concept_map: ConceptMap
    items: tuple[Item, ...]
    input: StepInput
    now: float

    def concept(self, concept_id):
        return next(c for c in self.concept_map.concepts if c.id == concept_id)

    def items_for(self, concept_id, purpose):
        return [i for i in self.items if i.concept == concept_id and i.purpose == purpose]

    def item(self, item_id):
        return next(i for i in self.items if i.id == item_id)


# 역할: 화면용 문항; 정답·근거·루브릭을 빼서 브라우저와 저장소로 정답이 새지 않게 함
def _item_view(item):
    view = {'id': item.id, 'format': item.format, 'question': item.question}
    if item.options:
        view['options'] = item.options
    return view


def _concept_view(ctx, concept_id):
    return {'id': concept_id, 'title': ctx.concept(concept_id).title}


def _log(s, ctx, kind, concept=None, item=None, **detail):
    s.log.append(Event(at=ctx.now, kind=kind, concept=concept, item=item, detail=detail))


# 역할: 선수 개념이 앞에 오는 전체 개념 순서; 빈틈을 "가장 아래부터" 보충하는 기준
def _prerequisite_order(concept_map):
    prerequisites = {c.id: [p.id for p in c.prerequisites] for c in concept_map.concepts}
    order, seen = [], set()

    def visit(node):
        if node not in seen:
            seen.add(node)
            for p in prerequisites[node]:
                visit(p)
            order.append(node)

    for concept in prerequisites:
        visit(concept)
    return order


# 역할: 진단 시작(S3); 막힌 개념의 직접 선수 개념을 깊이 1로 대기열에 넣음
def _start_diagnosis(s, ctx):
    s.stage = 'diagnose'
    s.frontier = [(p.id, 1) for p in ctx.concept(s.target).prerequisites]
    _log(s, ctx, 'diagnosis_started', s.target)
    _next_probe(s, ctx)


# 역할: 다음 진단 문항을 고르거나 진단을 끝냄
# - 너비 우선: 직접 선수 개념을 먼저 모두 확인해, 문항 상한에 걸려도 가장 가까운 빈틈은 놓치지 않게 함
# - 이미 확인한 개념은 다시 묻지 않음(여러 경로로 만나는 공통 선수 개념)
def _next_probe(s, ctx):
    asked = sum(1 for p in s.probes if p.result in ('known', 'gap'))
    while s.frontier and asked < MAX_DIAGNOSIS_ITEMS:
        concept, depth = s.frontier.pop(0)
        if any(p.concept == concept for p in s.probes):
            continue
        candidates = ctx.items_for(concept, 'diagnosis')
        if not candidates:
            # 문항 은행이 아직 없는 개념은 판단하지 않고 기록만 남김; 빈틈으로 단정하지 않기 위함
            s.probes.append(Probe(concept=concept, depth=depth, result='no_items'))
            _log(s, ctx, 'probe_skipped', concept, reason='no_items')
            continue
        item = candidates[0]
        s.pending, s.pending_depth = item.id, depth
        s.view = {'kind': 'question', 'purpose': 'diagnosis', 'concept': _concept_view(ctx, concept), 'item': _item_view(item)}
        return
    unchecked = [c for c, _ in s.frontier if not any(p.concept == c for p in s.probes)]
    if unchecked:
        _log(s, ctx, 'diagnosis_budget_reached', unchecked=unchecked)
    s.frontier = []
    _start_remediation(s, ctx)


# 역할: 진단 결과 화면의 판정 근거 한 줄; 학습자가 본 문항 문장·보기와 학습자 답만 담고 정답 키·근거·루브릭은 넣지 않음
def _evidence_view(ctx, probe):
    view = {'concept': probe.concept, 'item': probe.item, 'result': probe.result, 'question': None, 'answer': probe.answer}
    if probe.item:
        shown = _item_view(ctx.item(probe.item))
        view['question'] = shown['question']
        if 'options' in shown:
            view['options'] = shown['options']
    return view


# 역할: 진단 결과로 보충 순서를 정함; 빈틈은 선수 개념이 먼저, 막힌 개념은 마지막(S6 재확인)
def _start_remediation(s, ctx):
    gaps = {p.concept for p in s.probes if p.result == 'gap'}
    ordered = [c for c in _prerequisite_order(ctx.concept_map) if c in gaps]
    s.fixes = [Fix(concept=c) for c in ordered] + [Fix(concept=s.target)]
    s.cursor, s.pending, s.stage = 0, None, 'remediate'
    s.view = {'kind': 'diagnosis_result', 'target': _concept_view(ctx, s.target),
              'gaps': [_concept_view(ctx, c) for c in ordered],
              # 판정 근거: 어떤 문항에 어떤 답을 내서 빈틈으로 봤는지 학습자에게 보여 줌
              'evidence': [_evidence_view(ctx, p) for p in s.probes],
              'opted_out': s.opted_out}
    _log(s, ctx, 'remediation_planned', s.target, order=[f.concept for f in s.fixes])


def _expect(ctx, *types):
    if ctx.input.type not in types:
        raise ValueError(f'지금 단계에서 받을 수 없는 입력입니다: {ctx.input.type}')


def _answer_for(s, ctx):
    if ctx.input.item != s.pending or ctx.input.answer is None:
        raise ValueError('현재 문항에 대한 답이 아닙니다. 화면을 새로고침해 주세요.')
    return ctx.input.answer


# 역할: S2 막힌 개념 특정; 붙여넣은 문장을 지도의 개념 ID 하나로 연결(LLM 1회)
def _identify(s, ctx):
    _expect(ctx, 'start', 'continue')
    concept = ctx.gateway.identify(s.entry.excerpt, ctx.concept_map)
    if concept not in {c.id for c in ctx.concept_map.concepts}:
        s.stage, s.view = 'out_of_scope', {'kind': 'out_of_scope', 'return_to': s.entry.return_to}
        _log(s, ctx, 'out_of_scope', identified=concept)
        return
    s.target = concept
    _log(s, ctx, 'concept_identified', concept)
    _start_diagnosis(s, ctx)


# 역할: S3 진단 응답 처리; 맞히면 그 아래로 내려가지 않고(가지치기), 틀리면 깊이 상한 안에서 한 단계 더 내려감
def _diagnose(s, ctx):
    if s.pending is None:
        _expect(ctx, 'start', 'continue')
        _next_probe(s, ctx)
        return
    if ctx.input.type == 'opt_out':
        # "바로 설명": 남은 진단을 그만두고 보충으로 넘어감; 측정에서 따로 분리할 수 있게 표시
        # - 이미 답한 문항에서 찾은 빈틈은 버리지 않고 막힌 개념 앞에 보충(처음부터 누르면 막힌 개념만 보충)
        s.opted_out, s.frontier = True, []
        _log(s, ctx, 'opted_out', s.target, skipped_item=s.pending)
        _start_remediation(s, ctx)
        return
    _expect(ctx, 'answer', 'skip_known')
    if ctx.input.item != s.pending:
        raise ValueError('현재 문항에 대한 응답이 아닙니다. 화면을 새로고침해 주세요.')
    item = ctx.item(s.pending)
    if ctx.input.type == 'skip_known':
        # "이미 알아요": 자기 보고로 기록하고 아는 개념처럼 가지치기; 거짓 건너뛰기 여부는 이후 분석 대상
        s.probes.append(Probe(concept=item.concept, depth=s.pending_depth, result='self_known', item=item.id))
        _log(s, ctx, 'probe_self_known', item.concept, item.id)
    else:
        answer = _answer_for(s, ctx)
        correct = grade(item, answer)
        s.probes.append(Probe(concept=item.concept, depth=s.pending_depth, result='known' if correct else 'gap',
                              item=item.id, answer=answer))
        _log(s, ctx, 'probe_answered', item.concept, item.id, answer=answer, correct=correct)
        if not correct and s.pending_depth < MAX_DEPTH:
            s.frontier += [(p.id, s.pending_depth + 1) for p in ctx.concept(item.concept).prerequisites]
    s.pending = None
    _next_probe(s, ctx)


# 역할: S4 보충; 현재 빈틈을 설명하고(LLM 1회) 같은 화면에 확인 문항을 붙임
def _remediate(s, ctx):
    _expect(ctx, 'continue')
    fix = s.fixes[s.cursor]
    fix.attempts += 1
    text = ctx.gateway.explain(ctx.concept(fix.concept), fix.attempts, s.entry)
    _log(s, ctx, 'remediated', fix.concept, attempt=fix.attempts)
    s.view = {'kind': 'remediation', 'concept': _concept_view(ctx, fix.concept), 'attempt': fix.attempts, 'text': text,
              'is_target': s.cursor == len(s.fixes) - 1}
    checks = ctx.items_for(fix.concept, 'check')
    if not checks:
        # 확인 문항이 아직 없으면 이해를 판정하지 않고 넘어감; 통과로 기록하지 않기 위해 별도 결과로 남김
        fix.outcome, s.stage, s.pending = 'unchecked', 'decide', None
        s.view['note'] = '확인 문항이 아직 없어 이해 확인을 건너뜁니다.'
        return
    # 재보충 때는 다른 확인 문항을 써서 같은 문항을 외워 통과하는 일을 줄임(문항이 모자라면 순환)
    item = checks[(fix.attempts - 1) % len(checks)]
    s.pending, s.stage = item.id, 'check'
    s.view['item'] = _item_view(item)


# 역할: S5·S6 혼합형 확인의 첫 단계; 적용 문항을 결정적으로 채점하고 서술 질문으로 넘어감
def _check(s, ctx):
    _expect(ctx, 'answer')
    answer = _answer_for(s, ctx)
    item = ctx.item(s.pending)
    s.check_passed = grade(item, answer)
    _log(s, ctx, 'check_answered', item.concept, item.id, answer=answer, correct=s.check_passed)
    explanations = ctx.items_for(item.concept, 'explanation')
    if not explanations:
        _conclude(s, ctx, s.check_passed)
        return
    s.pending, s.stage = explanations[0].id, 'explain'
    s.view = {'kind': 'question', 'purpose': 'explanation', 'concept': _concept_view(ctx, item.concept),
              'item': _item_view(explanations[0])}


# 역할: 혼합형 확인의 두 번째 단계; 서술을 루브릭으로 채점(LLM 1회)하고 두 결과가 모두 통과해야 이해로 인정
def _explain(s, ctx):
    _expect(ctx, 'answer')
    answer = _answer_for(s, ctx)
    item = ctx.item(s.pending)
    met = ctx.gateway.grade_explanation(item, answer)
    if len(met) != len(item.rubric):
        # 채점 결과 수가 루브릭과 다르면 LLM 출력 오류로 보고 재시도 가능한 오류(503)로 끝냄; 상태는 아직 바꾸지 않았음
        raise LLMUnavailable('서술 채점 결과가 올바르지 않습니다. 다시 시도해 주세요.')
    _log(s, ctx, 'explanation_graded', item.concept, item.id, answer=answer, rubric_met=met, check_passed=s.check_passed)
    _conclude(s, ctx, bool(s.check_passed) and all(met), rubric_met=met)


# 역할: 확인 결과에 따라 다음 보충 대상으로 넘어가거나, 재보충하거나, 미해결로 학습자 선택을 기다림
def _conclude(s, ctx, passed, **detail):
    fix = s.fixes[s.cursor]
    s.pending, s.check_passed = None, None
    if passed:
        fix.outcome = 'passed'
        s.view = {'kind': 'result', 'concept': _concept_view(ctx, fix.concept), 'passed': True, **detail}
        _advance(s)
    elif fix.attempts <= MAX_RETRIES:
        s.stage = 'remediate'
        s.view = {'kind': 'result', 'concept': _concept_view(ctx, fix.concept), 'passed': False, 'retry': True, **detail}
    else:
        # 재보충 상한까지 실패: 무한 반복 대신 미해결로 기록하고 진행 여부를 학습자가 고름
        fix.outcome, s.stage = 'unresolved', 'decide'
        s.view = {'kind': 'unresolved', 'concept': _concept_view(ctx, fix.concept), **detail}
    _log(s, ctx, 'fix_concluded', fix.concept, passed=passed, outcome=fix.outcome, attempts=fix.attempts)


def _advance(s):
    s.cursor += 1
    s.stage = 'remediate' if s.cursor < len(s.fixes) else 'return'


# 역할: 미해결·확인 생략 화면에서 학습자가 계속하기를 누르면 다음 보충 대상으로 이동
def _decide(s, ctx):
    _expect(ctx, 'continue')
    _log(s, ctx, 'continued', s.fixes[s.cursor].concept)
    _advance(s)
    s.view = {'kind': 'next'}


# 역할: S7 복귀; 채운 선수 개념과 원래 학습의 연결을 요약(LLM 1회)하고 복귀 지점을 돌려줌
def _return(s, ctx):
    _expect(ctx, 'continue')
    text = ctx.gateway.summarize(s, ctx.concept_map)
    s.stage = 'complete'
    s.view = {'kind': 'summary', 'text': text, 'return_to': s.entry.return_to,
              'results': [{'concept': _concept_view(ctx, f.concept), 'outcome': f.outcome, 'attempts': f.attempts} for f in s.fixes]}
    _log(s, ctx, 'returned', s.target, return_to=s.entry.return_to)


class _GraphState(TypedDict):
    job: StuckState


def _node(handler):
    def execute(state: _GraphState, runtime: Runtime[_Context]):
        handler(state['job'], runtime.context)
        return {'job': state['job']}
    return execute


# 역할: 저장된 stage에 맞는 노드 하나만 실행하고 끝내는 그래프; 다음 단계는 다음 요청에서 진행(커리큘럼과 같은 패턴)
def _build_graph():
    graph = StateGraph(_GraphState, context_schema=_Context)
    handlers = {'identify': _identify, 'diagnose': _diagnose, 'remediate': _remediate, 'check': _check,
                'explain': _explain, 'decide': _decide, 'return': _return}
    for name, handler in handlers.items():
        graph.add_node(name, _node(handler))
        graph.add_edge(name, END)
    graph.add_conditional_edges(START, lambda state: state['job'].stage,
                                {**{name: name for name in handlers}, 'complete': END, 'out_of_scope': END})
    return graph.compile()


_GRAPH = _build_graph()


# 역할: 상태와 입력을 받아 한 단계 진행한 새 상태 반환
# - 원본은 바꾸지 않음: LLM 실패·잘못된 입력이면 예외를 던지고 Spring은 마지막 저장 상태를 유지
# - 검수 전(draft) 지도로는 진단하지 않음: 정답 빈틈의 기준이 확정되지 않았기 때문
def step(original: StuckState | None, step_input: StepInput, gateway: Gateway, concept_map: ConceptMap,
         items: list[Item], now: float) -> StuckState:
    if concept_map.status != 'reviewed':
        raise ValueError('검수를 마친 개념 지도로만 진단할 수 있습니다.')
    if original is None:
        if step_input.type != 'start' or step_input.entry is None:
            raise ValueError('새 세션은 시작 입력과 진입 정보가 필요합니다.')
        entry = step_input.entry
        if entry.kind == 'roadmap' and entry.concept not in {c.id for c in concept_map.concepts}:
            raise ValueError(f'개념 지도에 없는 개념입니다: {entry.concept}')
        state = StuckState(entry=entry, stage='identify' if entry.kind == 'excerpt' else 'diagnose', target=entry.concept)
        context = _Context(gateway, concept_map, tuple(items), step_input, now)
        _log(state, context, 'stuck_started', entry.concept, entry_kind=entry.kind)
        if entry.kind == 'roadmap':
            _start_diagnosis(state, context)
            return state
    else:
        if step_input.type == 'start':
            raise ValueError('이미 시작한 세션입니다.')
        state = original.model_copy(deep=True)
    context = _Context(gateway, concept_map, tuple(items), step_input, now)
    return _GRAPH.invoke({'job': state}, context=context)['job']
