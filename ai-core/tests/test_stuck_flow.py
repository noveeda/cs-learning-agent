# 파일 역할: 막힘 흐름의 진단 가지치기·상한·보충 순서·혼합형 확인·보충 애니메이션·복귀와, 잘못된 입력·LLM 실패 시 상태 보존을 검증
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from serving.animation import CONCEPT_SCENES, SIMULATORS
from serving.animation.model import AnimationRequestModel
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


STACK_SCENE = {'kind': 'stack', 'ops': [{'op': 'push', 'value': 1}, {'op': 'push', 'value': 2}, {'op': 'pop'}]}


# 역할: LLM 역할 가짜 구현; 호출을 기록해 요청당 호출 수를 확인하고, 서술 통과 여부와 고를 장면을 조절
# - scene: choose_animation이 돌려줄 장면 요청(dict), None(장면 없음), 또는 예외 객체(던짐)
# - explained_scenes: explain이 받은 장면 요약을 호출 순서대로 기록(글 예시와 장면 일치 확인용)
class FakeGateway:
    def __init__(self, identified='top', rubric_pass=True, rubric_extra=0, scene=STACK_SCENE):
        self.calls, self.identified, self.rubric_pass, self.rubric_extra = [], identified, rubric_pass, rubric_extra
        self.scene, self.explained_scenes = scene, []

    def identify(self, excerpt, concept_map):
        self.calls.append('identify')
        return self.identified

    def explain(self, concept, attempt, entry, scene=None):
        self.calls.append('explain')
        self.explained_scenes.append(scene)
        return f'{concept.id} 설명 {attempt}'

    def grade_explanation(self, item, answer):
        self.calls.append('grade')
        return [self.rubric_pass] * (len(item.rubric) + self.rubric_extra)

    def summarize(self, state, concept_map):
        self.calls.append('summarize')
        return '요약'

    def choose_animation(self, concept, entry, kinds):
        self.calls.append('animate')
        if isinstance(self.scene, Exception):
            raise self.scene
        return None if self.scene is None else AnimationRequestModel.model_validate(self.scene).root


ROADMAP = {'kind': 'roadmap', 'concept': 'top', 'return_to': '/learn?concept=top'}


class Session:
    """한 세션을 끝까지 진행하며 요청마다 LLM 호출 수를 확인하는 도우미."""

    def __init__(self, test, gateway=None, concept_map=None, items=None, entry=ROADMAP):
        self.test, self.gateway = test, gateway or FakeGateway()
        self.map = concept_map or small_map()
        self.items = items if items is not None else small_items([c.id for c in self.map.concepts])
        self.state = self.send({'type': 'start', 'entry': entry}, start=True)

    # 보충(remediate) 요청만 설명 + 장면 선택으로 LLM을 2회까지 부르고, 나머지 요청은 1회 이하
    def send(self, data, start=False):
        before = len(self.gateway.calls)
        limit = 2 if not start and self.state.stage == 'remediate' else 1
        new = step(None if start else self.state, StepInput.model_validate(data), self.gateway, self.map, self.items, 1000.0)
        self.test.assertLessEqual(len(self.gateway.calls) - before, limit, '요청당 LLM 호출 상한')
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

    # 검증 목적: 진단 결과 근거에 학습자가 본 문항 문장과 학습자 답이 들어가 "어떤 문항을 틀려 빈틈인지" 보여 줄 수 있는지 확인
    def test_diagnosis_evidence_shows_question_and_answer(self):
        session = Session(self)
        session.diagnose(known={'beta', 'delta'}, skip={'gamma'})
        evidence = {e['concept']: e for e in session.state.view['evidence']}
        self.assertEqual(evidence['alpha'], {'concept': 'alpha', 'item': 'alpha-d1', 'result': 'gap',
                                             'question': 'alpha d1', 'answer': '틀린 답'})
        self.assertEqual(evidence['beta']['answer'], 'ok')
        self.assertEqual((evidence['gamma']['result'], evidence['gamma']['answer']), ('self_known', None))

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



