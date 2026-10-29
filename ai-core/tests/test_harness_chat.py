## @file
# @brief 스위치(CS_HARNESS)로 /chat을 근거 검사 하네스에 연결하는 경로와 교재 그림·애니메이션 artifact, 모델 잠금 동작을 검증
# - 하네스 흐름 자체(판정·수정 규칙)는 test_harness_flow.py가 맡고, 여기서는 연결부만 확인함
# - 모델은 OpenAI 가짜 클라이언트(역할별 응답)나 가짜 게이트웨이로, 검색기는 정해 둔 문단을 돌려주는 가짜로 대신함
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from rag.search import Figure, Passage
from serving.animation import CONCEPT_SCENES
from serving.animation.model import AnimationRequestModel
from serving.api import BUSY, app
from serving.concept_map import load_concept_map
from serving.context import history_messages
from serving.harness import BLOCKED_TEXT, NEEDS_CHECK_MARK, Grounding
from serving.harness_chat import DEFAULT_MIN_SCORE, build_retriever, chat_events, harness_enabled, previous_grounding
from serving.harness_log import HarnessLog
from serving.stuck_llm import PROMPTS

CONCEPT_MAP = load_concept_map()
SCENE_CONCEPT = 'stack'
PLAIN_CONCEPT = next(c.id for c in CONCEPT_MAP.concepts if c.id not in CONCEPT_SCENES)
PASSAGE = Passage('ods:s.tex#3.1:0', 'ods', 's.tex#3.1', 'Stacks', 'A stack removes the most recently added element.', 0.7)
GROUNDING = {'type': 'artifact', 'kind': 'grounding', 'artifact_id': 'b' * 32,
             'grounding': {'concepts': ['stack'], 'passage_ids': [PASSAGE.id], 'verified': ['스택은 마지막 값을 먼저 꺼낸다.']}}
FIGURE = {'type': 'artifact', 'kind': 'figure', 'artifact_id': 'c' * 32, 'url': f'/api/artifacts/{"c" * 32}',
          'figure': {'title': 't', 'caption': 'c', 'source': 's'}}


## @brief 정해 둔 문단·그림을 돌려주고 호출을 기록하는 가짜 검색기
class FakeRetriever:
    def __init__(self, figure=None):
        self.figure_result, self.calls = figure, []

    def passages(self, concepts, query, k=3, exclude=frozenset()):
        self.calls.append(('passages', tuple(concepts)))
        return [PASSAGE]

    def by_ids(self, ids):
        self.calls.append(('by_ids', tuple(ids)))
        return [PASSAGE] if PASSAGE.id in ids else []

    def figure(self, concepts, query, compatible_with=()):
        return self.figure_result


## @brief 역할마다 정해 둔 결과를 돌려주는 가짜 하네스 게이트웨이(모든 문장을 근거 있음으로 판정)
class FakeGateway:
    def __init__(self, concepts=(SCENE_CONCEPT,), follow_up=False):
        self.scope_result, self.calls = (list(concepts), follow_up), []

    def scope(self, question, previous, concept_map):
        self.calls.append(('scope', tuple(previous)))
        return self.scope_result

    def ground(self, question, sources, concept_titles, prior):
        self.calls.append(('ground', tuple(prior)))
        return [('스택은 마지막에 넣은 값을 먼저 꺼낸다.', ('p1',))]

    def judge(self, sentence, source_text):
        return 'supported', 'removes the most recently added element'

    def repair(self, sentence, source_text, source_key):
        raise AssertionError('판정이 모두 통과하므로 수정은 부르지 않음')

    def choose_animation(self, concept, entry, kinds):
        self.calls.append(('animate', concept.id, entry.excerpt, kinds))
        return AnimationRequestModel.model_validate({'kind': 'stack', 'ops': [{'op': 'push', 'value': 1}]}).root


def reply(content):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=content))])


## @brief OpenAI 가짜 클라이언트; 시스템 지시로 하네스 역할을 알아내 역할별 JSON을 돌려주고 호출 순서를 기록
# - on_call: 모델 호출 도중(요청 처리 중) 실행할 확인 함수. 동시에 들어온 두 번째 요청을 흉내 내는 데 씀
class FakeClient:
    def __init__(self, on_call=None):
        self.roles, self.on_call = [], on_call
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.models = SimpleNamespace(list=lambda timeout: SimpleNamespace(data=[SimpleNamespace(id='served-model')]))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def create(self, **kwargs):
        role = next(r for r, prompt in PROMPTS.items() if kwargs['messages'][0]['content'] == prompt)
        self.roles.append(role)
        if self.on_call:
            self.on_call(role)
        return reply(json.dumps({
            'scope': {'concepts': [PLAIN_CONCEPT], 'follow_up': False},
            'ground': {'sentences': [{'text': '스택은 마지막에 넣은 값을 먼저 꺼낸다.', 'sources': ['p1']},
                                     {'text': '꺼낼 때는 맨 위 값만 뺀다.', 'sources': ['p1']}]},
            'judge': {'verdict': 'supported', 'evidence': 'removes the most recently added element'},
        }[role], ensure_ascii=False))


