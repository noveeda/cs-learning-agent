# 파일 역할: 막힘 흐름 LLM 역할(vLLM Gateway)의 요청 형태·출력 검증·오류 변환·모델 잠금과, 가짜 모델로 끝까지 가는 흐름을 검증
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from openai import APIConnectionError, APITimeoutError

from serving.api import BUSY, app
from serving.concept_map import load_concept_map
from serving.item_bank import load_item_bank
from serving.stuck import Entry, LLMUnavailable, StepInput, step
from serving.stuck_llm import (EXPLAIN_APPROACHES, MODEL_LIST_TIMEOUT, PROMPTS, ROLE_SETTINGS, STEP_BUDGET_SECONDS,
                               StuckModelGateway)

MAP = load_concept_map()
ITEMS = load_item_bank()
REQUEST = httpx.Request('POST', 'http://127.0.0.1:21019/v1/chat/completions')


def reply(content, finish='stop'):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))])


# 역할: OpenAI 클라이언트 가짜 구현; 요청을 기록하고 시스템 지시로 역할을 알아내 역할별 응답 함수에 넘김
# - 응답 함수가 예외 객체를 돌려주면 그대로 던져 연결 실패·시간 초과를 흉내 냄
class FakeClient:
    def __init__(self, **responders):
        self.requests, self.responders, self.list_calls = [], responders, 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.models = SimpleNamespace(list=self.list_models)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def list_models(self, timeout):
        self.list_calls += 1
        return SimpleNamespace(data=[SimpleNamespace(id='served-model')])

    def create(self, **kwargs):
        role = next(r for r, prompt in PROMPTS.items() if kwargs['messages'][0]['content'] == prompt)
        self.requests.append((role, kwargs))
        result = self.responders[role](json.loads(kwargs['messages'][1]['content']), kwargs)
        if isinstance(result, Exception):
            raise result
        return result if isinstance(result, SimpleNamespace) else reply(result)


# 역할: 가짜 모델이 정상 응답만 하는 기본 역할 응답; identified로 개념 특정 결과를 정함
def happy_client(identified='stack'):
    return FakeClient(
        identify=lambda user, kw: json.dumps({'concept': identified}),
        explain=lambda user, kw: f"**{user['concept']['title']}** 보충 설명",
        grade_explanation=lambda user, kw: json.dumps({'criteria': [{'evidence': '근거 부분', 'met': True} for _ in user['rubric']]}),
        summarize=lambda user, kw: '복귀 요약',
    )


def gateway(client, lock=None):
    return StuckModelGateway(MAP, lock or threading.Lock(), client_factory=lambda: client)


def item(item_id):
    return next(i for i in ITEMS if i.id == item_id)


