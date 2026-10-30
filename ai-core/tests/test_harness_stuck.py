## @file
# @brief [막혔어요] 하네스판(HarnessStuckGateway)의 시도별 근거 선택·서술 채점 글자 대조·복귀 요약 템플릿과 /stuck/step 스위치 연결을 검증
# - 모델은 OpenAI 가짜 클라이언트(역할별 응답), 검색기는 exclude를 따르는 가짜로 대신함
# - 상태 전이는 test_stuck_flow.py의 가짜 게이트웨이와 같은 순서로 진행되는지 같은 시나리오를 두 게이트웨이로 돌려 비교함
import json
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from openai import APIConnectionError

from rag.search import Passage
from serving.api import BUSY, app
from serving.concept_map import load_concept_map
from serving.harness import FAILED_TEXT, NO_SOURCE_TEXT
from serving.harness_stuck import SCENE_FAILED_TEXT, HarnessStuckGateway, with_particle
from serving.item_bank import Item
from serving.stuck import Entry, Fix, LLMUnavailable, StuckState
from serving.stuck_llm import PROMPTS, StuckModelGateway
from test_stuck_flow import FakeGateway as FlowFakeGateway, Session, small_map

CONCEPT_MAP = load_concept_map()
STACK = next(c for c in CONCEPT_MAP.concepts if c.id == 'stack')
ENTRY = Entry(kind='roadmap', concept='queue', return_to='/learn?concept=queue')
PASSAGES = [Passage(f'ods:s.tex#3.{n}:0', 'ods', f's.tex#3.{n}', f'Stacks {n}', f'Passage {n} says a stack is LIFO.', 1 - n / 10)
            for n in range(1, 8)]
SCENE = {'kind': 'stack', 'ops': [{'op': 'push', 'value': 1}, {'op': 'pop'}], 'steps': ['1을 넣는다.', '1을 꺼낸다.']}


## @brief 점수 순 문단 중 exclude에 없는 상위 k개를 돌려주는 가짜 검색기; 받은 exclude를 기록함
class FakeRetriever:
    def __init__(self, passages=PASSAGES):
        self.result, self.excludes = list(passages), []

    def passages(self, concepts, query, k=3, exclude=frozenset()):
        self.excludes.append(set(exclude))
        return [p for p in self.result if p.id not in exclude][:k]


def reply(content):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=content))])


## @brief OpenAI 가짜 클라이언트; 시스템 지시로 역할을 알아내 정상 응답을 만들고 받은 입력을 역할별로 기록
# - ground: 근거 키 앞의 두 개로 문장 두 개, judge: 근거 원문 앞부분을 근거 구절로 supported
# - grade: 루브릭 항목마다 grade(user)가 돌려준 (evidence, met)
# - overrides: 역할 → 응답 함수(기본 응답 대신)
class FakeClient:
    def __init__(self, grade=None, **overrides):
        self.inputs = {}
        self.grade = grade or (lambda user: [(user['answer'][:4], True)] * len(user['rubric']))
        self.overrides = overrides
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.models = SimpleNamespace(list=lambda timeout: SimpleNamespace(data=[SimpleNamespace(id='served-model')]))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def create(self, **kwargs):
        role = next(r for r, prompt in PROMPTS.items() if kwargs['messages'][0]['content'] == prompt)
        user = json.loads(kwargs['messages'][1]['content'])
        self.inputs.setdefault(role, []).append(user)
        if role in self.overrides:
            return reply(self.overrides[role](user))
        keys = [s['key'] for s in user.get('sources', [])]
        return reply(json.dumps({
            'identify': lambda: {'concept': 'none'},
            'animate': lambda: {'scene': None},
            'ground': lambda: {'sentences': [{'text': f'근거 {k}의 설명이다.', 'sources': [k]} for k in keys[:2]]},
            'judge': lambda: {'verdict': 'supported', 'evidence': user['source'][:12]},
            'grade_explanation': lambda: {'criteria': [{'evidence': e, 'met': m} for e, m in self.grade(user)]},
        }[role](), ensure_ascii=False))


def gateway(client=None, retriever=None, lock=None, concept_map=CONCEPT_MAP):
    client = client or FakeClient()
    return HarnessStuckGateway(concept_map, lock or threading.Lock(), retriever or FakeRetriever(),
                               client_factory=lambda: client)


