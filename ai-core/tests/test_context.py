import unittest
from serving.context import fit_context, history_messages, token_counter


class ContextTests(unittest.TestCase):
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

    def test_budget_counts_content_not_message_count(self):
        system = {'role': 'system', 'content': ''}
        history = [{'role': 'user', 'content': 'x' * 100},
                   {'role': 'assistant', 'content': 'y' * 100},
                   {'role': 'user', 'content': 'a'}, {'role': 'assistant', 'content': 'b'}]
        count = lambda messages: sum(len(m['content']) for m in messages)
        result, dropped, tokens = fit_context(system, history, [{'role': 'user', 'content': 'now'}], count, 5)
        self.assertEqual((dropped, tokens), (2, 5))
        self.assertEqual(result[1]['content'], 'a')

    def test_does_not_silently_truncate_current_request(self):
        with self.assertRaisesRegex(ValueError, '예산'):
            fit_context({}, [], [{}, {}], len, 2)

    def test_diagram_structure_is_kept_and_dangling_history_rejected(self):
        turns = [{'role': 'user', 'content': 'draw'},
                 {'role': 'assistant', 'content': 'done', 'artifacts': [{
                     'artifact_id': 'a', 'diagram': {'nodes': [{'id': 'page'}], 'edges': []}}]}]
        self.assertIn('"id": "page"', history_messages(turns)[1]['content'])
        with self.assertRaises(ValueError):
            history_messages(turns[:1])
