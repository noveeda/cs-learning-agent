# 파일 역할: 막힘 흐름의 진단 가지치기·상한·보충 순서·혼합형 확인·복귀와, 잘못된 입력·LLM 실패 시 상태 보존을 검증
import unittest

from fastapi.testclient import TestClient

from serving.api import app
from serving.concept_map import ConceptMap, load_concept_map
from serving.item_bank import Item, load_item_bank
from serving.stuck import LLMUnavailable, MAX_DIAGNOSIS_ITEMS, StepInput, UnavailableGateway, step


# 역할: 규칙 확인용 작은 지도; top의 선수는 alpha·beta, alpha 아래로 gamma → delta → eps가 이어짐(eps는 깊이 4)
def small_map(status='reviewed', edges=None):
    edges = edges or {'top': ['alpha', 'beta'], 'alpha': ['gamma'], 'gamma': ['delta'], 'delta': ['eps'], 'beta': [], 'eps': []}
    return ConceptMap.model_validate({'id': 'test', 'title': '테스트', 'version': 1, 'status': status, 'concepts': [
        {'id': cid, 'title': cid.upper(), 'description': cid, 'objectives': ['설명할 수 있다.'],
         'prerequisites': [{'id': p, 'reason': '근거'} for p in ps]} for cid, ps in edges.items()]})


# 역할: 개념마다 진단 1·확인 2·서술 1 문항을 만들고, 정답은 모두 "ok"
def small_items(concepts, without=()):
    items = []
    for cid in concepts:
        for suffix, purpose in (('d1', 'diagnosis'), ('c1', 'check'), ('c2', 'check'), ('e1', 'explanation')):
            if (cid, purpose) in without:
                continue
            fields = {'id': f'{cid}-{suffix}', 'concept': cid, 'purpose': purpose, 'objective': 0,
                      'question': f'{cid} {suffix}', 'rationale': '근거'}
            if purpose == 'explanation':
                fields.update(format='explanation', rubric=['핵심1', '핵심2'])
            else:
                fields.update(format='short_exact', answer='ok')
            items.append(Item.model_validate(fields))
    return items


# 역할: LLM 역할 가짜 구현; 호출을 기록해 요청당 1회 이하인지 확인하고, 서술 통과 여부를 조절
class FakeGateway:
    def __init__(self, identified='top', rubric_pass=True):
        self.calls, self.identified, self.rubric_pass = [], identified, rubric_pass

    def identify(self, excerpt, concept_map):
        self.calls.append('identify')
        return self.identified

    def explain(self, concept, attempt, entry):
        self.calls.append('explain')
        return f'{concept.id} 설명 {attempt}'

    def grade_explanation(self, item, answer):
        self.calls.append('grade')
        return [self.rubric_pass] * len(item.rubric)

    def summarize(self, state, concept_map):
        self.calls.append('summarize')
        return '요약'


ROADMAP = {'kind': 'roadmap', 'concept': 'top', 'return_to': '/learn?concept=top'}


class Session:
    """한 세션을 끝까지 진행하며 요청마다 LLM 호출 수를 확인하는 도우미."""

    def __init__(self, test, gateway=None, concept_map=None, items=None, entry=ROADMAP):
        self.test, self.gateway = test, gateway or FakeGateway()
        self.map = concept_map or small_map()
        self.items = items if items is not None else small_items([c.id for c in self.map.concepts])
        self.state = self.send({'type': 'start', 'entry': entry}, start=True)

    def send(self, data, start=False):
        before = len(self.gateway.calls)
        new = step(None if start else self.state, StepInput.model_validate(data), self.gateway, self.map, self.items, 1000.0)
        self.test.assertLessEqual(len(self.gateway.calls) - before, 1, '요청당 LLM 호출은 1회 이하')
        self.state = new
        return new

    # 역할: 문항의 실제 정답 키(아는 학습자의 답)
    def key(self, item_id):
        return next(i.answer for i in self.items if i.id == item_id)

    # 역할: 진단 문항에 답함; known에 있는 개념이면 정답 키로 맞히고 아니면 틀림
    def diagnose(self, known=(), skip=()):
        while self.state.stage == 'diagnose':
            concept = self.state.view['concept']['id']
            if concept in skip:
                self.send({'type': 'skip_known', 'item': self.state.pending})
            else:
                answer = self.key(self.state.pending) if concept in known else '틀린 답'
                self.send({'type': 'answer', 'item': self.state.pending, 'answer': answer})

    # 역할: 보충 대상마다 설명을 받고 확인 문항·서술에 답함; check_ok가 False면 적용 문항을 틀림
    def remediate(self, check_ok=True):
        while self.state.stage not in ('return', 'complete'):
            if self.state.stage in ('remediate', 'decide'):
                self.send({'type': 'continue'})
            elif self.state.stage == 'check':
                self.send({'type': 'answer', 'item': self.state.pending, 'answer': 'ok' if check_ok else 'no'})
            elif self.state.stage == 'explain':
                self.send({'type': 'answer', 'item': self.state.pending, 'answer': '자기 말 설명'})
        if self.state.stage == 'return':
            self.send({'type': 'continue'})


