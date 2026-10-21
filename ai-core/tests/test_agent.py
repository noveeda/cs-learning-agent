# 파일 역할: 모델 응답을 모사해 도구 결과 전달·스트리밍 순서·잘린 호출 실행 방지를 검증
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, patch

from openai.types.chat import ChatCompletionMessage
from serving.agent import run_agent


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
