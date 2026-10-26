# 파일 역할: 모델 응답을 모사해 도구 결과 전달·스트리밍 순서·잘린 호출 실행 방지·애니메이션 도구 분기를 검증
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, patch

from openai.types.chat import ChatCompletionMessage
from serving.agent import TOOLS, run_agent
from serving.animation import ANIMATION_TOOL, SIMULATORS
from serving.animation.model import AnimationRequestModel
from serving.context import token_counter


# 역할: 모델 스트림의 텍스트·도구·종료 사유 조각 구성
def chunk(text=None, calls=None, finish=None):
    return SimpleNamespace(choices=[SimpleNamespace(
        delta=SimpleNamespace(content=text, tool_calls=calls), finish_reason=finish)])


# 역할: 컨텍스트 매니저로 사용되는 가짜 모델 스트림 구성
def stream(chunks):
    result = MagicMock()
    result.__enter__.return_value = iter(chunks)
    return result


# 역할: 완성된 메시지를 테스트에서 사용할 스트리밍 응답 형태로 변환
def response(message):
    calls = []
    for i, call in enumerate(message.tool_calls or []):
        calls.append(SimpleNamespace(index=i, id=call.id, function=call.function))
    return stream([chunk(message.content, calls, 'tool_calls' if calls else 'stop')])


class AgentTests(unittest.TestCase):
    # 검증 목적: 도구 결과가 다음 모델 호출에 포함되는지 확인
    def test_tool_result_is_returned_to_model(self):
        arguments = json.dumps({'title': 'test', 'nodes': [
            {'id': 'a', 'label': 'A', 'column': 0, 'row': 0}], 'edges': []})
        call = ChatCompletionMessage(role='assistant', tool_calls=[{
            'id': 'call_1', 'type': 'function',
            'function': {'name': 'create_diagram', 'arguments': arguments}}])
        client = Mock()
        client.chat.completions.create.side_effect = [response(call), response(
            ChatCompletionMessage(role='assistant', content='완료'))]
        with tempfile.TemporaryDirectory() as folder:
            events = list(run_agent(client, 'test', '그려줘', Path(folder)))
            self.assertEqual([e['type'] for e in events],
                             ['tool_started', 'tool_finished', 'artifact', 'text_start', 'text_delta', 'answer', 'done'])
            self.assertTrue(Path(events[2]['path']).exists())
        messages = client.chat.completions.create.call_args.kwargs['messages']
        self.assertEqual(messages[-1]['tool_call_id'], 'call_1')
        self.assertTrue(json.loads(messages[-1]['content'])['ok'])

    # 검증 목적: 허용하지 않은 도구를 거부하고 모델에 실패 정보 전달 확인
    def test_unknown_tool_is_rejected_and_reported(self):
        call = ChatCompletionMessage(role='assistant', tool_calls=[{
            'id': 'bad', 'type': 'function', 'function': {'name': 'run_shell', 'arguments': '{}'}}])
        client = Mock()
        client.chat.completions.create.side_effect = [response(call), response(
            ChatCompletionMessage(role='assistant', content='도구를 사용할 수 없습니다.'))]
        with tempfile.TemporaryDirectory() as folder:
            events = list(run_agent(client, 'test', 'test', Path(folder)))
            self.assertIn('tool_error', [e['type'] for e in events])
            self.assertEqual(list(Path(folder).iterdir()), [])

    # 검증 목적: 도구 반복 호출이 지정 횟수를 넘지 않는지 확인
    def test_repeated_tool_calls_stop_at_limit(self):
        call = ChatCompletionMessage(role='assistant', tool_calls=[{
            'id': 'bad', 'type': 'function', 'function': {'name': 'unknown', 'arguments': '{}'}}])
        client = Mock()
        client.chat.completions.create.side_effect = [response(call), response(call)]
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(RuntimeError):
            list(run_agent(client, 'test', 'test', Path(folder), max_tool_rounds=1))
        self.assertEqual(client.chat.completions.create.call_count, 2)

    # 검증 목적: 스트림 종료 전에 텍스트 이벤트가 전달되는지 확인
    def test_text_is_yielded_before_stream_finishes(self):
        client = Mock()
        client.chat.completions.create.return_value = stream([
            chunk('가상 '), chunk('메모리'), chunk(finish='stop')])
        events = run_agent(client, 'test', 'test', Path('/tmp/unused'))
        self.assertEqual(next(events)['type'], 'text_start')
        self.assertEqual(next(events), {'type': 'text_delta', 'text': '가상 '})
        self.assertEqual(next(events), {'type': 'text_delta', 'text': '메모리'})
        self.assertEqual(next(events), {'type': 'answer', 'text': '가상 메모리'})
        self.assertEqual(next(events)['type'], 'done')
        self.assertTrue(client.chat.completions.create.call_args.kwargs['stream'])

    # 검증 목적: 분할 수신된 도구 인자를 완성한 뒤 한 번만 실행하는지 확인
    @patch('serving.agent.create_diagram')
    def test_fragmented_tool_arguments_execute_only_when_complete(self, create):
        create.return_value = {'ok': True, 'artifact_id': 'test'}
        def fragment(id=None, name=None, arguments=None):
            return SimpleNamespace(index=0, id=id, function=SimpleNamespace(name=name, arguments=arguments))
        client = Mock()
        client.chat.completions.create.side_effect = [stream([
            chunk(calls=[fragment('call_1', 'create_diagram', '{"title":')]),
            chunk(calls=[fragment(arguments='"도식"}')]),
            chunk(finish='tool_calls'),
        ]), response(ChatCompletionMessage(role='assistant', content='완료'))]
        list(run_agent(client, 'test', 'test', Path('/tmp/unused')))
        create.assert_called_once_with('{"title":"도식"}', Path('/tmp/unused'))

    # 검증 목적: 중단된 스트림의 불완전한 도구를 실행하지 않는지 확인
    @patch('serving.agent.create_diagram')
    def test_interrupted_stream_never_executes_partial_tool(self, create):
        call = SimpleNamespace(index=0, id='call_1', function=SimpleNamespace(
            name='create_diagram', arguments='{"title":'))
        client = Mock()
        client.chat.completions.create.return_value = stream([chunk(calls=[call])])
        with self.assertRaisesRegex(RuntimeError, '중단'):
            list(run_agent(client, 'test', 'test', Path('/tmp/unused')))
        create.assert_not_called()

    # 검증 목적: 토큰 한도로 잘린 답변을 성공으로 처리하지 않는지 확인
    def test_token_limit_is_error_not_success(self):
        client = Mock()
        client.chat.completions.create.return_value = stream([chunk('일부 답변'), chunk(finish='length')])
        events = run_agent(client, 'test', 'test', Path('/tmp/unused'))
        self.assertEqual(next(events)['type'], 'text_start')
        self.assertEqual(next(events)['type'], 'text_delta')
        with self.assertRaisesRegex(RuntimeError, '토큰 제한'):
            list(events)


