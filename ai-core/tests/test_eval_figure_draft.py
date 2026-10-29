## @file
# @brief 도식 초안 생성기의 정답 비노출 요청, 검사 실패 재요청·상태 분류, 문항 은행 반영을 모델 서버 없이 검증
import json
import tempfile
import unittest
from pathlib import Path

from eval.figure_draft import RETRIES, apply_drafts, build_messages, draft_item, mark_dropped, repair_layout, summarize
from serving.item_bank import Item

ITEM = Item.model_validate({
    'id': 'shortest-path-d9', 'concept': 'shortest-path', 'purpose': 'diagnosis', 'objective': 0,
    'format': 'multiple_choice', 'question': '간선이 1→2:6, 1→3:2, 3→2:3인 방향 그래프에서 1부터 2까지의 최단 거리는?',
    'options': {'A': '5', 'B': '6', 'C': '8', 'D': '11'}, 'answer': 'A', 'rationale': '1→3→2 경로가 2+3=5로 가장 짧다.'})


## @brief 모델 응답 JSON 글; nodes만 주면 그래프 도식, figure=False면 도식 없음 응답
def reply(nodes=(), edges=(), figure=True):
    return json.dumps({'facts': [], 'has_figure': figure, 'nodes': list(nodes), 'edges': list(edges), 'tables': []})


NODES = [{'id': 'a', 'label': '1', 'shape': 'circle', 'x': 0, 'y': 1},
         {'id': 'b', 'label': '2', 'shape': 'circle', 'x': 2, 'y': 0},
         {'id': 'c', 'label': '3', 'shape': 'circle', 'x': 2, 'y': 2}]
EDGES = [{'from': 'a', 'to': 'b', 'label': '6', 'directed': True},
         {'from': 'a', 'to': 'c', 'label': '2', 'directed': True},
         {'from': 'c', 'to': 'b', 'label': '', 'directed': True}]
LEAK = {'id': 'd', 'label': '거리 5', 'shape': 'text', 'x': 4, 'y': 1}  # 계산한 최단 거리: 지문·보기에 없는 글


## @brief 준비된 응답을 차례로 돌려주고 받은 메시지를 기록하는 가짜 모델
def fake(*responses):
    calls = []

    def complete(messages):
        calls.append(messages)
        return responses[len(calls) - 1]
    return complete, calls


class DraftRequestTests(unittest.TestCase):
    # 검증 목적: 정답 키·해설·선택지가 모델 요청에 들어가지 않아, 모델이 답을 알고 결과 상태를 그리는 일을 막음
    def test_request_contains_question_only(self):
        text = json.dumps(build_messages(ITEM), ensure_ascii=False)
        self.assertIn(ITEM.question, text)
        self.assertNotIn(ITEM.rationale, text)
        self.assertNotIn('"answer"', text)
        self.assertNotIn('11', text.replace(ITEM.question, ''))