def ndjson(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patches = [patch('serving.api.OUTPUT_DIR', self.root),
                   patch('serving.api.HarnessLog', lambda: HarnessLog(self.root / 'logs')),
                   patch('serving.api.build_retriever', lambda: FakeRetriever())]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def harness_on(self):
        return patch.dict(os.environ, {'CS_HARNESS': '1'})

    # 검증 목적(Review Focus 2): 스위치를 켜지 않으면 /chat이 기존 에이전트를 쓰고 하네스를 부르지 않음
    @patch('serving.api.chat_events')
    @patch('serving.api.OpenAI')
    @patch('serving.api.run_agent')
    def test_chat_uses_agent_when_switch_off(self, run, client, events):
        client.return_value.__enter__.return_value.models.list.return_value.data = [
            SimpleNamespace(id='test', model_extra={'max_model_len': 32768})]
        client.return_value.__enter__.return_value.base_url = 'http://127.0.0.1:8000/v1/'
        run.return_value = iter([{'type': 'answer', 'text': 'ok'}, {'type': 'done'}])
        with patch.dict(os.environ, {'CS_HARNESS': ''}):
            self.assertFalse(harness_enabled())
            response = self.client.post('/chat', json={'question': '스택?'})
        self.assertTrue(run.called)
        self.assertFalse(events.called)
        self.assertEqual(ndjson(response)[-1], {'type': 'done'})

    # 검증 목적: 스위치를 켜면 /chat이 에이전트 대신 하네스 이벤트를 그대로 내보냄
    @patch('serving.api.run_agent')
    def test_chat_uses_harness_when_switch_on(self, run):
        with self.harness_on(), patch('serving.api.chat_events',
                                      return_value=iter([{'type': 'answer', 'text': 'h'}, {'type': 'done'}])) as events:
            response = self.client.post('/chat', json={'question': '스택?'})
        self.assertFalse(run.called)
        self.assertTrue(events.called)
        self.assertEqual([e['type'] for e in ndjson(response)], ['started', 'answer', 'done'])

    # 검증 목적(잠금 a): 하네스 채팅 한 요청 안의 여러 모델 호출(범위·생성·판정×2)이 모두 성공하고, 요청 내내 BUSY가 잡혀 있음
    # - 게이트웨이가 BUSY를 다시 잡으려 하면 모든 호출이 '다른 요청을 처리 중'으로 실패하는 문제를 막는지 확인
    def test_harness_model_calls_succeed_while_chat_holds_busy(self):
        held = []
        fake = FakeClient(on_call=lambda role: held.append(BUSY.locked()))
        with self.harness_on(), patch('serving.stuck_llm._default_client', return_value=fake):
            events = ndjson(self.client.post('/chat', json={'question': '스택이 뭐예요?'}))
        self.assertEqual(fake.roles, ['scope', 'ground', 'judge', 'judge'])
        self.assertEqual(held, [True] * 4)
        final = next(e for e in events if e['type'] == 'answer')['text']
        self.assertTrue(final.startswith('스택은 마지막에 넣은 값을 먼저 꺼낸다.[^p1] 꺼낼 때는 맨 위 값만 뺀다.[^p1]'))
        self.assertNotIn(NEEDS_CHECK_MARK, final)
        self.assertFalse(BUSY.locked())

    # 검증 목적(잠금 b): 하네스 채팅이 진행 중일 때 들어온 두 번째 채팅은 기다리지 않고 기존 '다른 질문을 처리 중' 오류로 거절됨
    def test_second_chat_rejected_while_harness_runs(self):
        second = []

        def ask_again(role):
            if role == 'scope':
                second.append(ndjson(self.client.post('/chat', json={'question': '두 번째'})))

        with self.harness_on(), patch('serving.stuck_llm._default_client', return_value=FakeClient(on_call=ask_again)):
            first = ndjson(self.client.post('/chat', json={'question': '스택이 뭐예요?'}))
        self.assertEqual(second, [[{'type': 'error', 'message': '다른 질문을 처리 중입니다. 잠시 후 다시 시도해 주세요.'}]])
        self.assertEqual(first[-1], {'type': 'done'})

    # 검증 목적(잠금 c): 스위치가 꺼져 있으면 기존처럼 BUSY가 잡혀 있을 때 바로 오류를 돌려줌
    def test_switch_off_keeps_busy_behavior(self):
        BUSY.acquire()
        try:
            with patch.dict(os.environ, {'CS_HARNESS': ''}):
                events = ndjson(self.client.post('/chat', json={'question': 'test'}))
        finally:
            BUSY.release()
        self.assertEqual(events, [{'type': 'error', 'message': '다른 질문을 처리 중입니다. 잠시 후 다시 시도해 주세요.'}])

    # 검증 목적: Spring이 저장했다가 이력으로 돌려보내는 grounding·figure artifact를 요청 검증이 거부하지 않고,
    #   스위치가 꺼진 에이전트에는 모델 입력·활성 artifact로 넘기지 않음
    @patch('serving.api.OpenAI')
    @patch('serving.api.run_agent')
    def test_history_skips_grounding_and_figure_artifacts(self, run, client):
        client.return_value.__enter__.return_value.models.list.return_value.data = [
            SimpleNamespace(id='test', model_extra={'max_model_len': 32768})]
        client.return_value.__enter__.return_value.base_url = 'http://127.0.0.1:8000/v1/'
        run.return_value = iter([{'type': 'done'}])
        history = [{'role': 'user', 'content': '스택?'}, {'role': 'assistant', 'content': '답', 'artifacts': [GROUNDING, FIGURE]}]
        with patch.dict(os.environ, {'CS_HARNESS': ''}):
            response = self.client.post('/chat', json={'question': '더 쉽게', 'history': history, 'active_artifact': FIGURE})
        self.assertEqual(response.status_code, 200)
        kwargs = run.call_args.kwargs
        self.assertIsNone(kwargs['active_artifact'])
        messages = history_messages(kwargs['history'])
        self.assertEqual(messages[1]['content'], '답')

    # 검증 목적: 하네스가 복사해 둔 교재 그림 PNG를 /artifacts/{id}가 image/png로 돌려줌(기존 SVG 처리와 별개)
    def test_figure_artifact_png_served(self):
        source = self.root / 'src.png'
        source.write_bytes(b'\x89PNG fake')
        retriever = FakeRetriever(Figure('ods:f1', 'ods', 's.tex#3.1', 'A stack', source, 0.5))
        events = list(chat_events('스택?', [], FakeGateway(), retriever, CONCEPT_MAP, HarnessLog(self.root / 'logs'), self.root))
        [figure] = [e for e in events if e.get('kind') == 'figure']
        self.assertNotIn('path', figure)
        self.assertNotIn(str(source), json.dumps(events))
        self.assertEqual(figure['url'], f"/api/artifacts/{figure['artifact_id']}")
        response = self.client.get(f"/artifacts/{figure['artifact_id']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['content-type'], 'image/png')
        self.assertEqual(response.content, b'\x89PNG fake')


class BuildRetrieverTests(unittest.TestCase):
    # 검증 목적: 운영 검색기가 RAG_MIN_SCORE가 없을 때 측정으로 정한 기본 하한을 쓰고, 환경 변수가 있으면 그 값을 따르는지 확인
    #   (하한이 0이면 관련 없는 문단도 근거로 넘어가 "근거 없음" 대신 엉뚱한 근거로 답하게 됨)
    def test_build_retriever_min_score_default_and_env(self):
        with patch.dict(os.environ):
            os.environ.pop('RAG_MIN_SCORE', None)
            self.assertEqual(build_retriever().min_score, DEFAULT_MIN_SCORE)
            self.assertGreater(DEFAULT_MIN_SCORE, 0.0)
        with patch.dict(os.environ, {'RAG_MIN_SCORE': '0.3'}):
            self.assertEqual(build_retriever().min_score, 0.3)


class ChatEventsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.log = HarnessLog(self.root / 'logs')

    def tearDown(self):
        self.tmp.cleanup()

    def run_chat(self, gw, retriever, history=(), question='스택이 뭐예요?'):
        return list(chat_events(question, list(history), gw, retriever, CONCEPT_MAP, self.log, self.root))

    # 검증 목적(Review Focus 3): 직전 답에 근거 기록이 있으면 "더 쉽게" 같은 후속 질문이 그 개념·문단·검증 문장을 이어 씀
    def test_follow_up_reuses_previous_grounding(self):
        history = [{'role': 'user', 'content': '스택?'}, {'role': 'assistant', 'content': '답', 'artifacts': [GROUNDING]}]
        self.assertEqual(previous_grounding(history),
                         Grounding(('stack',), (PASSAGE.id,), ('스택은 마지막 값을 먼저 꺼낸다.',)))
        gw, retriever = FakeGateway(concepts=(PLAIN_CONCEPT,), follow_up=True), FakeRetriever()
        events = self.run_chat(gw, retriever, history, '더 쉽게')
        self.assertEqual(gw.calls[0], ('scope', ('stack',)))
        self.assertEqual(retriever.calls[0], ('by_ids', (PASSAGE.id,)))
        self.assertIn(('ground', ('스택은 마지막 값을 먼저 꺼낸다.',)), gw.calls)
        self.assertEqual(next(e for e in events if e.get('kind') == 'grounding')['grounding']['concepts'], ['stack'])

    # 검증 목적(Review Focus 3): 직전 답에 근거 기록이 없으면(스위치를 켜기 전 대화) 일반 범위 결정으로 감
    def test_follow_up_without_grounding_falls_back(self):
        history = [{'role': 'user', 'content': '스택?'},
                   {'role': 'assistant', 'content': '예전 답', 'artifacts': [{'artifact_id': 'd' * 32, 'diagram': {}}]}]
        self.assertIsNone(previous_grounding(history))
        gw, retriever = FakeGateway(concepts=(PLAIN_CONCEPT,), follow_up=True), FakeRetriever()
        self.run_chat(gw, retriever, history, '더 쉽게')
        self.assertEqual(gw.calls[0], ('scope', ()))
        self.assertEqual(retriever.calls[0], ('passages', (PLAIN_CONCEPT,)))

    # 검증 목적: 직전 근거 기록은 마지막 assistant 턴의 것만 씀(더 이전 턴의 기록을 끌어오지 않음)
    def test_previous_grounding_only_from_last_assistant_turn(self):
        history = [{'role': 'user', 'content': 'a'}, {'role': 'assistant', 'content': 'b', 'artifacts': [GROUNDING]},
                   {'role': 'user', 'content': 'c'}, {'role': 'assistant', 'content': 'd'}]
        self.assertIsNone(previous_grounding(history))

    # 검증 목적: 장면이 있는 개념이고 교재 그림이 없으면 장면 선택으로 애니메이션 하나를 grounding 앞에 붙임
    def test_animation_preferred_for_trace_concepts(self):
        gw = FakeGateway(concepts=(SCENE_CONCEPT,))
        events = self.run_chat(gw, FakeRetriever(), question='스택에 1을 넣는 과정')
        kinds = [e.get('kind') for e in events if e['type'] == 'artifact']
        self.assertEqual(kinds, ['animation', 'grounding'])
        animation = next(e for e in events if e.get('kind') == 'animation')
        self.assertEqual(animation['request']['kind'], 'stack')
        self.assertTrue(animation['animation']['frames'])
        self.assertIn(('animate', SCENE_CONCEPT, '스택에 1을 넣는 과정', CONCEPT_SCENES[SCENE_CONCEPT]), gw.calls)

    # 검증 목적: 교재 그림이 있으면 그림을 쓰고 장면 선택 모델 호출을 하지 않음. 장면 없는 개념도 애니메이션을 만들지 않음
    def test_figure_wins_and_plain_concept_has_no_animation(self):
        source = self.root / 'src.png'
        source.write_bytes(b'png')
        gw = FakeGateway(concepts=(SCENE_CONCEPT,))
        events = self.run_chat(gw, FakeRetriever(Figure('ods:f1', 'ods', 's.tex#3.1', 'A stack', source, 0.5)))
        self.assertEqual([e.get('kind') for e in events if e['type'] == 'artifact'], ['figure', 'grounding'])
        self.assertTrue((self.root / f"{events[-4]['artifact_id']}.png").is_file())
        plain = FakeGateway(concepts=(PLAIN_CONCEPT,))
        events = self.run_chat(plain, FakeRetriever())
        self.assertEqual([e.get('kind') for e in events if e['type'] == 'artifact'], ['grounding'])
        self.assertNotIn('animate', [c[0] for c in gw.calls + plain.calls])

    # 검증 목적: 장면 선택이 실패해도(모델 오류) 답은 애니메이션 없이 정상으로 끝남. 차단 답에는 artifact가 없음
    def test_animation_failure_and_blocked_answer(self):
        class Failing(FakeGateway):
            def choose_animation(self, *args):
                from serving.stuck import LLMUnavailable
                raise LLMUnavailable('down')

        events = self.run_chat(Failing(), FakeRetriever())
        self.assertEqual([e.get('kind') for e in events if e['type'] == 'artifact'], ['grounding'])
        self.assertEqual(events[-1], {'type': 'done'})
        blocked = self.run_chat(FakeGateway(concepts=()), FakeRetriever())
        self.assertEqual(next(e for e in blocked if e['type'] == 'answer')['text'], BLOCKED_TEXT)
        self.assertFalse([e for e in blocked if e['type'] == 'artifact'])


if __name__ == '__main__':
    unittest.main()