# 역할: 가짜 모델이 지정 도구를 한 번 부르고, 다음 턴에 텍스트로 끝내는 클라이언트 구성
def tool_then_text(name, arguments):
    call = ChatCompletionMessage(role='assistant', tool_calls=[{
        'id': 'call_1', 'type': 'function', 'function': {'name': name, 'arguments': arguments}}])
    client = Mock()
    client.chat.completions.create.side_effect = [response(call), response(
        ChatCompletionMessage(role='assistant', content='완료'))]
    return client


STACK = {'kind': 'stack', 'ops': [{'op': 'push', 'value': 1}, {'op': 'push', 'value': 2}, {'op': 'pop'}]}


class AnimationToolTests(unittest.TestCase):
    # 검증 목적: show_animation 성공 시 도식과 같은 artifact 이벤트로 프레임이 나가고,
    # 모델에는 프레임 전체 대신 단계 설명만 돌려줘 문맥 토큰을 아끼는지 확인
    def test_show_animation_tool_emits_artifact(self):
        client = tool_then_text('show_animation', json.dumps(STACK))
        events = list(run_agent(client, 'test', '스택 보여줘', Path('/tmp/unused')))
        artifact = next(e for e in events if e['type'] == 'artifact')
        self.assertEqual(artifact['kind'], 'animation')
        self.assertEqual(len(artifact['animation']['frames']), 4)
        tool_message = json.loads(client.chat.completions.create.call_args.kwargs['messages'][-1]['content'])
        self.assertTrue(tool_message['ok'])
        self.assertNotIn('animation', tool_message)
        self.assertIn('pop: top의 2를 꺼냅니다', tool_message['steps'])

    # 검증 목적: 실행할 수 없는 장면(빈 스택 pop)은 tool_error로 알리고, 모델이 고칠 수 있게 실패 결과를 돌려주는지 확인
    def test_show_animation_invalid_returns_tool_error(self):
        client = tool_then_text('show_animation', json.dumps({'kind': 'stack', 'ops': [{'op': 'pop'}]}))
        events = list(run_agent(client, 'test', 'test', Path('/tmp/unused')))
        self.assertIn('tool_error', [e['type'] for e in events])
        self.assertNotIn('artifact', [e['type'] for e in events])
        tool_message = json.loads(client.chat.completions.create.call_args.kwargs['messages'][-1]['content'])
        self.assertIs(tool_message['ok'], False)
        self.assertIn('비어', tool_message['error'])

    # 검증 목적: 평평한 도구 스키마를 본 모델이 다른 kind 전용 필드를 null로 채워도 실행되는지 확인.
    # null이 아닌 엉뚱한 필드는 strict 검증대로 tool_error여야 함
    def test_show_animation_drops_null_fields(self):
        nulls = {**STACK, 'values': None, 'capacity': None,
                 'ops': [{**op, 'index': None, 'key': None} for op in STACK['ops']]}
        events = list(run_agent(tool_then_text('show_animation', json.dumps(nulls)), 'test', 'test', Path('/tmp/unused')))
        self.assertIn('artifact', [e['type'] for e in events])
        wrong = {**STACK, 'capacity': 3}
        events = list(run_agent(tool_then_text('show_animation', json.dumps(wrong)), 'test', 'test', Path('/tmp/unused')))
        self.assertIn('tool_error', [e['type'] for e in events])

    # 검증 목적: (Review Focus 3) 활성 artifact가 애니메이션이어도 diagram 키를 찾다 죽지 않고, 장면 요청을 질문에 붙이는지 확인
    def test_active_animation_artifact_does_not_require_diagram(self):
        client = Mock()
        client.chat.completions.create.return_value = stream([chunk('답'), chunk(finish='stop')])
        active = {'artifact_id': 'a' * 32, 'kind': 'animation', 'request': STACK}
        history = [{'role': 'user', 'content': '스택'},
                   {'role': 'assistant', 'content': '완료', 'artifacts': [active]}]
        list(run_agent(client, 'test', '바꿔줘', Path('/tmp/unused'), history=history, active_artifact=active))
        messages = client.chat.completions.create.call_args.kwargs['messages']
        self.assertIn('animation_request', messages[-1]['content'])
        self.assertIn('"op": "pop"', messages[-1]['content'])
        self.assertIn('animation_request', messages[-2]['content'])

    # 검증 목적: 실제 입력 토큰 계산에도 채팅에 보내는 도구 두 개가 모두 들어가 예산 계산이 어긋나지 않는지 확인
    def test_token_counter_includes_both_tools(self):
        http = Mock()
        http.post.return_value.json.return_value = {'count': 10}
        self.assertEqual(token_counter(http, 'test')([{'role': 'user', 'content': 'x'}]), 10)
        tools = http.post.call_args.kwargs['json']['tools']
        self.assertEqual([t['function']['name'] for t in tools], ['create_diagram', 'show_animation'])
        self.assertIs(tools, TOOLS)

    # 검증 목적: 운영 모델 채팅 템플릿은 parameters의 최상위 properties만 렌더링하므로,
    # 도구 스키마가 평평한 object이고 kind·op 목록이 실제 요청 스키마와 같은지 확인
    def test_animation_tool_schema_is_flat_and_matches_models(self):
        parameters = ANIMATION_TOOL['function']['parameters']
        self.assertEqual(parameters['type'], 'object')
        self.assertNotIn('oneOf', parameters)
        properties = parameters['properties']
        self.assertEqual(set(properties['kind']['enum']), set(SIMULATORS))
        defs = AnimationRequestModel.model_json_schema()['$defs'].values()
        ops = {value for d in defs if 'op' in d.get('properties', {})
               for value in d['properties']['op'].get('enum', [d['properties']['op'].get('const')])}
        self.assertEqual(set(properties['ops']['items']['properties']['op']['enum']), ops)
        fields = {name for d in defs for name in d.get('properties', {})} - {'kind', 'op'}
        item_fields = set(properties['ops']['items']['properties'])
        self.assertLessEqual(fields, set(properties) | item_fields)