class DiagnosisTests(unittest.TestCase):
    # 검증 목적: 맞힌 선수 개념 아래로는 내려가지 않고, 틀린 개념만 한 단계씩 더 내려가는지(가지치기) 확인
    def test_prunes_known_and_descends_into_gaps(self):
        session = Session(self)
        session.diagnose(known={'beta', 'delta'})
        probes = [(p.concept, p.depth, p.result) for p in session.state.probes]
        self.assertEqual(probes, [('alpha', 1, 'gap'), ('beta', 1, 'known'), ('gamma', 2, 'gap'), ('delta', 3, 'known')])

    # 검증 목적: 빈틈은 선수 개념부터, 막힌 개념은 마지막(S6 재확인)으로 보충 순서를 정하는지 확인
    def test_remediation_order_is_bottom_up_then_target(self):
        session = Session(self)
        session.diagnose(known={'beta', 'delta'})
        self.assertEqual([f.concept for f in session.state.fixes], ['gamma', 'alpha', 'top'])
        self.assertEqual([g['id'] for g in session.state.view['gaps']], ['gamma', 'alpha'])

    # 검증 목적: 깊이 3을 넘는 선수 개념(eps, 깊이 4)은 모두 틀려도 묻지 않는지 확인
    def test_stops_at_depth_limit(self):
        session = Session(self)
        session.diagnose()
        self.assertNotIn('eps', [p.concept for p in session.state.probes])
        self.assertEqual(max(p.depth for p in session.state.probes), 3)

    # 검증 목적: 진단 문항이 5개에 도달하면 남은 개념을 묻지 않고 기록만 남기는지 확인
    def test_stops_at_item_budget(self):
        edges = {'hub': [f'p{i}' for i in range(1, 7)], **{f'p{i}': [] for i in range(1, 7)}}
        concept_map = small_map(edges=edges)
        session = Session(self, concept_map=concept_map, entry={**ROADMAP, 'concept': 'hub'})
        session.diagnose(known={f'p{i}' for i in range(1, 7)})
        self.assertEqual(len(session.state.probes), MAX_DIAGNOSIS_ITEMS)
        budget = [e for e in session.state.log if e.kind == 'diagnosis_budget_reached']
        self.assertEqual(budget[0].detail['unchecked'], ['p6'])

    # 검증 목적: "이미 알아요"는 자기 보고로 기록하고 그 아래로 내려가지 않는지 확인
    def test_skip_known_is_recorded_and_pruned(self):
        session = Session(self)
        session.diagnose(skip={'alpha'}, known={'beta'})
        self.assertEqual([(p.concept, p.result) for p in session.state.probes], [('alpha', 'self_known'), ('beta', 'known')])

    # 검증 목적: "바로 설명"은 진단을 끝내고 막힌 개념만 보충 대상으로 두며 표시를 남기는지 확인
    def test_opt_out_skips_to_target(self):
        session = Session(self)
        session.send({'type': 'opt_out'})
        self.assertTrue(session.state.opted_out)
        self.assertEqual([f.concept for f in session.state.fixes], ['top'])

    # 검증 목적: 진단 문항이 없는 개념은 빈틈으로 단정하지 않고 no_items로 남기는지 확인
    def test_concept_without_items_is_not_assumed_gap(self):
        concepts = ['top', 'alpha', 'beta', 'gamma', 'delta', 'eps']
        session = Session(self, items=small_items(concepts, without={('beta', 'diagnosis')}))
        session.diagnose(known={'alpha'})
        self.assertIn(('beta', 'no_items'), [(p.concept, p.result) for p in session.state.probes])
        self.assertNotIn('beta', [f.concept for f in session.state.fixes])


