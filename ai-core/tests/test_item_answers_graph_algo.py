# 파일 역할: 묶음 5(위상 정렬·최단 경로·탐욕법·최소 신장 트리·동적 계획법) 문항의 구성을 검사하고, 단답 정답을 알고리즘을 실제로 돌려 독립 계산해 대조
import math
import re
import unittest
from collections import Counter, deque
from itertools import combinations

from serving.concept_map import load_concept_map
from serving.item_bank import load_item_bank, normalize_short_answer

SCOPE = {'topological-sort', 'shortest-path', 'greedy', 'mst', 'dynamic-programming'}
# 오답에만 몰리면 지식 없이 오답을 지울 단서가 되는 극단 표현(CS-300 선택형이 단서로 풀린 원인)
EXTREME_WORDS = ('항상', '반드시', '절대', '전혀', '무조건')


# 역할: 문항 속 간선 표기를 (a, b, 가중치) 목록으로 읽음; "a→b"는 방향, "a-b"는 무방향, ":w"가 없으면 가중치 None
def edges(question):
    return [(int(a), int(b), None if w == '' else int(w))
            for a, _, b, w in re.findall(r'(\d+)(-|→)(\d+)(?::(-?\d+))?', question)]


# 역할: 진입 차수와 큐로 위상 정렬(진입 차수 0인 정점은 작은 번호부터, 이웃도 작은 번호부터 처리); 꺼낸 순서와 끝까지 남은 정점 반환
def kahn(pairs):
    vertices = sorted({v for a, b, _ in pairs for v in (a, b)})
    indegree = {v: 0 for v in vertices}
    out = {v: [] for v in vertices}
    for a, b, _ in pairs:
        out[a].append(b)
        indegree[b] += 1
    queue, order = deque(v for v in vertices if indegree[v] == 0), []
    while queue:
        v = queue.popleft()
        order.append(v)
        for n in sorted(out[v]):
            indegree[n] -= 1
            if indegree[n] == 0:
                queue.append(n)
    return order, [v for v in vertices if v not in order]


# 역할: 다익스트라; 확정되지 않은 정점 중 거리가 가장 작은 것(같으면 작은 번호)을 확정하고, 확정된 정점은 다시 갱신하지 않음
# - confirmed만큼 확정한 직후의 거리 표를 반환해 중간 상태 문항도 계산함
def dijkstra(pairs, start, confirmed=None):
    vertices = sorted({v for a, b, _ in pairs for v in (a, b)})
    distance, done = {v: math.inf for v in vertices}, set()
    distance[start] = 0
    while len(done) < len(vertices) and (confirmed is None or len(done) < confirmed):
        v = min((u for u in vertices if u not in done and distance[u] < math.inf), key=lambda u: (distance[u], u), default=None)
        if v is None:
            break
        done.add(v)
        for a, b, w in pairs:
            if a == v and b not in done:
                distance[b] = min(distance[b], distance[v] + w)
    return distance


# 역할: 벨만-포드로 음의 간선이 있어도 맞는 최단 거리; 다익스트라 결과와 비교할 기준
def bellman_ford(pairs, start):
    vertices = {v for a, b, _ in pairs for v in (a, b)}
    distance = {v: math.inf for v in vertices}
    distance[start] = 0
    for _ in range(len(vertices) - 1):
        for a, b, w in pairs:
            distance[b] = min(distance[b], distance[a] + w)
    return distance


# 역할: 끝나는 시각이 빠른 회의부터 겹치지 않게 고름(앞 회의가 끝나는 시각에 시작해도 됨)
def earliest_end(meetings):
    chosen, end = [], -math.inf
    for start, finish in sorted(meetings, key=lambda m: m[1]):
        if start >= end:
            chosen.append((start, finish))
            end = finish
    return chosen


def meetings_in(question):
    return [(int(s), int(e)) for s, e in re.findall(r'\((\d+),\s?(\d+)\)', question)]


# 역할: 큰 동전부터 고르는 탐욕 거스름돈의 동전 수와, 동적 계획법으로 구한 최소 동전 수
def greedy_coins(coins, amount):
    count = 0
    for coin in sorted(coins, reverse=True):
        count, amount = count + amount // coin, amount % coin
    return count


def fewest_coins(coins, amount):
    best = [0] + [math.inf] * amount
    for value in range(1, amount + 1):
        best[value] = min(best[value - c] + 1 for c in coins if c <= value)
    return best[amount]


# 역할: 서로소 집합으로 크루스칼; 가중치 작은 간선부터 사이클을 만들지 않는 것만 고름
def kruskal(pairs):
    parent = {}

    def root(v):
        parent.setdefault(v, v)
        while parent[v] != v:
            v = parent[v]
        return v
    chosen = []
    for a, b, w in sorted(pairs, key=lambda e: e[2]):
        if root(a) != root(b):
            parent[root(a)] = root(b)
            chosen.append(w)
    return chosen