class RequestShapeTests(unittest.TestCase):
    # 검증 목적: 개념 특정이 지도 ID + "none"만 고르게 schema를 걸고, 역할 설정표의 샘플링·분량·시간·추론 끔을 그대로 보내는지 확인
    def test_identify_request_uses_map_enum_and_role_settings(self):
        client = happy_client()
        with patch.dict('os.environ', {'VLLM_MODEL': 'env-model'}):
            self.assertEqual(gateway(client).identify('스택에서 pop을 모르겠어요', MAP), 'stack')
        role, kw = client.requests[0]
        settings = ROLE_SETTINGS['identify']
        schema = kw['response_format']['json_schema']['schema']
        self.assertEqual(schema['properties']['concept']['enum'], [c.id for c in MAP.concepts] + ['none'])
        self.assertEqual((kw['model'], kw['temperature'], kw['max_tokens'], kw['timeout']),
                         ('env-model', settings.temperature, settings.max_tokens, settings.timeout))
        self.assertEqual(kw['extra_body'], {'chat_template_kwargs': {'enable_thinking': False}})
        self.assertNotIn('top_p', kw)
        self.assertEqual(client.list_calls, 0)

    # 검증 목적: 모델 선택 결과(docs/model-selection.md 7절)의 역할별 샘플링·분량·시간이 실제 요청 인자로 나가는지 확인
    # - 판정 역할(①③)은 결정적으로 temperature 0, 생성 역할(②④)은 Gemma 4 권장 샘플링(top_k는 vLLM 확장 인자라 extra_body)
    def test_role_requests_follow_model_selection(self):
        expected = {'identify': (0.0, None, None, 64, 30), 'explain': (1.0, 0.95, 64, 1536, 60),
                    'grade_explanation': (0.0, None, None, 1024, 30), 'summarize': (1.0, 0.95, 64, 512, 30)}
        client = happy_client()
        state = run_session(self, {'kind': 'excerpt', 'excerpt': '스택의 pop이 이해가 안 돼요', 'return_to': '/x'}, client)
        self.assertEqual(state.stage, 'complete')
        sent = {role: kw for role, kw in client.requests}
        self.assertEqual(set(sent), set(expected))
        for role, (temperature, top_p, top_k, max_tokens, timeout) in expected.items():
            kw = sent[role]
            self.assertEqual((kw['temperature'], kw.get('top_p'), kw['extra_body'].get('top_k'), kw['max_tokens'], kw['timeout']),
                             (temperature, top_p, top_k, max_tokens, timeout), role)
            self.assertEqual(kw['extra_body']['chat_template_kwargs'], {'enable_thinking': False}, role)

    # 검증 목적: 지도에 맞는 개념이 없다는 "none"은 None(범위 밖)으로 바뀌고, 모델 이름이 없으면 서버의 첫 모델을 쓰는지 확인
    def test_identify_none_and_served_model_fallback(self):
        client = happy_client(identified='none')
        with patch.dict('os.environ', {}, clear=True):
            self.assertIsNone(gateway(client).identify('오늘 점심 뭐 먹지', MAP))
        self.assertEqual(client.requests[0][1]['model'], 'served-model')

    # 검증 목적: 보충 설명 요청에 개념 목표·선수 개념·원래 맥락이 들어가고, 재보충이면 설명 방식이 바뀌며, 문항 정답은 넘기지 않는지 확인
    def test_explain_request_content(self):
        client = happy_client()
        concept = next(c for c in MAP.concepts if c.id == 'adt')
        entry = Entry(kind='excerpt', excerpt='스택이 왜 추상 자료형이죠?', return_to='/learn')
        g = gateway(client)
        self.assertEqual(g.explain(concept, 1, entry), '**추상 자료형과 구현** 보충 설명')
        g.explain(concept, 2, entry)
        first, second = (json.loads(kw['messages'][1]['content']) for _, kw in client.requests)
        self.assertEqual(first['concept']['objectives'], concept.objectives)
        self.assertEqual(first['stuck_at'], {'excerpt': '스택이 왜 추상 자료형이죠?'})
        self.assertEqual((first['approach'], second['approach']), (EXPLAIN_APPROACHES[1], EXPLAIN_APPROACHES[2]))
        self.assertNotIn('response_format', client.requests[0][1])
        sent = json.dumps([kw['messages'] for _, kw in client.requests], ensure_ascii=False)
        for i in ITEMS:
            if i.concept == 'adt':
                self.assertNotIn(i.rationale, sent)

    # 검증 목적: 서술 채점이 루브릭 항목 수로 길이를 고정한 criteria 배열 schema를 걸고, 결과를 루브릭 순서의 list[bool]로 돌려주는지 확인
    def test_grade_explanation_keeps_rubric_order(self):
        target = item('stack-e1')
        client = FakeClient(grade_explanation=lambda user, kw: json.dumps(
            {'criteria': [{'evidence': '', 'met': i != 1} for i, _ in enumerate(user['rubric'])]}))
        self.assertEqual(gateway(client).grade_explanation(target, '나중에 호출한 함수가 먼저 끝난다'), [True, False, True])
        _, kw = client.requests[0]
        criteria = kw['response_format']['json_schema']['schema']['properties']['criteria']
        self.assertEqual((criteria['minItems'], criteria['maxItems']), (3, 3))
        self.assertEqual(criteria['items']['properties']['evidence'], {'type': 'string', 'maxLength': 200})
        user = json.loads(kw['messages'][1]['content'])
        self.assertEqual((user['rubric'], user['reference']), (target.rubric, target.rationale))

    # 검증 목적: 빈 답은 모델을 부르지 않고 모든 항목 미충족으로 판정하는지 확인
    def test_blank_explanation_fails_without_model_call(self):
        client = FakeClient()
        self.assertEqual(gateway(client).grade_explanation(item('stack-e1'), '   '), [False, False, False])
        self.assertEqual(client.requests, [])

    # 검증 목적: 역할마다 시도 횟수 × 대기 시간 + 모델 조회 시간이 Spring 대기(320초) 안의 예산을 넘지 않는지 확인
    def test_role_settings_fit_spring_timeout(self):
        self.assertLess(STEP_BUDGET_SECONDS, 320)
        for role, settings in ROLE_SETTINGS.items():
            self.assertLessEqual(settings.attempts * settings.timeout + MODEL_LIST_TIMEOUT, STEP_BUDGET_SECONDS, role)
        self.assertEqual(set(ROLE_SETTINGS), set(PROMPTS))