# 역할: 실제 지도·문항 은행으로 concept에서 막힌 세션을 열고 진단을 건너뛴 뒤 첫 보충 화면까지 진행
# - 진단할 선수 개념이 없는 개념(logarithm 등)은 시작하자마자 보충 단계라 opt_out을 보내지 않음
def remediation_session(test, concept, gateway):
    session = Session(test, gateway=gateway, concept_map=load_concept_map(), items=load_item_bank(),
                      entry={'kind': 'roadmap', 'concept': concept, 'return_to': f'/learn?concept={concept}'})
    if session.state.stage == 'diagnose':
        session.send({'type': 'opt_out'})
    session.send({'type': 'continue'})
    test.assertEqual(session.state.view['kind'], 'remediation')
    return session


class AnimationTests(unittest.TestCase):
    # 검증 목적: 장면이 정해진 개념(stack)의 보충 화면에 코드가 계산한 프레임이 붙고, 측정용 로그에 장면 종류가 남는지 확인
    def test_remediation_view_has_animation_for_stack(self):
        session = remediation_session(self, 'stack', FakeGateway())
        animation = session.state.view['animation']
        self.assertEqual((animation['kind'], len(animation['frames'])), ('stack', 4))
        self.assertEqual(set(animation), {'kind', 'title', 'frames'})
        self.assertTrue(session.state.view['text'])
        remediated = [e for e in session.state.log if e.kind == 'remediated'][-1]
        self.assertEqual(remediated.detail['animation_kind'], 'stack')

    # 검증 목적: (E2E 문제 3) 보충 글이 화면의 장면과 같은 예시를 쓰도록, 장면을 먼저 고르고 그 요약을 설명에 넘기는지 확인
    # - 글이 장면을 모르면 최소 힙 글 + 최대 힙 장면, mod 10 글 + mod 5 장면처럼 서로 다른 예시가 한 화면에 나옴
    # - 요약에는 요청과 함께 코드가 계산한 단계 캡션(steps)을 넣어, 모델이 단계별 상태를 직접 계산하다 틀리지 않게 함
    def test_explain_receives_chosen_scene(self):
        gateway = FakeGateway()
        session = remediation_session(self, 'stack', gateway)
        self.assertEqual(gateway.calls[-2:], ['animate', 'explain'])
        steps = [frame['caption'] for frame in session.state.view['animation']['frames']]
        self.assertEqual(gateway.explained_scenes[-1], {**STACK_SCENE, 'steps': steps})

    # 검증 목적: (Review Focus 1) 실행할 수 없는 장면(빈 스택 pop)·장면 선택 LLM 실패에도 503 없이 텍스트 보충만 보여 주고,
    # 실패 이유를 로그에 남기는지 확인
    def test_animation_failure_falls_back_to_text(self):
        for scene in ({'kind': 'stack', 'ops': [{'op': 'pop'}]}, LLMUnavailable('모델 응답을 받지 못했습니다.')):
            with self.subTest(scene=scene):
                session = remediation_session(self, 'stack', FakeGateway(scene=scene))
                self.assertTrue(session.state.view['text'])
                self.assertNotIn('animation', session.state.view)
                self.assertEqual(session.state.stage, 'check')
                failed = [e for e in session.state.log if e.kind == 'animation_failed']
                self.assertEqual(len(failed), 1)
                self.assertTrue(failed[0].detail['reason'])
                remediated = [e for e in session.state.log if e.kind == 'remediated'][-1]
                self.assertIsNone(remediated.detail['animation_kind'])
                # 실행하지 못한 장면은 화면에 없으므로 설명 입력에도 넣지 않음(글이 보이지 않는 장면을 가리키지 않게)
                self.assertEqual(session.gateway.explained_scenes, [None])

    # 검증 목적: 모델이 알맞은 장면이 없다고(null) 고르면 실패 로그 없이 텍스트만 보여 주는지 확인
    def test_null_scene_shows_text_only(self):
        session = remediation_session(self, 'stack', FakeGateway(scene=None))
        self.assertNotIn('animation', session.state.view)
        self.assertNotIn('animation_failed', [e.kind for e in session.state.log])
        self.assertEqual(session.gateway.explained_scenes, [None])

    # 검증 목적: 장면 목록에 없는 개념(logarithm)은 장면 선택 LLM을 부르지 않아 보충 지연이 늘지 않는지 확인
    def test_concept_without_scene_skips_choose(self):
        gateway = FakeGateway()
        session = remediation_session(self, 'logarithm', gateway)
        self.assertEqual(gateway.calls.count('animate'), 0)
        self.assertEqual(gateway.explained_scenes, [None])
        self.assertNotIn('animation', session.state.view)

    # 검증 목적: 개념별 허용 장면 표가 실제 개념 지도 ID와 시뮬레이터 종류만 쓰는지 확인(오타로 장면이 조용히 빠지는 일 방지)
    def test_concept_scenes_reference_map_and_simulators(self):
        ids = {c.id for c in load_concept_map().concepts}
        self.assertLessEqual(set(CONCEPT_SCENES), ids)
        self.assertLessEqual({k for kinds in CONCEPT_SCENES.values() for k in kinds}, set(SIMULATORS))
        self.assertEqual(CONCEPT_SCENES['stack'], ('stack',))
        self.assertNotIn('logarithm', CONCEPT_SCENES)
        # 순회 개념은 bst 장면(삽입·삭제)이 아니라 전용 순회 장면을 씀(MVP 3)
        self.assertEqual(CONCEPT_SCENES['tree-traversal'], ('tree_traversal',))