class SwitchTests(unittest.TestCase):
    # 검증 목적(Review Focus 2): 스위치가 꺼져 있으면 /stuck/step이 기존 StuckModelGateway를 그대로 쓰고, 켜면 하네스판을 씀
    # - 두 경우 모두 채팅·커리큘럼과 같은 BUSY 잠금을 넘겨, 모델 호출마다 잠금을 잡는 기존 방식이 유지되는지 함께 확인
    @patch('serving.api.build_retriever', lambda: FakeRetriever())
    def test_stuck_uses_model_gateway_when_switch_off(self):
        client = TestClient(app)
        body = {'input': {'type': 'start', 'entry': {'kind': 'roadmap', 'concept': 'stack', 'return_to': '/learn'}}}
        for switch, expected in (('', StuckModelGateway), ('1', HarnessStuckGateway)):
            with self.subTest(switch=switch), patch.dict(os.environ, {'CS_HARNESS': switch}), \
                    patch('serving.api.stuck_step', return_value=None) as step:
                step.side_effect = lambda state, data, gw, *rest: StuckState(entry=data.entry, stage='complete')
                self.assertEqual(client.post('/stuck/step', json=body).status_code, 200)
                gw = step.call_args.args[2]
                self.assertIs(type(gw), expected)
                self.assertIs(gw.lock, BUSY)


