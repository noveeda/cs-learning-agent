# 파일 역할: 실제 내용 길이 기반 예산과 최근 문답 쌍·도식 구조 보존을 검증
import unittest
from unittest.mock import Mock
from serving.context import fit_context, history_messages, token_counter


class ContextTests(unittest.TestCase):
    # 검증 목적: 오래된 문답만 제외하고 최근 문답·현재 도구 교환 보존 확인
    def test_keeps_recent_pairs_and_current_tool_exchange(self):
        system = {'role': 'system', 'content': 'rules'}
        history = [{'role': role, 'content': str(i)} for i in range(3)
                   for role in ('user', 'assistant')]
        current = [{'role': 'user', 'content': 'current diagram'},
                   {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call'}]},
                   {'role': 'tool', 'content': 'result', 'tool_call_id': 'call'}]
        result, dropped, tokens = fit_context(system, history, current, len, 6)
        self.assertEqual(result, [system, *history[-2:], *current])
        self.assertEqual((dropped, tokens), (4, 6))

    # 검증 목적: 메시지 개수 대신 내용의 토큰 비용으로 이력을 선택하는지 확인
    def test_budget_counts_content_not_message_count(self):
        system = {'role': 'system', 'content': ''}
        history = [{'role': 'user', 'content': 'x' * 100},
                   {'role': 'assistant', 'content': 'y' * 100},
                   {'role': 'user', 'content': 'a'}, {'role': 'assistant', 'content': 'b'}]
        count = lambda messages: sum(len(m['content']) for m in messages)
        result, dropped, tokens = fit_context(system, history, [{'role': 'user', 'content': 'now'}], count, 5)
        self.assertEqual((dropped, tokens), (2, 5))
        self.assertEqual(result[1]['content'], 'a')

    # 검증 목적: 필수 입력이 너무 크면 조용히 자르지 않고 오류 반환 확인
    def test_does_not_silently_truncate_current_request(self):
        with self.assertRaisesRegex(ValueError, '예산'):
            fit_context({}, [], [{}, {}], len, 2)

    # 검증 목적: 도식 구조를 보존하고 짝 없는 문답을 거부하는지 확인
    def test_diagram_structure_is_kept_and_dangling_history_rejected(self):
        turns = [{'role': 'user', 'content': 'draw'},
                 {'role': 'assistant', 'content': 'done', 'artifacts': [{
                     'artifact_id': 'a', 'diagram': {'nodes': [{'id': 'page'}], 'edges': []}}]}]
        self.assertIn('"id": "page"', history_messages(turns)[1]['content'])
        with self.assertRaises(ValueError):
            history_messages(turns[:1])

    # 검증 목적: 생성과 동일한 추론 템플릿·도구 스키마로 토큰을 계산해 문맥 초과 방지
    def test_tokenization_uses_thinking_template_and_tools(self):
        http = Mock()
        http.post.return_value.json.return_value = {'count': 123}
        messages = [{'role': 'user', 'content': '질문'}]
        self.assertEqual(token_counter(http, 'model')(messages), 123)
        body = http.post.call_args.kwargs['json']
        self.assertEqual(body['chat_template_kwargs'], {'enable_thinking': True})
        self.assertEqual(body['messages'], messages)
        self.assertTrue(body['tools'])
        self.assertTrue(body['add_generation_prompt'])
        http.post.return_value.raise_for_status.assert_called_once()