class FailureTests(unittest.TestCase):
    # 검증 목적: 연결 실패·시간 초과는 설정한 횟수만큼 다시 시도한 뒤 LLMUnavailable로 바뀌는지 확인
    def test_connection_and_timeout_errors_become_unavailable(self):
        for error in (APIConnectionError(request=REQUEST), APITimeoutError(request=REQUEST)):
            client = FakeClient(identify=lambda user, kw, e=error: e)
            with self.assertRaises(LLMUnavailable):
                gateway(client).identify('스택', MAP)
            self.assertEqual(len(client.requests), ROLE_SETTINGS['identify'].attempts)

    # 검증 목적: 잘린 출력·빈 출력·응답 구조 누락(choices 비었거나 없음, message 없음)·형식이 틀린 JSON·지도 밖 ID·항목 수가 다른 채점은 모두 LLMUnavailable인지 확인
    def test_invalid_outputs_become_unavailable(self):
        cases = [
            ('identify', lambda user, kw: SimpleNamespace(choices=[])),
            ('identify', lambda user, kw: SimpleNamespace(choices=None)),
            ('identify', lambda user, kw: SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=None)])),
            ('identify', lambda user, kw: reply(None)),
            ('identify', lambda user, kw: reply('{"concept": "stack"}', finish='length')),
            ('identify', lambda user, kw: ''),
            ('identify', lambda user, kw: '{"concept": '),
            ('identify', lambda user, kw: '{"concept": "not-in-map"}'),
            ('identify', lambda user, kw: '["stack"]'),
            ('grade_explanation', lambda user, kw: json.dumps({'criteria': [{'evidence': '', 'met': True}]})),
            ('grade_explanation', lambda user, kw: json.dumps({'criteria': [{'met': 'yes'} for _ in user['rubric']]})),
            ('grade_explanation', lambda user, kw: json.dumps([{'evidence': '', 'met': True}] * 3)),
            ('grade_explanation', lambda user, kw: reply('{"criteria": [{"evidence": "', finish='length')),
        ]
        for role, responder in cases:
            g = gateway(FakeClient(**{role: responder}))
            with self.assertRaises(LLMUnavailable, msg=role):
                if role == 'identify':
                    g.identify('스택', MAP)
                else:
                    g.grade_explanation(item('stack-e1'), '설명')

    # 검증 목적: 첫 응답이 형식 오류여도 재시도에서 올바른 응답을 받으면 성공하는지 확인
    def test_retry_recovers_from_one_bad_output(self):
        outputs = iter(['not json', '{"concept": "queue"}'])
        client = FakeClient(identify=lambda user, kw: next(outputs))
        self.assertEqual(gateway(client).identify('큐', MAP), 'queue')
        self.assertEqual(len(client.requests), 2)

    # 검증 목적: 다른 모델 요청이 잠금을 잡고 있으면 모델을 부르지 않고 503용 오류로 끝나며, 성공·실패 뒤에는 잠금을 풀어 주는지 확인
    def test_model_lock(self):
        lock, client = threading.Lock(), happy_client()
        lock.acquire()
        with self.assertRaisesRegex(LLMUnavailable, '다른 요청'):
            gateway(client, lock).identify('스택', MAP)
        self.assertEqual(client.requests, [])
        lock.release()
        gateway(client, lock).identify('스택', MAP)
        with self.assertRaises(LLMUnavailable):
            gateway(FakeClient(identify=lambda user, kw: ''), lock).identify('스택', MAP)
        self.assertFalse(lock.locked())


# 역할: 실제 지도·문항 은행과 가짜 모델로 세션을 끝까지 진행; 진단은 모두 틀리고 확인 문항은 정답 키로 맞힘
def run_session(test, entry, client):
    g, state, calls = gateway(client), None, 0
    data = {'type': 'start', 'entry': entry}
    while True:
        before = len(client.requests)
        state = step(state, StepInput.model_validate(data), g, MAP, ITEMS, 1.0)
        test.assertLessEqual(len(client.requests) - before, 1, '요청당 LLM 호출은 1회 이하')
        calls += 1
        if state.stage in ('complete', 'out_of_scope') or calls > 200:
            return state
        if state.stage == 'diagnose':
            data = {'type': 'answer', 'item': state.pending, 'answer': '모름'}
        elif state.stage == 'check':
            data = {'type': 'answer', 'item': state.pending, 'answer': item(state.pending).answer}
        elif state.stage == 'explain':
            data = {'type': 'answer', 'item': state.pending, 'answer': '자기 말 설명'}
        else:
            data = {'type': 'continue'}