class ExplainTests(unittest.TestCase):
    # 검증 목적: 1차 보충은 개념 문단 상위 3개를 근거(p1~p3)로 생성하고, 판정을 통과한 문장에 각주와 교재 출처가 붙는지 확인
    # - 기존 explain의 예시·비유 지시(EXPLAIN_APPROACHES)는 하네스 생성 입력에 넣지 않음(사용자 결정)
    def test_explain_first_attempt_cites_passages(self):
        client, retriever = FakeClient(), FakeRetriever()
        text = gateway(client, retriever).explain(STACK, 1, ENTRY)
        self.assertEqual(retriever.excludes, [set()])
        self.assertEqual([s['key'] for s in client.inputs['ground'][0]['sources']], ['p1', 'p2', 'p3'])
        self.assertNotIn('approach', json.dumps(client.inputs['ground'][0], ensure_ascii=False))
        self.assertIn('근거 p1의 설명이다.[^p1]', text)
        self.assertIn('[^p1]: > Passage 1 says', text)
        self.assertIn('**출처**', text)
        self.assertNotIn('확인 필요', text)

    # 검증 목적: 2차 보충에 장면이 있으면 문단 검색 없이 코드가 계산한 장면 단계(steps)를 근거 a1..aN으로 쓰는지 확인
    # - 각주 정의는 애니메이션 단계 문장 그대로이고, 교재 출처 블록은 붙지 않음(근거가 교재가 아니므로)
    def test_explain_second_attempt_uses_scene_steps_as_sources(self):
        client, retriever = FakeClient(), FakeRetriever()
        gw = gateway(client, retriever)
        gw.explain(STACK, 1, ENTRY)
        text = gw.explain(STACK, 2, ENTRY, SCENE)
        self.assertEqual(len(retriever.excludes), 1)
        self.assertEqual(client.inputs['ground'][1]['sources'], [{'key': 'a1', 'text': '1을 넣는다.'},
                                                                 {'key': 'a2', 'text': '1을 꺼낸다.'}])
        self.assertIn('근거 a1의 설명이다.[^a1]', text)
        self.assertIn('[^a1]: 애니메이션 1단계: 1을 넣는다.', text)
        self.assertNotIn('**출처**', text)

    # 검증 목적: 장면이 없는 2차와 3차 보충은 앞 시도에서 쓴 문단을 빼고 다음 상위 문단을 쓰는지 확인
    # - 장면으로 보충한 2차는 문단을 쓰지 않았으므로, 그 뒤 3차는 1차 문단만 뺌
    def test_explain_third_attempt_uses_unused_passages(self):
        ids = [p.id for p in PASSAGES]
        retriever = FakeRetriever()
        gw = gateway(retriever=retriever)
        for attempt in (1, 2, 3):
            gw.explain(STACK, attempt, ENTRY)
        self.assertEqual(retriever.excludes, [set(), set(ids[:3]), set(ids[:6])])
        retriever.excludes.clear()
        for attempt, scene in ((1, None), (2, SCENE), (3, None)):
            gw.explain(STACK, attempt, ENTRY, scene)
        self.assertEqual(retriever.excludes, [set(), set(ids[:3])])

    # 검증 목적: 쓴 문단 기억은 막힌 곳(entry)과 개념마다 따로이고, 1차 보충에서 새로 시작하는지 확인
    # - 다른 막힘 세션이나 다른 개념의 1차 보충이 앞 기록을 지우면 3차가 2차 문단을 다시 씀
    def test_used_passages_are_per_entry_and_concept(self):
        ids = [p.id for p in PASSAGES]
        queue = next(c for c in CONCEPT_MAP.concepts if c.id == 'queue')
        retriever = FakeRetriever()
        gw = gateway(retriever=retriever)
        gw.explain(STACK, 1, ENTRY)
        gw.explain(STACK, 2, ENTRY)
        gw.explain(STACK, 1, Entry(kind='excerpt', excerpt='큐가 뭔가요', return_to='/chat'))
        gw.explain(queue, 1, ENTRY)
        gw.explain(STACK, 3, ENTRY)
        self.assertEqual(retriever.excludes[-1], set(ids[:6]))
        gw.explain(STACK, 1, ENTRY)
        self.assertEqual(retriever.excludes[-1], set())

    # 검증 목적: 1차 보충 없이 시작해(1단계 개념은 설명 차례 2부터) 쓴 문단 기억이 이어져도, 남은 문단이 없으면
    # 기억을 비우고 상위 문단부터 다시 써서 근거 없음 문구로 끝나지 않는지 확인
    def test_exhausted_passages_start_over(self):
        retriever = FakeRetriever()
        gw = gateway(retriever=retriever)
        for attempt in (2, 3, 4) * len(PASSAGES):
            self.assertNotEqual(gw.explain(STACK, attempt, ENTRY), NO_SOURCE_TEXT)
        # 첫 호출 뒤에도 빈 exclude로 다시 검색한 적이 있어야 함(기억을 비우고 처음부터)
        self.assertIn(set(), retriever.excludes[1:])

    # 검증 목적(Review Focus 5): 개념에 문단이 없으면(빈칸 개념·색인 없음) 모델을 부르지 않고 근거 없음 문구를 돌려줌
    def test_explain_no_passage_returns_no_source_text(self):
        client = FakeClient()
        self.assertEqual(gateway(client, FakeRetriever([])).explain(STACK, 1, ENTRY), NO_SOURCE_TEXT)
        self.assertNotIn('ground', client.inputs)

    # 검증 목적: 임베딩 서버 오류는 500 대신 LLMUnavailable(→ 503, 상태 보존)이 되어 학습자가 다시 시도할 수 있음
    def test_search_server_error_is_unavailable(self):
        class DownRetriever(FakeRetriever):
            def passages(self, *args, **kwargs):
                raise APIConnectionError(request=httpx.Request('POST', 'http://127.0.0.1:20310/v1/embeddings'))

        with self.assertRaises(LLMUnavailable):
            gateway(retriever=DownRetriever()).explain(STACK, 1, ENTRY)

    # 검증 목적: 생성 호출이 끝내 실패하면 교재 원문 대체 답, 장면 근거였으면 장면 단계 대체 답을 돌려줌(모델 문장 없음)
    def test_generation_failure_returns_fallback(self):
        client = FakeClient(ground=lambda user: 'not json')
        text = gateway(client).explain(STACK, 1, ENTRY)
        self.assertTrue(text.startswith(FAILED_TEXT))
        self.assertIn('> Passage 1 says a stack is LIFO.', text)
        scene_text = gateway(client).explain(STACK, 2, ENTRY, SCENE)
        self.assertEqual(scene_text, f'{SCENE_FAILED_TEXT}\n\n1. 1을 넣는다.\n2. 1을 꺼낸다.')

    # 검증 목적: 판정을 통과하지 못한 문장이 절반을 넘으면 모델 문장 대신 교재 원문 대체 답으로 바꿈(Task 6 규칙)
    def test_majority_unsupported_returns_fallback(self):
        client = FakeClient(judge=lambda user: json.dumps({'verdict': 'unsupported', 'evidence': ''}),
                            repair=lambda user: json.dumps({'text': '고친 문장이다.', 'sources': [user['source_key']]}))
        self.assertTrue(gateway(client).explain(STACK, 1, ENTRY).startswith(FAILED_TEXT))

    # 검증 목적(잠금 판단): 단계 시작 때 다른 요청이 모델을 쓰고 있으면 기존처럼 LLMUnavailable(→ 503, 상태 보존)
    # - 채팅은 요청 전체 동안 BUSY를 잡으므로, 시작 시 한 번 확인해 대체 답 대신 재시도 안내를 보여 줌
    def test_busy_at_start_is_unavailable(self):
        lock = threading.Lock()
        lock.acquire()
        with self.assertRaises(LLMUnavailable):
            gateway(lock=lock).explain(STACK, 1, ENTRY)

    # 검증 목적(잠금 판단): 생성 뒤 판정 사이에 다른 요청이 끼어들어 판정 호출이 잠금을 못 잡으면,
    # 그 문장은 예외로 멈추지 않고 화면에서 빠짐(Task 6 규칙: 판정 실패는 needs_check, needs_check는 보여 주지 않음)
    def test_interleaved_request_hides_sentence(self):
        class InterleavedLock:
            """시작 확인·생성 다음의 첫 판정에서만 다른 요청이 잠금을 잡고 있는 상황을 흉내 냄."""

            def __init__(self):
                self.count = 0

            def acquire(self, blocking=True):
                self.count += 1
                return self.count != 3

            def release(self):
                pass

        text = gateway(lock=InterleavedLock()).explain(STACK, 1, ENTRY)
        self.assertNotIn('근거 p1의 설명이다.', text)
        self.assertIn('근거 p2의 설명이다.[^p2]', text)