class RemediationTests(unittest.TestCase):
    # 검증 목적: 혼합형 확인을 모두 통과하면 복귀 요약과 복귀 지점까지 도달하고 측정 기록이 남는지 확인
    def test_full_flow_reaches_return_point(self):
        session = Session(self)
        session.diagnose(known={'beta', 'delta'})
        session.remediate()
        state = session.state
        self.assertEqual(state.stage, 'complete')
        self.assertEqual(state.view['return_to'], '/learn?concept=top')
        self.assertEqual([f.outcome for f in state.fixes], ['passed', 'passed', 'passed'])
        kinds = [e.kind for e in state.log]
        for kind in ('stuck_started', 'probe_answered', 'remediated', 'check_answered', 'explanation_graded', 'returned'):
            self.assertIn(kind, kinds)

    # 검증 목적: 적용 문항은 맞혀도 서술 루브릭을 통과하지 못하면 이해로 인정하지 않는지(혼합형) 확인
    def test_check_and_rubric_must_both_pass(self):
        session = Session(self, gateway=FakeGateway(rubric_pass=False))
        session.send({'type': 'opt_out'})
        session.send({'type': 'continue'})
        session.send({'type': 'answer', 'item': session.state.pending, 'answer': 'ok'})
        session.send({'type': 'answer', 'item': session.state.pending, 'answer': '설명'})
        self.assertEqual(session.state.fixes[0].outcome, 'pending')
        view = session.state.view
        self.assertEqual((view['kind'], view['passed'], view['retry']), ('result', False, True))
        self.assertEqual(session.state.stage, 'remediate')

    # 검증 목적: 재보충 2회까지 실패하면 미해결로 기록하고, 계속하기로 다음 단계에 넘어가며, 재보충마다 다른 확인 문항을 쓰는지 확인
    def test_unresolved_after_retry_limit(self):
        session = Session(self)
        session.send({'type': 'opt_out'})
        asked = []
        while session.state.stage != 'decide':
            if session.state.stage == 'check':
                asked.append(session.state.pending)
            if session.state.stage == 'remediate':
                session.send({'type': 'continue'})
            elif session.state.stage == 'check':
                session.send({'type': 'answer', 'item': session.state.pending, 'answer': 'no'})
            elif session.state.stage == 'explain':
                session.send({'type': 'answer', 'item': session.state.pending, 'answer': '설명'})
        self.assertEqual(session.state.fixes[0].attempts, 3)
        self.assertEqual(session.state.fixes[0].outcome, 'unresolved')
        self.assertEqual(asked, ['top-c1', 'top-c2', 'top-c1'])
        session.send({'type': 'continue'})
        self.assertEqual(session.state.stage, 'return')

    # 검증 목적: 확인 문항이 없는 개념은 통과로 기록하지 않고 unchecked로 남기는지 확인
    def test_missing_check_items_are_unchecked(self):
        concepts = ['top', 'alpha', 'beta', 'gamma', 'delta', 'eps']
        session = Session(self, items=small_items(concepts, without={('top', 'check')}))
        session.send({'type': 'opt_out'})
        session.send({'type': 'continue'})
        self.assertEqual(session.state.fixes[0].outcome, 'unchecked')
        self.assertEqual(session.state.stage, 'decide')