class FlowTests(unittest.TestCase):
    # 검증 목적: 로드맵 진입(stack)이 가짜 모델로 진단·보충·확인·복귀를 거쳐 complete까지 가는지 확인
    def test_roadmap_entry_completes(self):
        client = happy_client()
        state = run_session(self, {'kind': 'roadmap', 'concept': 'stack', 'return_to': '/#/roadmap?concept=stack'}, client)
        self.assertEqual(state.stage, 'complete')
        self.assertEqual((state.view['text'], state.view['return_to']), ('복귀 요약', '/#/roadmap?concept=stack'))
        self.assertTrue(all(f.outcome == 'passed' for f in state.fixes))
        self.assertEqual({r for r, _ in client.requests}, {'explain', 'grade_explanation', 'summarize'})

    # 검증 목적: 붙여넣기 진입이 개념 특정으로 시작해 complete까지 가는지 확인
    def test_excerpt_entry_completes(self):
        client = happy_client(identified='queue')
        state = run_session(self, {'kind': 'excerpt', 'excerpt': '원형 큐에서 rear가 왜 0으로 가요?', 'return_to': '/#/roadmap'}, client)
        self.assertEqual((state.stage, state.target), ('complete', 'queue'))
        self.assertEqual(client.requests[0][0], 'identify')

    # 검증 목적: 지도에는 있지만 문항 은행이 없는 개념(MVP 지원 밖)과 지도 밖("none")은 범위 밖으로 끝나는지 확인
    def test_unsupported_or_unknown_concept_is_out_of_scope(self):
        for identified in ('tree', 'none'):
            state = run_session(self, {'kind': 'excerpt', 'excerpt': '막힌 문장', 'return_to': '/#/roadmap'}, happy_client(identified))
            self.assertEqual(state.stage, 'out_of_scope', identified)

    # 검증 목적: 모델 실패 시 원래 상태가 그대로 남는지(Spring이 마지막 저장 상태로 재시도 가능) 확인
    def test_model_failure_keeps_state(self):
        start = step(None, StepInput(type='start', entry={'kind': 'roadmap', 'concept': 'stack', 'return_to': '/x'}),
                     gateway(FakeClient()), MAP, ITEMS, 1.0)
        opted = step(start, StepInput(type='opt_out'), gateway(FakeClient()), MAP, ITEMS, 1.0)
        before = opted.model_dump()
        failing = FakeClient(explain=lambda user, kw: APIConnectionError(request=REQUEST))
        with self.assertRaises(LLMUnavailable):
            step(opted, StepInput(type='continue'), gateway(failing), MAP, ITEMS, 1.0)
        self.assertEqual(opted.model_dump(), before)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        start = self.client.post('/stuck/step', json={'input': {'type': 'start', 'entry': {
            'kind': 'roadmap', 'concept': 'stack', 'return_to': '/x'}}}).json()
        self.opted = self.client.post('/stuck/step', json={'state': start, 'input': {'type': 'opt_out'}}).json()

    # 검증 목적: /stuck/step이 실제 Gateway를 써서 모델 응답으로 보충 화면을 만드는지 확인
    def test_step_uses_model_gateway(self):
        with patch('serving.stuck_llm._default_client', happy_client):
            response = self.client.post('/stuck/step', json={'state': self.opted, 'input': {'type': 'continue'}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['view']['text'], '**스택** 보충 설명')

    # 검증 목적: 모델 실패와 모델 잠금 사용 중은 모두 503과 한국어 안내로 끝나는지 확인
    def test_model_failure_and_busy_are_503(self):
        failing = lambda: FakeClient(explain=lambda user, kw: APITimeoutError(request=REQUEST))
        with patch('serving.stuck_llm._default_client', failing):
            response = self.client.post('/stuck/step', json={'state': self.opted, 'input': {'type': 'continue'}})
        self.assertEqual(response.status_code, 503)
        self.assertIn('다시 시도', response.json()['detail'])
        with BUSY, patch('serving.stuck_llm._default_client', happy_client):
            busy = self.client.post('/stuck/step', json={'state': self.opted, 'input': {'type': 'continue'}})
        self.assertEqual((busy.status_code, busy.json()['detail']), (503, '다른 요청을 처리 중입니다. 잠시 후 다시 시도해 주세요.'))


if __name__ == '__main__':
    unittest.main()
