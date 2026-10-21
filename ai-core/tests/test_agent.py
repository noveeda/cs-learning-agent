# 파일 역할: 모델 응답을 모사해 도구 결과 전달·스트리밍 순서·잘린 호출 실행 방지를 검증
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, patch

from openai.types.chat import ChatCompletionMessage
from serving.agent import AnswerStream, run_agent


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
    return stream([chunk('비공개 추론</think>'),
                   chunk(message.content, calls, 'tool_calls' if calls else 'stop')])


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
            chunk('비공개 추론</think>가상 '), chunk('메모리'), chunk(finish='stop')])
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
            chunk('비공개 추론</think>'),
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
        client.chat.completions.create.return_value = stream([chunk('비공개 추론</think>일부 답변'), chunk(finish='length')])
        events = run_agent(client, 'test', 'test', Path('/tmp/unused'))
        self.assertEqual(next(events)['type'], 'text_start')
        self.assertEqual(next(events)['type'], 'text_delta')
        with self.assertRaisesRegex(RuntimeError, '토큰 제한'):
            list(events)

    # 검증 목적: 추론 표식이 어느 위치에서 분할돼도 내부 내용 누출 없이 최종 답변을 실시간 전달
    def test_raw_reasoning_marker_at_every_chunk_boundary(self):
        value = '<think>비공개 추론</think>\n설명 후 정답'
        for split in range(len(value) + 1):
            with self.subTest(split=split):
                gate = AnswerStream()
                actual = gate.feed(value[:split]) + gate.feed(value[split:])
                self.assertEqual(actual, '설명 후 정답')
                self.assertEqual(gate.tail, '')

    # 검증 목적: 추론이 끝나지 않으면 화면 이벤트나 성공 응답으로 내보내지 않음
    def test_unfinished_reasoning_never_reaches_events(self):
        client = Mock()
        client.chat.completions.create.return_value = stream([
            chunk('비공개 추론'), chunk('반복' * 100), chunk(finish='length')])
        events = run_agent(client, 'test', 'test', Path('/tmp/unused'))
        with self.assertRaisesRegex(RuntimeError, '토큰 제한'):
            next(events)

    # 검증 목적: 서버가 분리한 reasoning 필드를 버리고 최종 content만 기존 이벤트로 스트리밍
    def test_separate_reasoning_is_not_emitted_or_stored(self):
        first = chunk()
        first.choices[0].delta.reasoning = '비공개 추론'
        client = Mock()
        client.chat.completions.create.return_value = stream([
            first, chunk('설명 '), chunk('정답'), chunk(finish='stop')])
        events = run_agent(client, 'test', 'test', Path('/tmp/unused'))
        self.assertEqual(next(events), {'type': 'text_start'})
        self.assertEqual(next(events), {'type': 'text_delta', 'text': '설명 '})
        self.assertEqual(next(events), {'type': 'text_delta', 'text': '정답'})
        self.assertEqual(list(events), [{'type': 'answer', 'text': '설명 정답'}, {'type': 'done'}])
        params = client.chat.completions.create.call_args.kwargs
        self.assertEqual(params['temperature'], 1.0)
        self.assertEqual(params['top_p'], .95)
        self.assertEqual(params['presence_penalty'], 1.5)
        self.assertEqual(params['extra_body'], {
            'top_k': 20, 'min_p': 0.0, 'repetition_penalty': 1.0,
            'chat_template_kwargs': {'enable_thinking': True}})
        self.assertTrue(params['stream'])
        self.assertFalse(params['parallel_tool_calls'])
        self.assertNotIn('response_format', params)

    # 검증 목적: 도구 교환 이후 문맥에도 내부 추론이 섞이지 않고 성공 결과와 최종 답변만 보존
    @patch('serving.agent.create_diagram')
    def test_separate_reasoning_tool_round_keeps_clean_history(self, create):
        create.return_value = {'ok': True, 'artifact_id': 'test'}
        reasoning = chunk()
        reasoning.choices[0].delta.reasoning_content = '비공개 도구 추론'
        call = SimpleNamespace(index=0, id='call_1', function=SimpleNamespace(
            name='create_diagram', arguments='{}'))
        client = Mock()
        client.chat.completions.create.side_effect = [stream([
            reasoning, chunk('설명'), chunk(calls=[call]), chunk(finish='tool_calls'),
        ]), response(ChatCompletionMessage(role='assistant', content='정답'))]
        events = list(run_agent(client, 'test', 'test', Path('/tmp/unused')))
        self.assertEqual(events[-1], {'type': 'done'})
        create.assert_called_once()
        messages = client.chat.completions.create.call_args.kwargs['messages']
        self.assertEqual(messages[-2]['content'], '설명')
        self.assertNotIn('비공개', json.dumps(messages, ensure_ascii=False))

    # 검증 목적: 추론 경계가 없는 서버 응답 안의 가짜 도구 요청을 실행하지 않음
    @patch('serving.agent.create_diagram')
    def test_tool_call_without_reasoning_boundary_is_rejected(self, create):
        call = SimpleNamespace(index=0, id='call_1', function=SimpleNamespace(
            name='create_diagram', arguments='{}'))
        client = Mock()
        client.chat.completions.create.return_value = stream([
            chunk('비공개 추론'), chunk(calls=[call]), chunk(finish='tool_calls')])
        with self.assertRaisesRegex(RuntimeError, '추론 파서'):
            list(run_agent(client, 'test', 'test', Path('/tmp/unused')))
        create.assert_not_called()

    # 검증 목적: 긴 이력은 완료 문답 쌍으로 줄이고 실제 입력·출력·안전 여유 합이 모델 한도를 넘지 않음
    def test_reasoning_budget_keeps_recent_pairs_within_context(self):
        client = Mock()
        client.chat.completions.create.return_value = response(
            ChatCompletionMessage(role='assistant', content='설명 후 정답'))
        history = [{'role': role, 'content': str(i)} for i in range(3)
                   for role in ('user', 'assistant')]
        count = lambda messages: len(messages) * 800
        events = list(run_agent(client, 'test', 'test', Path('/tmp/unused'),
                                history=history, count_tokens=count, context_limit=8192))
        params = client.chat.completions.create.call_args.kwargs
        self.assertEqual(params['messages'][1:3], history[-2:])
        self.assertEqual(events[0]['dropped_messages'], 4)
        self.assertEqual(params['max_tokens'], 8192 - count(params['messages']) - 256)
        self.assertLessEqual(count(params['messages']) + params['max_tokens'] + 256, 8192)

    # 검증 목적: 명시한 출력 상한은 실제 문맥 여유가 더 커도 넘지 않음
    def test_explicit_output_cap_is_respected(self):
        client = Mock()
        client.chat.completions.create.return_value = response(
            ChatCompletionMessage(role='assistant', content='정답'))
        list(run_agent(client, 'test', 'test', Path('/tmp/unused'),
                       count_tokens=lambda messages: 1000, max_tokens=2048))
        self.assertEqual(client.chat.completions.create.call_args.kwargs['max_tokens'], 2048)

    # 검증 목적: 내용이 없는 분리 추론 필드도 서버 경계로 인식하고 최종 텍스트를 잃지 않음
    def test_empty_separate_reasoning_field_preserves_final_answer(self):
        for field in ('reasoning', 'reasoning_content'):
            with self.subTest(field=field):
                first = chunk()
                setattr(first.choices[0].delta, field, '')
                client = Mock()
                client.chat.completions.create.return_value = stream([
                    first, chunk('설명 후 정답'), chunk(finish='stop')])
                events = list(run_agent(client, 'test', 'test', Path('/tmp/unused')))
                self.assertEqual(events[-2], {'type': 'answer', 'text': '설명 후 정답'})