# 역할: 프림; 이미 이어진 정점들에서 나가는 가장 가벼운 간선으로 새 정점을 하나씩 붙임
def prim(pairs, start):
    joined = [start]
    vertices = {v for a, b, _ in pairs for v in (a, b)}
    while len(joined) < len(vertices):
        crossing = [(w, b if a in joined else a) for a, b, w in pairs if (a in joined) != (b in joined)]
        joined.append(min(crossing)[1])
    return joined


# 역할: 리프(차수 1 이하)를 반복해 지워 사이클 위 정점만 남김(2-코어)
def cycle_vertices(pairs):
    alive = [(a, b) for a, b, _ in pairs]
    while True:
        degree = Counter(v for e in alive for v in e)
        leaves = {v for v, d in degree.items() if d <= 1}
        if not leaves:
            return set(degree)
        alive = [e for e in alive if not set(e) & leaves]


# 역할: 문항 첫 줄 뒤의 코드를 실제로 정의하고 fib 호출을 감싸 인자별 호출 횟수를 셈
def fib_calls(question, n):
    scope = {}
    exec(question.split('\n', 1)[1], scope)
    original, calls = scope['fib'], Counter()

    def counted(k):
        calls[k] += 1
        return original(k)
    scope['fib'] = counted
    counted(n)
    return calls


# 역할: 오른쪽·아래로만 움직이는 경로를 모두 나열해 막힌 칸을 피하는 것만 셈; 점화식 표를 쓰지 않고 정답을 확인하기 위함
def grid_paths(row, col, blocked):
    total = 0
    for downs in combinations(range(row + col), row):
        r = c = 0
        ok = (r, c) != blocked
        for step in range(row + col):
            r, c = (r + 1, c) if step in downs else (r, c + 1)
            ok = ok and (r, c) != blocked
        total += ok
    return total


def join(values):
    return ','.join(str(v) for v in values)


# 역할: 문항 ID → 정답 키와 별도로 계산한 기대 답
def expected_answers(items):
    q = {item.id: item.question for item in items}
    popped, left = kahn(edges(q['topological-sort-c2']))
    table = dijkstra(edges(q['shortest-path-c1']), 1, confirmed=3)
    negative = edges(q['shortest-path-c2'])
    first_four = sorted(edges(q['mst-c2']), key=lambda e: e[2])[:4]  # 사이클 검사 없이 가벼운 간선 4개
    return {
        'topological-sort-d1': join(kahn(edges(q['topological-sort-d1']))[0]),
        'topological-sort-c1': join(kahn(edges(q['topological-sort-c1']))[0]),
        'topological-sort-c2': join([len(popped), len(left)]),
        'shortest-path-d1': join(dijkstra(edges(q['shortest-path-d1']), 1)[v] for v in (2, 3, 4)),
        'shortest-path-c1': join(-1 if table[v] == math.inf else table[v] for v in (4, 5, 6)),
        'shortest-path-c2': join([dijkstra(negative, 1)[2], bellman_ford(negative, 1)[2]]),
        'greedy-d1': len(earliest_end(meetings_in(q['greedy-d1']))),
        'greedy-c1': join([greedy_coins((1, 3, 4), 6), fewest_coins((1, 3, 4), 6)]),
        'greedy-c2': join(start for start, _ in earliest_end(meetings_in(q['greedy-c2']))),
        'mst-d1': join(kruskal(edges(q['mst-d1']))),
        'mst-c1': join(prim(edges(q['mst-c1']), 1)),
        'mst-c2': join([len(cycle_vertices(first_four)), sum(kruskal(edges(q['mst-c2'])))]),
        'dynamic-programming-d1': fib_calls(q['dynamic-programming-d1'], 6)[2],
        'dynamic-programming-c1': sum(fib_calls(q['dynamic-programming-c1'], 10).values()),
        'dynamic-programming-c2': join([grid_paths(1, 3, (1, 1)), grid_paths(2, 3, (1, 1))]),
    }


class GraphAlgoItemTests(unittest.TestCase):
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

    # 검증 목적: 동전 단위 선택형은 금액 1~100에서 탐욕 결과가 최소 개수와 다른 단위가 정답 하나뿐인지 확인
    def test_greedy_coin_choice_is_unique(self):
        item = next(i for i in self.items if i.id == 'greedy-d2')
        fails = [label for label, text in item.options.items()
                 if any(greedy_coins(coins, a) != fewest_coins(coins, a)
                        for coins in [tuple(int(v) for v in re.findall(r'\d+', text))] for a in range(1, 101))]
        self.assertEqual(fails, [item.answer])

    # 검증 목적: 모든 단답 정답이 문항 내용으로부터 독립 계산한 값과 일치하는지 확인
    def test_short_answers_match_independent_computation(self):
        short = {i.id: i for i in self.items if i.format == 'short_exact'}
        expected = expected_answers(self.items)
        self.assertEqual(set(expected), set(short))
        for item_id, value in expected.items():
            self.assertEqual(normalize_short_answer(str(value)), short[item_id].answer, item_id)


if __name__ == '__main__':
    unittest.main()