RUBRIC_ITEM = Item.model_validate({'id': 'stack-e1', 'concept': 'stack', 'purpose': 'explanation', 'objective': 0,
                                   'format': 'explanation', 'question': '스택을 설명하라', 'rationale': '근거',
                                   'rubric': ['후입선출', '맨 위']})


class GradeTests(unittest.TestCase):
    # 검증 목적: 모델이 충족이라고 해도 근거 구절이 학생 답에 (공백·문장부호를 지운 뒤) 그대로 없으면 미충족으로 바꿈
    def test_grade_evidence_not_in_answer_flips_to_false(self):
        client = FakeClient(grade=lambda user: [('스택은 후입선출', True), ('맨 위에서만 꺼낸다', True)])
        met = gateway(client).grade_explanation(RUBRIC_ITEM, '스택은, 후입선출 구조다.')
        self.assertEqual(met, [True, False])

    # 검증 목적: 모델이 미충족이라 한 항목은 근거가 답에 있어도 충족으로 바꾸지 않고, 빈 답은 모델 없이 모두 미충족
    def test_grade_keeps_model_false_and_empty_answer(self):
        client = FakeClient(grade=lambda user: [('스택은', False), ('스택은', True)])
        self.assertEqual(gateway(client).grade_explanation(RUBRIC_ITEM, '스택은 구조다'), [False, True])
        self.assertEqual(gateway(client).grade_explanation(RUBRIC_ITEM, '  '), [False, False])
        self.assertEqual(len(client.inputs['grade_explanation']), 1)