class SafetyTests(unittest.TestCase):
    # 검증 목적: 화면용 상태(view)에 정답 키·근거·루브릭이 들어가지 않는지 확인
    def test_view_never_contains_answer_keys(self):
        session = Session(self)
        while session.state.stage == 'diagnose':
            item = session.state.view['item']
            self.assertFalse({'answer', 'rationale', 'rubric'} & set(item))
            session.send({'type': 'answer', 'item': session.state.pending, 'answer': 'no'})
        self.assertNotIn('"answer": "ok"', session.state.model_dump_json())

    # 검증 목적: 현재 문항이 아닌 답·단계에 맞지 않는 입력·중복 시작은 거부하고 상태를 바꾸지 않는지 확인
    def test_rejects_stale_or_invalid_input(self):
        session = Session(self)
        before = session.state.model_dump()
        for data in ({'type': 'answer', 'item': 'beta-d1', 'answer': 'ok'}, {'type': 'continue'}, {'type': 'start', 'entry': ROADMAP}):
            with self.assertRaises(ValueError):
                session.send(data)
        self.assertEqual(session.state.model_dump(), before)

    # 검증 목적: LLM이 필요한 단계에서 연결이 없으면 예외로 끝나고 원래 상태가 보존되는지 확인
    def test_llm_failure_keeps_state(self):
        session = Session(self)
        session.send({'type': 'opt_out'})
        before = session.state.model_dump()
        with self.assertRaises(LLMUnavailable):
            step(session.state, StepInput(type='continue'), UnavailableGateway(), session.map, session.items, 1.0)
        self.assertEqual(session.state.model_dump(), before)

    # 검증 목적: 검수 전(draft) 지도로는 진단을 시작하지 않는지 확인
    def test_rejects_draft_concept_map(self):
        with self.assertRaises(ValueError):
            Session(self, concept_map=small_map(status='draft'))

    # 검증 목적: 붙여넣은 문장은 개념 특정(LLM)으로 시작하고, 지도 밖이면 범위 밖으로 끝나는지 확인
    def test_excerpt_entry_identifies_concept_or_stops(self):
        excerpt = {'kind': 'excerpt', 'excerpt': '스택에서 막혔어요', 'return_to': '/learn'}
        found = Session(self, gateway=FakeGateway(identified='top'), entry=excerpt)
        self.assertEqual((found.state.target, found.state.stage), ('top', 'diagnose'))
        missing = Session(self, gateway=FakeGateway(identified='not-in-map'), entry=excerpt)
        self.assertEqual(missing.state.stage, 'out_of_scope')


class RepositoryDataTests(unittest.TestCase):
    # 검증 목적: 실제 검수된 지도·문항 은행으로 스택에서 막힌 흐름이 진단을 끝내고 보충 계획까지 가는지 확인
    def test_stack_session_with_repository_data(self):
        session = Session(self, concept_map=load_concept_map(), items=load_item_bank(),
                          entry={'kind': 'roadmap', 'concept': 'stack', 'return_to': '/learn?concept=stack'})
        self.assertEqual(session.state.view['concept']['id'], 'adt')
        session.diagnose(known={'linked-list'})
        self.assertEqual([p.concept for p in session.state.probes], ['adt', 'linked-list'])
        self.assertEqual([f.concept for f in session.state.fixes], ['adt', 'stack'])

    # 검증 목적: API가 새 세션을 만들고, LLM이 필요한 단계는 503, 오래된 답은 409로 돌려주는지 확인
    def test_api_contract(self):
        client = TestClient(app)
        start = client.post('/stuck/step', json={'input': {'type': 'start', 'entry': {
            'kind': 'roadmap', 'concept': 'stack', 'return_to': '/learn?concept=stack'}}})
        self.assertEqual(start.status_code, 200)
        state = start.json()
        self.assertEqual(state['view']['item']['id'], 'adt-d1')
        self.assertNotIn('answer', state['view']['item'])
        stale = client.post('/stuck/step', json={'state': state, 'input': {'type': 'answer', 'item': 'array-d1', 'answer': 'A'}})
        self.assertEqual(stale.status_code, 409)
        opted = client.post('/stuck/step', json={'state': state, 'input': {'type': 'opt_out'}}).json()
        self.assertEqual(client.post('/stuck/step', json={'state': opted, 'input': {'type': 'continue'}}).status_code, 503)


if __name__ == '__main__':
    unittest.main()