class SafetyTests(unittest.TestCase):
    # 검증 목적: 진단부터 복귀까지 매 단계 저장 상태(view·log 포함)에 정답 키·근거·루브릭 문자열이 없는지 확인
    # - 학습자는 늘 틀린 답만 내므로 정답 키 "ok"가 보이면 문항 정보가 샌 것
    # - JSON 공백 형식에 기대지 않도록 직렬화한 값 자체를 찾음(이전 검사는 공백 차이로 항상 통과했음)
    def test_state_never_contains_answer_keys(self):
        session = Session(self)
        secrets = {json.dumps(i.answer) for i in session.items if i.answer}
        secrets |= {i.rationale for i in session.items} | {r for i in session.items for r in i.rubric or []}
        send = session.send

        def checked_send(data):
            dumped = send(data).model_dump_json()
            self.assertFalse([x for x in secrets if x in dumped], data)
            return session.state

        session.send = checked_send
        session.diagnose()
        session.remediate(check_ok=False)
        self.assertEqual(session.state.stage, 'complete')

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

    # 검증 목적: 서술 채점 결과 수가 루브릭과 다르면(LLM 출력 오류) 재시도 가능한 LLMUnavailable로 끝나고 상태가 보존되는지 확인
    def test_rubric_count_mismatch_is_llm_failure(self):
        session = Session(self, gateway=FakeGateway(rubric_extra=1))
        session.send({'type': 'opt_out'})
        session.send({'type': 'continue'})
        session.send({'type': 'answer', 'item': session.state.pending, 'answer': 'ok'})
        before = session.state.model_dump()
        with self.assertRaises(LLMUnavailable):
            session.send({'type': 'answer', 'item': session.state.pending, 'answer': '설명'})
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

    # 검증 목적: API가 새 세션을 만들고, 모델 서버에 연결하지 못하면 LLM이 필요한 단계는 503, 오래된 답은 409로 돌려주는지 확인
    # - 운영 모델(8000)을 부르지 않도록 아무것도 듣지 않는 내 시험 포트(21019)를 모델 주소로 씀
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
        with patch.dict('os.environ', {'VLLM_BASE_URL': 'http://127.0.0.1:21019/v1'}):
            self.assertEqual(client.post('/stuck/step', json={'state': opted, 'input': {'type': 'continue'}}).status_code, 503)


if __name__ == '__main__':
    unittest.main()