class SummaryTests(unittest.TestCase):
    def state(self, target, fixes):
        return StuckState(entry=Entry(kind='roadmap', concept=target, return_to='/learn'), stage='return', target=target,
                          fixes=[Fix(concept=c, attempts=1, outcome=o) for c, o in fixes])

    # 검증 목적: 복귀 요약을 모델 없이 템플릿으로 조립하고, 개념 제목의 받침에 맞는 조사(을/를, 은/는)를 고르는지 확인
    def test_summarize_is_template_with_particles(self):
        client = FakeClient()
        text = gateway(client).summarize(self.state('priority-queue', [
            ('heap', 'passed'), ('queue', 'unresolved'), ('priority-queue', 'passed')]), CONCEPT_MAP)
        self.assertEqual(text, '우선순위 큐를 이해하려고 힙, 큐를 다시 설명했어요. 힙, 우선순위 큐는 이해 확인을 통과했어요. '
                               '큐는 아직 이해 확인을 통과하지 못했어요. 다시 살펴보세요.')
        text = gateway(client).summarize(self.state('stack', [('adt', 'passed'), ('stack', 'unchecked')]), CONCEPT_MAP)
        self.assertEqual(text, '스택을 이해하려고 추상 자료형과 구현을 다시 설명했어요. 추상 자료형과 구현은 이해 확인을 통과했어요. '
                               '스택은 확인 문항이 없어 이해 확인을 하지 않았어요.')
        text = gateway(client).summarize(self.state('heap', [('heap', 'passed')]), CONCEPT_MAP)
        self.assertEqual(text, '힙을 다시 설명했어요. 힙은 이해 확인을 통과했어요.')
        self.assertEqual(client.inputs, {})

    # 검증 목적: 영문·숫자·괄호로 끝나는 제목도 읽는 소리의 받침으로 조사를 고르는지 확인
    # - 영문자는 이름 소리(L 엘, M 엠, N 엔, R 알만 받침), 숫자는 한자어 읽기(0 영, 1 일, 3 삼, 6 육, 7 칠, 8 팔만 받침)
    # - 괄호 설명은 건너뛰고 앞 낱말로 고름: "최단 경로(다익스트라)를"
    def test_particles_for_latin_digits_and_parentheses(self):
        cases = {('트랜잭션과 ACID', '을'): '트랜잭션과 ACID를', ('SQL', '은'): 'SQL은', ('IPv4', '은'): 'IPv4는',
                 ('2의 보수 1', '을'): '2의 보수 1을', ('최단 경로(다익스트라)', '을'): '최단 경로(다익스트라)를',
                 ('계층 모델(OSI·TCP/IP)', '은'): '계층 모델(OSI·TCP/IP)은', ('큐', '을'): '큐를', ('힙', '을'): '힙을',
                 ('스택', '을'): '스택을'}
        for (word, particle), expected in cases.items():
            with self.subTest(word=word):
                self.assertEqual(with_particle(word, particle), expected)


## @brief test_stuck_flow 시나리오를 돌리기 위한 하네스판; Session의 요청당 호출 상한 확인용으로 역할 호출을 기록
class RecordingHarness(HarnessStuckGateway):
    def __init__(self, rubric_pass=True):
        grade = lambda user: [(user['answer'][:4], rubric_pass)] * len(user['rubric'])  # noqa: E731
        super().__init__(small_map(), threading.Lock(), FakeRetriever(), client_factory=lambda: FakeClient(grade))
        self.calls = []

    def identify(self, *args):
        self.calls.append('identify')
        return super().identify(*args)

    def explain(self, *args):
        self.calls.append('explain')
        return super().explain(*args)

    def grade_explanation(self, *args):
        self.calls.append('grade')
        return super().grade_explanation(*args)

    def summarize(self, *args):
        self.calls.append('summarize')
        return super().summarize(*args)

    def choose_animation(self, *args, **kwargs):
        self.calls.append('animate')
        return super().choose_animation(*args, **kwargs)


class FlowParityTests(unittest.TestCase):
    ## @brief 시나리오를 실행하며 요청마다 (단계, 화면 종류, 보충 결과)를 모아 돌려줌
    def trace(self, gateway, scenario):
        session = Session(self, gateway=gateway)
        trace = []
        original = session.send

        def send(data, start=False):
            state = original(data, start)
            trace.append((state.stage, state.view.get('kind'), tuple((f.concept, f.attempts, f.outcome) for f in state.fixes)))
            return state

        session.send = send
        scenario(session)
        return trace

    # 검증 목적: 같은 시나리오(전체 통과·루브릭 미통과·재보충 상한)를 기존 가짜 게이트웨이와 하네스판으로 돌렸을 때
    # 상태 전이가 같은지 확인. 하네스판은 Gateway 계약(explain은 문자열)을 지켜 엔진·Spring·화면을 바꾸지 않아야 함
    def test_stuck_flow_transitions_match_harness_gateway(self):
        def full(session):
            session.diagnose(known={'beta', 'delta'})
            session.remediate()

        def unresolved(session):
            session.send({'type': 'opt_out'})
            session.remediate(check_ok=False)

        for name, scenario, rubric_pass in (('full', full, True), ('rubric_fail', full, False),
                                            ('unresolved', unresolved, True)):
            with self.subTest(name):
                expected = self.trace(FlowFakeGateway(rubric_pass=rubric_pass), scenario)
                self.assertEqual(self.trace(RecordingHarness(rubric_pass), scenario), expected)
                self.assertEqual(expected[-1][0], 'complete')


if __name__ == '__main__':
    unittest.main()
