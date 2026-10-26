# 파일 역할: 묶음 4(힙 정렬·그래프·그래프 표현·BFS·DFS) 문항의 구성을 검사하고, 단답 정답을 문항 속 그래프를 실제로 만들어 탐색해 독립 계산해 대조
import math
import random
import re
import unittest
from collections import Counter, deque

from serving.concept_map import load_concept_map
from serving.item_bank import load_item_bank, normalize_short_answer

SCOPE = {'heap-sort', 'graph', 'graph-representation', 'bfs', 'dfs'}
# 오답에만 몰리면 지식 없이 오답을 지울 단서가 되는 극단 표현(CS-300 선택형이 단서로 풀린 원인)
EXTREME_WORDS = ('항상', '반드시', '절대', '전혀', '무조건')


# 역할: 문항 속 간선 표기("1-2"는 무방향, "1→2"는 방향)를 읽어 {정점: 이웃 정렬 목록} 반환; 이웃을 작은 번호부터 보는 규칙을 그대로 따르기 위함
def graph(question):
    adjacency = {}
    for a, kind, b in re.findall(r'(\d+)(-|→)(\d+)', question):
        a, b = int(a), int(b)
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set())
        if kind == '-':
            adjacency[b].add(a)
    return {v: sorted(ns) for v, ns in adjacency.items()}


# 역할: 최대 힙 아래로 재정렬; 앞쪽 size칸만 힙으로 봄
def sift_down_max(values, i, size):
    while 2 * i + 1 < size:
        child = 2 * i + 1
        if child + 1 < size and values[child + 1] > values[child]:
            child += 1
        if values[child] <= values[i]:
            break
        values[i], values[child] = values[child], values[i]
        i = child


# 역할: 큐로 BFS; 꺼낸 정점의 이웃을 작은 번호부터 넣고 넣을 때 방문 표시. stop을 꺼내 이웃을 넣은 직후 큐도 함께 반환
def bfs(adjacency, start, stop=None):
    order, distance, queue = [], {start: 0}, deque([start])
    snapshot = None
    while queue:
        v = queue.popleft()
        order.append(v)
        for n in adjacency[v]:
            if n not in distance:
                distance[n] = distance[v] + 1
                queue.append(n)
        if v == stop:
            snapshot = list(queue)
    return order, distance, snapshot


# 역할: 재귀 DFS; 미방문 이웃을 작은 번호부터 따라 내려감
def dfs(adjacency, v, seen=None):
    seen = seen if seen is not None else []
    seen.append(v)
    for n in adjacency[v]:
        if n not in seen:
            dfs(adjacency, n, seen)
    return seen


# 역할: 스택 DFS; 꺼낸 정점이 미방문이면 방문하고 이웃을 큰 번호부터 넣음(작은 번호가 먼저 나오게)
def stack_dfs(adjacency, start):
    seen, stack = [], [start]
    while stack:
        v = stack.pop()
        if v not in seen:
            seen.append(v)
            stack.extend(sorted(adjacency[v], reverse=True))
    return seen


# 역할: 무방향 그래프의 연결 요소 목록과 요소마다의 간선 수
def components(adjacency):
    found, seen = [], set()
    for v in sorted(adjacency):
        if v not in seen:
            part = set(bfs(adjacency, v)[0])
            seen |= part
            edges = sum(len(adjacency[u]) for u in part) // 2
            found.append((part, edges))
    return found


def join(values):
    return ','.join(str(v) for v in values)


