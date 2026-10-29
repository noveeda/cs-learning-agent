## @file
# @brief 막힘 흐름 LLM 역할(vLLM Gateway)의 요청 형태·출력 검증·오류 변환·모델 잠금·장면 선택과, 가짜 모델로 끝까지 가는 흐름을 검증
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
from serving.animation import SCENE_FORMATS, SIMULATORS, simulate
from serving.animation.model import AnimationRequestModel
from serving.stuck_llm import (COMMON, EXPLAIN_APPROACHES, MODEL_LIST_TIMEOUT, PROMPTS, ROLE_SETTINGS, STEP_BUDGET_SECONDS,
                               StuckModelGateway)

MAP = load_concept_map()
ITEMS = load_item_bank()
REQUEST = httpx.Request('POST', 'http://127.0.0.1:21019/v1/chat/completions')


def reply(content, finish='stop'):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))])


## @brief OpenAI 클라이언트 가짜 구현; 요청을 기록하고 시스템 지시로 역할을 알아내 역할별 응답 함수에 넘김
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


STACK_SCENE = {'kind': 'stack', 'ops': [{'op': 'push', 'value': 1}, {'op': 'push', 'value': 2}, {'op': 'pop'}]}


## @brief 가짜 모델이 정상 응답만 하는 기본 역할 응답; identified로 개념 특정 결과를 정함
# - 장면 선택은 stack이 허용되면 stack 장면, 아니면 "장면 없음"(null)을 고름
def happy_client(identified='stack'):
    return FakeClient(
        identify=lambda user, kw: json.dumps({'concept': identified}),
        explain=lambda user, kw: f"**{user['concept']['title']}** 보충 설명",
        grade_explanation=lambda user, kw: json.dumps({'criteria': [{'evidence': '근거 부분', 'met': True} for _ in user['rubric']]}),
        summarize=lambda user, kw: '복귀 요약',
        animate=lambda user, kw: json.dumps({'scene': STACK_SCENE if 'stack' in user['scenes'] else None}),
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
    # - ② explain은 사실 오류를 줄이려고 temperature만 0.7로 낮춤(docs/model-selection.md 7절)
    def test_role_requests_follow_model_selection(self):
        expected = {'identify': (0.0, None, None, 64, 30), 'explain': (0.7, 0.95, 64, 1536, 60),
                    'grade_explanation': (0.0, None, None, 1024, 30), 'summarize': (1.0, 0.95, 64, 512, 30),
                    'animate': (0.0, None, None, 512, 30)}
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
        scene = {**STACK_SCENE, 'steps': ['빈 스택에서 시작합니다', 'push 1: 1을 top 위에 올립니다']}
        g.explain(concept, 1, entry, scene)
        first, second, with_scene = (json.loads(kw['messages'][1]['content']) for _, kw in client.requests)
        self.assertEqual(first['concept']['objectives'], concept.objectives)
        # 장면이 있을 때만 그 요약(요청 + 단계 캡션)을 scene으로 넘김; 장면이 없으면 키 자체가 없어 글만 씀
        self.assertNotIn('scene', first)
        self.assertEqual(with_scene['scene'], scene)
        self.assertEqual(first['stuck_at'], {'excerpt': '스택이 왜 추상 자료형이죠?'})
        self.assertEqual((first['approach'], second['approach']), (EXPLAIN_APPROACHES[1], EXPLAIN_APPROACHES[2]))
        self.assertNotIn('response_format', client.requests[0][1])
        sent = json.dumps([kw['messages'] for _, kw in client.requests], ensure_ascii=False)
        for i in ITEMS:
            if i.concept == 'adt':
                self.assertNotIn(i.rationale, sent)

    # 검증 목적: 보충 설명 사실 오류(PR #38 검수: 모델이 즉석에서 만든 수치 예시 오류, 3차 '오해 바로잡기'에서 정의 반전)를 막는 지시가 빠지지 않는지 확인
    # - 모든 차수: 개념 설명·학습 목표를 사실 근거로 삼고, 새 수치 계산 예시를 만들지 않음
    # - 2차: 단계별 예시는 원소를 작게 두고 단계마다 상태를 적은 뒤 다시 셈(재측정에서 이동 횟수 오류)
    # - 3차: 정의를 먼저 정확히 다시 말하고, 오해와 사실을 분명히 나눠 씀
    def test_explain_prompt_guards_against_factual_errors(self):
        prompt = PROMPTS['explain']
        self.assertIn('concept.description과 concept.objectives', prompt)
        self.assertIn('새 수치를 만들어 계산하는 예시는 쓰지 않는다', prompt)
        self.assertIn('정의를 한 문장으로 다시 정확히 말한 뒤', EXPLAIN_APPROACHES[3])
        self.assertIn("'~라고 생각하기 쉽지만 실제로는 ~이다'", EXPLAIN_APPROACHES[3])
        self.assertIn('새 수치 계산 예시는 넣지 않는다', EXPLAIN_APPROACHES[3])
        self.assertIn('정의를 말할 때는 concept.description 문장을 거의 그대로 쓴다', prompt)
        self.assertIn('적어 둔 상태를 다시 세어 맞는지 확인한다', EXPLAIN_APPROACHES[2])

    # 검증 목적: 실제 스택 E2E에서 나온 보충 글 문제 두 가지를 막는 지시가 빠지지 않는지 확인
    # - 문제 2: 화면 Markdown은 수식을 렌더링하지 않아 $\rightarrow$ 같은 LaTeX가 글자 그대로 보였음
    # - 문제 3: 글 예시(최소 힙·mod 10)와 함께 보이는 장면(최대 힙·mod 5)이 달랐음 → scene과 같은 종류·값을 쓰게 함
    #   단계 상태는 steps(코드 계산)를 따르게 함: 실측에서 모델이 힙 단계를 직접 계산하다 없는 교환을 쓴 사례가 있었음
    #   단계 전체는 애니메이션이 보여 주므로 글은 핵심 단계만 짚게 함: 모든 단계를 다시 늘어놓아 글이 길어지는 것을 막음
    def test_explain_prompt_matches_scene_and_avoids_latex(self):
        prompt = PROMPTS['explain']
        self.assertIn('수식 표기(LaTeX, $…$)를 쓰지 않고', prompt)
        self.assertIn('→ 같은 기호나 말로 쓴다', prompt)
        self.assertIn('입력에 scene이 있으면', prompt)
        self.assertIn('같은 종류·같은 값', prompt)
        self.assertIn('k mod buckets', prompt)
        self.assertIn('steps를 그대로 따르고 직접 계산하지 않는다', prompt)
        self.assertIn('핵심 단계 한두 개만', prompt)

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
        # 보충(S4) 한 요청은 설명과 장면 선택을 차례로 부르므로 두 역할의 최악 시간 합도 예산 안이어야 함
        s4 = sum(ROLE_SETTINGS[r].attempts * ROLE_SETTINGS[r].timeout + MODEL_LIST_TIMEOUT for r in ('explain', 'animate'))
        self.assertLessEqual(s4, STEP_BUDGET_SECONDS)



class AnimateTests(unittest.TestCase):
    def setUp(self):
        self.concept = next(c for c in MAP.concepts if c.id == 'heap')
        self.entry = Entry(kind='excerpt', excerpt='힙에서 삽입 후 왜 위로 올라가요?', return_to='/learn')

    # 검증 목적: 장면 선택 응답 schema가 허용 장면 종류(kinds)의 요청 타입만 고를 수 있게 제한되고 null 분기가 없는지 확인
    # - 실측(2026-10-26)에서 null 분기가 있으면 장면이 분명한 개념(heap·hash-table)도 null로 쏠려 null을 뺌
    # - 가이드 디코딩은 $ref를 풀지 못할 수 있어 schema 안에 $ref가 남지 않는지도 확인
    def test_choose_animation_schema_limits_kinds(self):
        scene = {'kind': 'heap', 'order': 'min', 'ops': [{'op': 'insert', 'value': 3}, {'op': 'insert', 'value': 1}]}
        client = FakeClient(animate=lambda user, kw: json.dumps({'scene': scene}))
        request = gateway(client).choose_animation(self.concept, self.entry, ('heap',))
        self.assertEqual((request.kind, request.order, len(request.ops)), ('heap', 'min', 2))
        _, kw = client.requests[0]
        schema = kw['response_format']['json_schema']['schema']
        options = schema['properties']['scene']['anyOf']
        kinds = [o['properties']['kind']['const'] for o in options if o.get('type') == 'object']
        self.assertEqual(kinds, ['heap'])
        self.assertNotIn({'type': 'null'}, options)
        self.assertEqual(schema['properties']['scene']['anyOf'], options)
        for keyword in ('$ref', 'discriminator', 'oneOf'):
            self.assertNotIn(keyword, json.dumps(schema))
        two = FakeClient(animate=lambda user, kw: json.dumps({'scene': None}))
        gateway(two).choose_animation(self.concept, self.entry, ('heap', 'bst'))
        options = two.requests[0][1]['response_format']['json_schema']['schema']['properties']['scene']['anyOf']
        self.assertEqual([o['properties']['kind']['const'] for o in options if o.get('type') == 'object'], ['heap', 'bst'])

    # 검증 목적: schema에는 null이 없지만 응답이 null이면(가이드 디코딩이 꺼진 서버 등) 오류 대신 None(텍스트만)을 돌려주는지 확인
    def test_choose_animation_null(self):
        client = FakeClient(animate=lambda user, kw: json.dumps({'scene': None}))
        self.assertIsNone(gateway(client).choose_animation(self.concept, self.entry, ('heap',)))

    # 검증 목적: 허용 밖 장면 종류나 스키마 위반은 잘못된 출력으로 재시도한 뒤 LLMUnavailable로 바뀌는지 확인
    def test_choose_animation_rejects_other_kinds(self):
        for scene in (STACK_SCENE, {'kind': 'heap', 'order': 'min', 'ops': [], 'extra': 1}):
            client = FakeClient(animate=lambda user, kw, s=scene: json.dumps({'scene': s}))
            with self.assertRaises(LLMUnavailable):
                gateway(client).choose_animation(self.concept, self.entry, ('heap',))
            self.assertEqual(len(client.requests), ROLE_SETTINGS['animate'].attempts)

    # 검증 목적: 개념별 제한표(CONCEPT_SCENE_LIMITS)가 장면 선택의 schema·형식 예시·응답 검증에 모두 적용되는지 확인
    # - merge-sort 보충에는 병합 정렬 장면만 나와야 함. 가이드 디코딩이 꺼진 서버에서 온 다른 알고리즘 응답도 거부
    def test_choose_animation_applies_concept_limits(self):
        concept = next(c for c in MAP.concepts if c.id == 'merge-sort')
        merge = {'kind': 'sort', 'algorithm': 'merge', 'values': [4, 1, 3]}
        client = FakeClient(animate=lambda user, kw: json.dumps({'scene': merge}))
        request = gateway(client).choose_animation(concept, self.entry, ('sort',))
        self.assertEqual(request.algorithm, 'merge')
        _, kw = client.requests[0]
        (option,) = kw['response_format']['json_schema']['schema']['properties']['scene']['anyOf']
        self.assertEqual(option['properties']['algorithm']['enum'], ['merge'])
        self.assertEqual(json.loads(kw['messages'][1]['content'])['formats']['sort']['algorithm'], 'merge')
        insertion = FakeClient(animate=lambda user, kw: json.dumps({'scene': {**merge, 'algorithm': 'insertion'}}))
        with self.assertRaises(LLMUnavailable):
            gateway(insertion).choose_animation(concept, self.entry, ('sort',))

    # 검증 목적: 장면 선택 입력에 개념 설명·목표·막힌 맥락·허용 장면·장면 형식 예시만 넣고 문항·정답은 넘기지 않으며,
    # 프롬프트가 실행 가능한 작은 입력, 음수 해시 키 회피, 좁은 null 조건을 지시하는지 확인
    # - 실측(2026-10-26, 90건)에서 null 선택이 81건이었음: 형식 예시가 없고 null 조건이 넓으면 모델이 null로 쏠림
    def test_choose_animation_request_content(self):
        client = FakeClient(animate=lambda user, kw: json.dumps({'scene': None}))
        gateway(client).choose_animation(self.concept, self.entry, ('heap',))
        _, kw = client.requests[0]
        user = json.loads(kw['messages'][1]['content'])
        self.assertEqual(set(user), {'concept', 'stuck_at', 'scenes', 'formats'})
        self.assertEqual(user['formats'], {'heap': SCENE_FORMATS['heap']})
        self.assertEqual((user['concept']['objectives'], user['stuck_at'], user['scenes']),
                         (self.concept.objectives, {'excerpt': '힙에서 삽입 후 왜 위로 올라가요?'}, ['heap']))
        sent = json.dumps(kw['messages'], ensure_ascii=False)
        for i in ITEMS:
            self.assertNotIn(i.question, sent)
        prompt = PROMPTS['animate']
        self.assertTrue(prompt.startswith(COMMON))
        for phrase in ('실행할 수 있는', '음수', 'formats', '반드시', '8개 이하', 'nodes에 있는 이름만'):
            self.assertIn(phrase, prompt)

    # 검증 목적: 같은 개념을 다시 설명할 때 직전 장면을 previous로 넘기고, 다른 장면을 고르라고 지시하는지 확인
    # - temperature 0이라 같은 입력이면 같은 장면이 나오므로, 입력에 직전 장면을 넣어야 재설명 장면이 달라짐
    def test_choose_animation_receives_previous_scene(self):
        previous = {'kind': 'heap', 'order': 'min', 'ops': [{'op': 'insert', 'value': 3}]}
        client = FakeClient(animate=lambda user, kw: json.dumps({'scene': None}))
        gateway(client).choose_animation(self.concept, self.entry, ('heap',), previous=previous)
        user = json.loads(client.requests[0][1]['messages'][1]['content'])
        self.assertEqual(user['previous'], previous)
        self.assertIn('previous', PROMPTS['animate'])
        self.assertIn('다른 장면', PROMPTS['animate'])
        self.assertEqual(ROLE_SETTINGS['animate'].temperature, 0.0)

    # 검증 목적: 모델에 보여 주는 장면 형식 예시가 모든 장면 종류에 있고, 그 자체로 검증·실행 가능한지 확인
    # - 예시가 틀리면 모델이 틀린 형식을 따라 해 스키마·시뮬레이션 실패가 늘기 때문
    def test_scene_formats_are_valid_and_runnable(self):
        self.assertEqual(set(SCENE_FORMATS), set(SIMULATORS))
        for kind, example in SCENE_FORMATS.items():
            request = AnimationRequestModel.model_validate(example).root
            self.assertEqual(request.kind, kind)
            simulate(request)

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


## @brief 실제 지도·문항 은행과 가짜 모델로 세션을 끝까지 진행; 진단은 모두 틀리고 확인 문항은 정답 키로 맞힘
# - items: 특정 개념 문항을 뺀 목록을 넣어 "문항 없는 개념"을 실제 문항 은행과 무관하게 만들 때 씀
def run_session(test, entry, client, items=ITEMS):
    g, state, calls = gateway(client), None, 0
    data = {'type': 'start', 'entry': entry}
    while True:
        before = len(client.requests)
        # 보충(remediate) 요청만 설명 + 장면 선택으로 2회까지, 나머지는 1회 이하
        limit = 2 if state and state.stage == 'remediate' else 1
        state = step(state, StepInput.model_validate(data), g, MAP, items, 1.0)
        test.assertLessEqual(len(client.requests) - before, limit, '요청당 LLM 호출 상한')
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
        self.assertEqual({r for r, _ in client.requests}, {'explain', 'grade_explanation', 'summarize', 'animate'})
        self.assertNotIn('animation_failed', [e.kind for e in state.log])

    # 검증 목적: 붙여넣기 진입이 개념 특정으로 시작해 complete까지 가는지 확인
    def test_excerpt_entry_completes(self):
        client = happy_client(identified='queue')
        state = run_session(self, {'kind': 'excerpt', 'excerpt': '원형 큐에서 rear가 왜 0으로 가요?', 'return_to': '/#/roadmap'}, client)
        self.assertEqual((state.stage, state.target), ('complete', 'queue'))
        self.assertEqual(client.requests[0][0], 'identify')

    # 검증 목적: 지도에는 있지만 문항 은행이 없는 개념(MVP 지원 밖)과 지도 밖("none")은 범위 밖으로 끝나는지 확인
    # - 문항 묶음이 늘면 실제 은행에서 문항 없는 개념이 사라지므로, tree 문항을 뺀 목록을 주입해 그 상황을 만듦
    def test_unsupported_or_unknown_concept_is_out_of_scope(self):
        without_tree = [i for i in ITEMS if i.concept != 'tree']
        for identified in ('tree', 'none'):
            state = run_session(self, {'kind': 'excerpt', 'excerpt': '막힌 문장', 'return_to': '/#/roadmap'}, happy_client(identified),
                                without_tree)
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

    # 검증 목적: 실제 Gateway 경로에서 장면 선택이 설명보다 먼저 나가고, 고른 장면 요청이 설명 입력의 scene으로 들어가는지 확인
    def test_explain_gets_scene_chosen_first(self):
        clients = []

        def recording():
            clients.append(happy_client())
            return clients[-1]
        with patch('serving.stuck_llm._default_client', recording):
            response = self.client.post('/stuck/step', json={'state': self.opted, 'input': {'type': 'continue'}})
        self.assertEqual(response.status_code, 200)
        requests = [(r, kw) for c in clients for r, kw in c.requests]
        self.assertEqual([r for r, _ in requests], ['animate', 'explain'])
        steps = [frame['caption'] for frame in response.json()['view']['animation']['frames']]
        self.assertEqual(json.loads(requests[1][1]['messages'][1]['content'])['scene'], {**STACK_SCENE, 'steps': steps})

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

    # 검증 목적: (Review Focus 1) 장면 선택이 시간 초과로 실패해도 설명은 장면 없이 진행해 503이 아니라 텍스트 보충 화면(200)을 주는지 확인
    def test_animation_failure_is_not_503(self):
        clients = []

        def failing():
            clients.append(happy_client())
            clients[-1].responders['animate'] = lambda user, kw: APITimeoutError(request=REQUEST)
            return clients[-1]
        with patch('serving.stuck_llm._default_client', failing):
            response = self.client.post('/stuck/step', json={'state': self.opted, 'input': {'type': 'continue'}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r for c in clients for r, _ in c.requests],
                         ['animate'] * ROLE_SETTINGS['animate'].attempts + ['explain'])
        explain_user = json.loads(clients[-1].requests[-1][1]['messages'][1]['content'])
        self.assertNotIn('scene', explain_user)
        self.assertEqual(response.json()['view']['text'], '**스택** 보충 설명')
        self.assertNotIn('animation', response.json()['view'])
        self.assertFalse(BUSY.locked())


if __name__ == '__main__':
    unittest.main()