class DraftItemTests(unittest.TestCase):
    # 검증 목적: 누설한 첫 초안은 오류를 알려 다시 받고, 고친 초안만 통과(빈 선 라벨은 라벨 없음으로 바뀜)
    def test_retries_with_checker_errors_then_accepts(self):
        complete, calls = fake(reply([*NODES, LEAK], EDGES), reply(NODES, EDGES))
        draft = draft_item(ITEM, complete)
        self.assertEqual(draft['status'], 'ok')
        self.assertEqual(len(calls), 2)
        self.assertIn('지문에 없습니다', calls[1][-1]['content'])
        self.assertNotIn('label', draft['figure']['edges'][2])

    # 검증 목적: 모델이 그릴 구조가 없다고 답하면 도식 없음(none)으로 두고 재요청하지 않음
    def test_no_figure_is_none(self):
        complete, calls = fake(reply(figure=False))
        self.assertEqual(draft_item(ITEM, complete)['status'], 'none')
        self.assertEqual(len(calls), 1)

    # 검증 목적: 재요청 뒤에도 누설이 남으면 버리고(leak), 구조 오류만 남으면 rejected로 따로 셈
    def test_persistent_failures_are_classified(self):
        complete, _ = fake(*[reply([*NODES, LEAK], EDGES)] * (1 + RETRIES))
        self.assertEqual(draft_item(ITEM, complete)['status'], 'leak')
        dangling = [{'from': 'a', 'to': 'z', 'label': '', 'directed': True}]
        complete, _ = fake(*[reply(NODES, dangling)] * (1 + RETRIES))
        broken = draft_item(ITEM, complete)
        self.assertEqual(broken['status'], 'rejected')
        self.assertIsNone(broken['figure'])
        self.assertEqual(summarize([draft_item(ITEM, fake(reply(figure=False))[0]), broken]),
                         {'total': 2, 'ok': 0, 'none': 1, 'leak': 0, 'rejected': 1, 'dropped': 0})

    # 검증 목적: 선이 엇갈린 초안은 모델에 다시 묻기 전에 정점 자리만 바꿔 고치고, 라벨·간선(지문 사실)은 그대로 둠
    def test_layout_repair_swaps_positions_only(self):
        square = [{'id': f'n{i}', 'label': str(i), 'shape': 'circle', 'x': x, 'y': y}
                  for i, (x, y) in enumerate(((0, 0), (2, 0), (0, 2), (2, 2)), 1)]
        ring = [{'from': 'n1', 'to': 'n2', 'label': '6', 'directed': True}, {'from': 'n2', 'to': 'n3', 'label': '2', 'directed': True},
                {'from': 'n3', 'to': 'n4', 'label': '3', 'directed': True}, {'from': 'n4', 'to': 'n1', 'label': '', 'directed': True}]
        item = ITEM.model_copy(update={'question': '간선이 1→2:6, 2→3:2, 3→4:3, 4→1인 방향 그래프'})
        complete, calls = fake(reply(square, ring))
        draft = draft_item(item, complete)
        self.assertEqual((draft['status'], len(calls)), ('ok', 1))
        nodes = draft['figure']['nodes']
        self.assertEqual(sorted((n['x'], n['y']) for n in nodes), sorted((n['x'], n['y']) for n in square))
        self.assertEqual([n['label'] for n in nodes], ['1', '2', '3', '4'])
        self.assertNotEqual(nodes, square)
        self.assertIsNone(repair_layout({'nodes': square}, item.question))
        overlapping = [{**square[0], 'x': 2, 'y': 0.5}, *square[1:]]  # 선 없이 겹치기만 한 초안: 바꿀 정점이 없음
        self.assertIsNone(repair_layout({'nodes': overlapping}, item.question))

    # 검증 목적: 이름표(text)가 값 칸과 같은 자리에 놓인 초안은 이름표만 옆·위 칸으로 옮겨 고치고 값 칸은 그대로 둠
    def test_layout_repair_nudges_overlapping_labels(self):
        item = ITEM.model_copy(update={'question': 'PC가 1200이고 IR은 비어 있다.'})
        nodes = [{'id': 'pc_t', 'label': 'PC', 'shape': 'text', 'x': 1, 'y': 0},
                 {'id': 'pc_v', 'label': '1200', 'shape': 'cell', 'x': 1, 'y': 0}]
        repaired = repair_layout({'nodes': nodes}, item.question)
        self.assertIsNotNone(repaired)
        self.assertEqual(repaired['nodes'][1], nodes[1])
        self.assertNotEqual(repaired['nodes'][0], nodes[0])


class ReviewTests(unittest.TestCase):
    # 검증 목적: 사람 검수에서 뺀 통과 초안은 dropped와 이유가 남아 문항 은행에 들어가지 않고 개수로 보고됨
    def test_mark_dropped_only_changes_passed_drafts(self):
        drafts = [{'id': 'a', 'status': 'ok', 'figure': {}}, {'id': 'b', 'status': 'none', 'figure': None}]
        mark_dropped(drafts, {'a': '정답 암시', 'b': '무시'})
        self.assertEqual([(d['status'], d.get('reason')) for d in drafts], [('dropped', '정답 암시'), ('none', None)])
        self.assertEqual(summarize(drafts)['dropped'], 1)


class ApplyDraftsTests(unittest.TestCase):
    # 검증 목적: 통과한 문항 줄에만 figure를 넣고 다른 줄은 글자 하나 바뀌지 않으며, 통과하지 못한 문항의 옛 figure는 지움
    def test_apply_touches_only_target_lines(self):
        other = {'id': 'queue-d1', 'concept': 'queue', 'question': '큐?'}
        target = {'id': ITEM.id, 'concept': 'shortest-path', 'question': ITEM.question}
        stale = {'id': 'mst-d1', 'concept': 'mst', 'question': 'x', 'figure': {'nodes': []}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'items-x.jsonl'
            path.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in (other, target, stale)) + '\n', encoding='utf-8')
            figure = {'nodes': NODES}
            changed = apply_drafts([{'id': ITEM.id, 'status': 'ok', 'figure': figure},
                                    {'id': 'mst-d1', 'status': 'leak', 'figure': None}], [Path(folder)])
            lines = path.read_text(encoding='utf-8').splitlines()
        self.assertEqual(changed, 1)
        self.assertEqual(lines[0], json.dumps(other, ensure_ascii=False))
        self.assertEqual(json.loads(lines[1])['figure'], figure)
        self.assertNotIn('figure', json.loads(lines[2]))


if __name__ == '__main__':
    unittest.main()