# 역할: 문항 ID → 정답 키와 별도로 계산한 기대 답
def expected_answers(items):
    q = {item.id: item.question for item in items}
    built = [4, 10, 3, 5, 1]
    for i in range(len(built) // 2 - 1, -1, -1):  # 마지막 부모부터 루트까지 아래로 재정렬
        sift_down_max(built, i, len(built))
    extracted = [9, 7, 8, 3, 5, 6]
    extracted[0], extracted[-1] = extracted[-1], extracted[0]  # 최댓값을 끝으로 보내고 남은 5칸만 재정렬
    sift_down_max(extracted, 0, len(extracted) - 1)
    depth, index = 0, 1021  # 1022개 힙의 마지막 칸이 가장 깊은 층
    while index > 0:
        index, depth = (index - 1) // 2, depth + 1
    parts = components(graph(q['graph-d1']))
    directed = graph(q['graph-c1'])
    reach = lambda v: set(bfs(directed, v)[0])
    on_cycle = [v for v in directed if any(v in reach(n) for n in directed[v])]  # 이웃에서 다시 자기에게 돌아올 수 있는 정점
    tree_like = graph(q['graph-c2'])
    edge_count = sum(len(ns) for ns in tree_like.values()) // 2
    matrix_graph = graph(q['graph-representation-c1'])
    rng, pairs = random.Random(0), set()  # 간선 300개짜리 임의 그래프를 실제로 만들어 인접 리스트 항목을 셈
    while len(pairs) < 300:
        a, b = rng.sample(range(100), 2)
        pairs.add((min(a, b), max(a, b)))
    matrix = [[0] * 100 for _ in range(100)]
    lists = {v: [] for v in range(100)}
    for a, b in pairs:
        lists[a].append(b)
        lists[b].append(a)
    shortest = bfs(graph(q['bfs-c2']), 1)[1]
    compare = graph(q['dfs-c2'])
    return {
        'heap-sort-d1': join(built),
        'heap-sort-c1': join(extracted),
        'heap-sort-c2': depth,
        'graph-d1': join([len(parts), sum(edges >= len(part) for part, edges in parts)]),
        'graph-c1': join([len(reach(1) - {1}), len(on_cycle)]),
        'graph-c2': edge_count - (len(tree_like) - len(components(tree_like))),
        'graph-representation-d1': join(graph(q['graph-representation-d1'])[2]),
        'graph-representation-c1': join(int(col in matrix_graph[4]) for col in range(1, 5)),
        'graph-representation-c2': join([sum(len(row) for row in matrix), sum(len(ns) for ns in lists.values())]),
        'bfs-d1': join(bfs(graph(q['bfs-d1']), 1)[0]),
        'bfs-c1': join(bfs(graph(q['bfs-c1']), 1, stop=3)[2]),
        'bfs-c2': join(shortest[v] for v in range(2, 6)),
        'dfs-d1': join(dfs(graph(q['dfs-d1']), 1)),
        'dfs-c1': join(stack_dfs(graph(q['dfs-c1']), 1)),
        'dfs-c2': join([bfs(compare, 1)[0][3], dfs(compare, 1)[3]]),
    }


class GraphItemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = [i for i in load_item_bank() if i.concept in SCOPE]

    # 검증 목적: 개념마다 진단 2·확인 2·서술 1이 있고, 모든 학습 목표가 자동 채점 문항으로 확인되는지 확인
    def test_each_concept_has_full_set_and_objective_coverage(self):
        concepts = {c.id: c for c in load_concept_map().concepts}
        self.assertEqual({i.concept for i in self.items}, SCOPE)
        for concept in SCOPE:
            own = [i for i in self.items if i.concept == concept]
            self.assertEqual(Counter(i.purpose for i in own), {'diagnosis': 2, 'check': 2, 'explanation': 1}, concept)
            graded = {i.objective for i in own if i.purpose != 'explanation'}
            self.assertEqual(graded, set(range(len(concepts[concept].objectives))), concept)

    # 검증 목적: 선택형 정답 위치가 한쪽으로 몰려 위치만으로 맞히는 일을 막음
    def test_choice_answers_are_balanced(self):
        labels = Counter(i.answer for i in self.items if i.format == 'multiple_choice')
        self.assertEqual(set(labels), set('ABCD'))
        self.assertLessEqual(max(labels.values()), math.ceil(sum(labels.values()) / 4) + 1)

    # 검증 목적: 보기만 보고 정답을 고르는 단서를 막음; 정답만 유독 길거나 극단 표현이 오답에만 있으면 실패
    def test_choices_give_no_surface_cue(self):
        for item in (i for i in self.items if i.format == 'multiple_choice'):
            lengths = {label: len(text) for label, text in item.options.items()}
            others = [v for label, v in lengths.items() if label != item.answer]
            self.assertLessEqual(lengths[item.answer], max(others), item.id)
            wrong_extreme = any(w in text for label, text in item.options.items() if label != item.answer for w in EXTREME_WORDS)
            self.assertFalse(wrong_extreme and not any(w in item.options[item.answer] for w in EXTREME_WORDS), item.id)

    # 검증 목적: 모든 단답 정답이 문항 내용으로부터 독립 계산한 값과 일치하는지 확인
    def test_short_answers_match_independent_computation(self):
        short = {i.id: i for i in self.items if i.format == 'short_exact'}
        expected = expected_answers(self.items)
        self.assertEqual(set(expected), set(short))
        for item_id, value in expected.items():
            self.assertEqual(normalize_short_answer(str(value)), short[item_id].answer, item_id)


if __name__ == '__main__':
    unittest.main()
