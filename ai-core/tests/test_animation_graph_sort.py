## @file
# @brief 그래프 탐색(BFS·DFS)·정렬(삽입·병합·퀵)·트리 순회 장면이 참조 구현과 같은 순서·결과를 그리고,
# 실행할 수 없는 요청을 거부하며, 개념별로 허용한 알고리즘만 장면 선택 schema에 남기는지 검증
# - 상태는 코드가 계산한다는 원칙(MVP 2): 프레임의 방문 순서·정렬 결과가 교과서 알고리즘과 다르면 학생이 틀린 과정을 배움
import itertools
import json
import math
import random
import unittest
from collections import deque

from pydantic import ValidationError

from serving.animation import (CONCEPT_SCENE_LIMITS, CONCEPT_SCENES, SCENE_FORMATS, SIMULATORS, check_limits,
                               scene_formats, scene_schema, simulate)
from serving.animation.model import MAX_FRAMES, AnimationRequestModel
from serving.concept_map import load_concept_map

GRAPH = {'nodes': ['A', 'B', 'C', 'D'], 'edges': [['A', 'B'], ['A', 'C'], ['B', 'D'], ['C', 'D']]}


def run(request: dict):
    return simulate(AnimationRequestModel.model_validate(request).root)


## @brief 마지막 프레임의 방문 순서 줄(y=7, id가 v로 시작하는 칸)을 x 순서대로 읽음
def visit_row(animation):
    cells = [b for b in animation.frames[-1].boxes if b.id.startswith('v')]
    return [b.label for b in sorted(cells, key=lambda b: b.x)]


## @brief 프레임의 정렬 배열 줄(y=0 cell)을 x 순서대로 읽음
def array_row(frame):
    return [int(b.label) for b in sorted((b for b in frame.boxes if b.y == 0 and b.shape == 'cell'), key=lambda b: b.x)]


## @brief 점 p에서 선분 a-b까지의 최단 거리(픽셀)
def segment_distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.dist(p, (a[0] + t * dx, a[1] + t * dy))


## @brief 이웃을 이름 오름차순으로 보는 인접 리스트(참조 구현용)
def neighbors(nodes, edges, directed):
    adj = {n: set() for n in nodes}
    for a, b in edges:
        adj[a].add(b)
        if not directed:
            adj[b].add(a)
    return {n: sorted(adj[n]) for n in nodes}


def bfs_reference(nodes, edges, start, directed=False):
    adj, seen, order, queue = neighbors(nodes, edges, directed), {start}, [], deque([start])
    while queue:
        node = queue.popleft()
        order.append(node)
        for nxt in adj[node]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return order


def dfs_reference(nodes, edges, start, directed=False):
    adj, order = neighbors(nodes, edges, directed), []

    def visit(node):
        order.append(node)
        for nxt in adj[node]:
            if nxt not in order:
                visit(nxt)
    visit(start)
    return order


## @brief Lomuto 분할(피벗은 마지막 원소)을 한 번 하고 피벗의 최종 인덱스를 반환
def lomuto_pivot_index(values):
    a, pivot, i = list(values), values[-1], 0
    for j in range(len(a) - 1):
        if a[j] < pivot:
            a[i], a[j] = a[j], a[i]
            i += 1
    return i


class GraphTraversalTest(unittest.TestCase):
    # 검증 목적: BFS 방문 순서가 deque 참조 구현과 같은지(무방향 A-B, A-C, B-D, C-D, 시작 A → A,B,C,D)
    def test_bfs_order_matches_reference(self):
        animation = run({'kind': 'graph_traversal', **GRAPH, 'directed': False, 'start': 'A', 'order': 'bfs'})
        self.assertEqual(visit_row(animation), ['A', 'B', 'C', 'D'])
        self.assertEqual(visit_row(animation), bfs_reference(GRAPH['nodes'], GRAPH['edges'], 'A'))

    # 검증 목적: DFS 방문 순서가 재귀 참조 구현과 같은지(같은 그래프 → A,B,D,C)
    def test_dfs_order_matches_reference(self):
        animation = run({'kind': 'graph_traversal', **GRAPH, 'directed': False, 'start': 'A', 'order': 'dfs'})
        self.assertEqual(visit_row(animation), ['A', 'B', 'D', 'C'])
        self.assertEqual(visit_row(animation), dfs_reference(GRAPH['nodes'], GRAPH['edges'], 'A'))

    # 검증 목적: 방향 그래프는 간선 방향으로만 가고, 닿지 않는 정점은 방문하지 않음. 여러 그래프에서 참조 구현과 비교
    def test_directed_and_more_graphs_match_reference(self):
        cases = [
            (['A', 'B', 'C', 'D', 'E'], [['A', 'B'], ['C', 'A'], ['B', 'D'], ['D', 'E'], ['E', 'B']], True, 'A'),
            (['1', '2', '3', '4', '5', '6'], [['1', '4'], ['1', '2'], ['2', '5'], ['4', '5'], ['3', '6']], False, '1'),
            (['S', 'A', 'B', 'C', 'D', 'E', 'F', 'G'],
             [['S', 'A'], ['S', 'B'], ['A', 'C'], ['B', 'C'], ['C', 'D'], ['D', 'E'], ['E', 'F'], ['F', 'G'], ['B', 'G']], False, 'S'),
        ]
        for nodes, edges, directed, start in cases:
            for order, reference in (('bfs', bfs_reference), ('dfs', dfs_reference)):
                with self.subTest(nodes=nodes, order=order):
                    animation = run({'kind': 'graph_traversal', 'nodes': nodes, 'edges': edges, 'directed': directed,
                                     'start': start, 'order': order})
                    self.assertEqual(visit_row(animation), reference(nodes, edges, start, directed))

    # 검증 목적: 방문 표시(새로 방문 new, 이미 방문 active, 미방문 normal)와 큐·스택 줄이 프레임마다 있는지 확인
    def test_frames_mark_visits_and_show_queue(self):
        animation = run({'kind': 'graph_traversal', **GRAPH, 'directed': False, 'start': 'A', 'order': 'bfs'})
        second = animation.frames[1]
        tones = {b.label: b.tone for b in second.boxes if b.id.startswith('n')}
        self.assertEqual(tones['A'], 'new')
        self.assertEqual(tones['D'], 'normal')
        queue = [b.label for b in sorted((b for b in second.boxes if b.id.startswith('q')), key=lambda b: b.x)]
        self.assertEqual(queue, ['B', 'C'])
        self.assertTrue(any(b.shape == 'label' and b.label == 'queue' for b in second.boxes))
        dfs = run({'kind': 'graph_traversal', **GRAPH, 'directed': False, 'start': 'A', 'order': 'dfs'})
        self.assertTrue(any(b.shape == 'label' and b.label == 'stack' for b in dfs.frames[1].boxes))

    # 검증 목적: 무방향 간선은 화살촉 없는 선 하나, 방향 간선은 화살촉 있는 화살표 하나로 그림
    # - 반대 방향 화살표 두 개를 겹쳐 그리면 양 끝 화살촉 선으로 보여 무방향 그래프를 잘못 읽게 됨
    def test_undirected_edges_drawn_as_plain_lines(self):
        undirected = run({'kind': 'graph_traversal', **GRAPH, 'directed': False, 'start': 'A', 'order': 'bfs'})
        directed = run({'kind': 'graph_traversal', **GRAPH, 'directed': True, 'start': 'A', 'order': 'bfs'})
        self.assertEqual(len(undirected.frames[0].arrows), 4)
        self.assertTrue(all(not arrow.head for arrow in undirected.frames[0].arrows))
        self.assertEqual(len(directed.frames[0].arrows), 4)
        self.assertTrue(all(arrow.head for arrow in directed.frames[0].arrows))
        # 기존 장면(연결 리스트 next 등)은 지정하지 않아도 화살촉 있음
        linked = run({'kind': 'linked_list', 'values': [1, 2], 'ops': [{'op': 'insert_head', 'value': 0}]})
        self.assertTrue(all(arrow.head for frame in linked.frames for arrow in frame.arrows))

    # 검증 목적: 무작위 그래프 수천 개에서 정점끼리 붙거나 간선이 다른 정점 위를 지나지 않는지 확인
    # - 거리는 화면 픽셀(칸 64×56) 기준; 정점은 폭 44·높이 40이라 중심이 120px 이상 떨어지면 사이 선이 보이고,
    #   간선이 다른 정점 중심에서 35px 이상 떨어지면 정점 테두리를 지나지 않음
    def test_layout_keeps_vertices_apart_on_random_graphs(self):
        rng = random.Random(7)
        for _ in range(3000):
            n = rng.randint(2, 8)
            nodes = [chr(ord('A') + i) for i in range(n)]
            directed = rng.random() < 0.5
            pairs = [p for p in itertools.permutations(nodes, 2) if directed or p[0] < p[1]]
            edges = [list(p) for p in rng.sample(pairs, min(len(pairs), rng.randint(0, 12)))]
            request = {'kind': 'graph_traversal', 'nodes': nodes, 'edges': edges, 'directed': directed,
                       'start': rng.choice(nodes), 'order': rng.choice(['bfs', 'dfs'])}
            frame = run(request).frames[0]
            places = {b.id: (b.x * 64, b.y * 56) for b in frame.boxes if b.shape == 'node'}
            with self.subTest(request=request):
                self.assertEqual(len(set(places.values())), n)
                self.assertTrue(all(b.y < 6 for b in frame.boxes if b.shape == 'node'))
                for a, b in itertools.combinations(places.values(), 2):
                    self.assertGreaterEqual(math.dist(a, b), 120)
                for arrow in frame.arrows:
                    ends = places[arrow.source], places[arrow.target]
                    for node, center in places.items():
                        if node not in (arrow.source, arrow.target):
                            self.assertGreaterEqual(segment_distance(center, *ends), 35)

    # 검증 목적: 없는 시작 정점·없는 정점을 잇는 간선·자기 간선·중복 간선·중복 정점 이름은 요청 검증에서 거부
    def test_unknown_start_rejected(self):
        base = {'kind': 'graph_traversal', **GRAPH, 'directed': False, 'start': 'A', 'order': 'bfs'}
        bad = [{'start': 'Z'}, {'edges': [['A', 'Z']]}, {'edges': [['A', 'A']]}, {'edges': [['A', 'B'], ['B', 'A']]},
               {'nodes': ['A', 'A', 'B']}]
        for change in bad:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                AnimationRequestModel.model_validate({**base, **change})


class SortTest(unittest.TestCase):
    # 검증 목적: 세 알고리즘 모두 마지막 프레임 배열이 정렬 결과와 같고, 퀵 정렬 첫 분할 뒤 피벗 위치가 Lomuto 참조와 같은지 확인
    def test_sort_final_sorted_and_steps(self):
        for values in ([5, 2, 9, 1, 7], [3, 1, 2], [8, 7, 6, 5, 4, 3, 2, 1], [4, 4, 1, 3, 4], [10, -5, 999, 0]):
            for algorithm in ('insertion', 'merge', 'quick'):
                with self.subTest(values=values, algorithm=algorithm):
                    animation = run({'kind': 'sort', 'algorithm': algorithm, 'values': values})
                    self.assertEqual(array_row(animation.frames[0]), values)
                    self.assertEqual(array_row(animation.frames[-1]), sorted(values))
            first_partition = run({'kind': 'sort', 'algorithm': 'quick', 'values': values}).frames[1]
            pivot = [b for b in first_partition.boxes if b.y == 0 and b.tone == 'active']
            self.assertEqual(len(pivot), 1)
            self.assertEqual(pivot[0].x, lomuto_pivot_index(values))
            self.assertEqual(int(pivot[0].label), values[-1])

    # 검증 목적: 같은 값이 있어도 원소 상자 id가 프레임 사이에 이어져(이동 애니메이션), 삽입·병합 정렬은 안정 정렬로 보임
    def test_stable_ids_for_equal_values(self):
        animation = run({'kind': 'sort', 'algorithm': 'insertion', 'values': [2, 1, 2]})
        first = {b.id: b.label for b in animation.frames[0].boxes if b.y == 0}
        last = sorted((b for b in animation.frames[-1].boxes if b.y == 0), key=lambda b: b.x)
        self.assertEqual(set(first), {b.id for b in last})
        twos = [b.id for b in last if b.label == '2']
        self.assertEqual(twos, [i for i, label in first.items() if label == '2'])


class TreeTraversalTest(unittest.TestCase):
    # 검증 목적: values를 차례로 BST에 넣은 트리의 전위·중위·후위 순회가 교과서 결과와 같은지 확인
    def test_tree_traversal_orders(self):
        expected = {'inorder': ['1', '3', '6', '8', '10'], 'preorder': ['8', '3', '1', '6', '10'],
                    'postorder': ['1', '6', '3', '10', '8']}
        for order, visits in expected.items():
            with self.subTest(order=order):
                animation = run({'kind': 'tree_traversal', 'values': [8, 3, 10, 1, 6], 'order': order})
                self.assertEqual(visit_row(animation), visits)
                self.assertEqual(len(animation.frames), len(visits) + 1)

    # 검증 목적: 중복 값(BST에 넣을 수 없음)과 그릴 수 없는 높이는 한국어 오류로 거부
    def test_tree_rejects_duplicates_and_tall_trees(self):
        for values in ([5, 3, 5], [1, 2, 3, 4, 5, 6, 7, 8]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                run({'kind': 'tree_traversal', 'values': values, 'order': 'inorder'})


class LimitsTest(unittest.TestCase):
    # 검증 목적: 각 장면의 가장 큰 입력도 MAX_FRAMES·격자 안에서 그려지는지 확인(분량 초과로 장면이 조용히 빠지지 않게)
    def test_frame_limit(self):
        nodes = list('ABCDEFGH')
        edges = [[a, b] for a, b in zip(nodes, nodes[1:])] + [['A', 'C'], ['A', 'E'], ['B', 'F'], ['C', 'G'], ['D', 'H']]
        requests = [{'kind': 'graph_traversal', 'nodes': nodes, 'edges': edges, 'directed': d, 'start': 'A', 'order': o}
                    for d in (False, True) for o in ('bfs', 'dfs')]
        requests += [{'kind': 'sort', 'algorithm': a, 'values': [999, 7, -99, 5, 4, 3, 2, 1]} for a in ('insertion', 'merge', 'quick')]
        requests += [{'kind': 'tree_traversal', 'values': [50, 30, 70, 20, 40, 60, 80, 10, 90], 'order': o}
                     for o in ('preorder', 'inorder', 'postorder')]
        for request in requests:
            with self.subTest(request=request):
                self.assertLessEqual(len(run(request).frames), MAX_FRAMES)
        with self.assertRaises(ValidationError):
            AnimationRequestModel.model_validate({'kind': 'sort', 'algorithm': 'merge', 'values': list(range(9))})


class ConceptScenesTest(unittest.TestCase):
    # 검증 목적: 새 개념 7개가 계획대로 장면과 이어지고, 제한표가 지도 ID·장면 필드만 쓰는지 확인
    def test_new_concepts_and_limits(self):
        expected = {'bfs': 'graph_traversal', 'graph': 'graph_traversal', 'dfs': 'graph_traversal', 'sorting-basics': 'sort',
                    'merge-sort': 'sort', 'quick-sort': 'sort', 'tree-traversal': 'tree_traversal'}
        for concept, kind in expected.items():
            self.assertEqual(CONCEPT_SCENES[concept], (kind,), concept)
        self.assertEqual(CONCEPT_SCENE_LIMITS['bfs'], {'order': ('bfs',)})
        self.assertEqual(CONCEPT_SCENE_LIMITS['graph'], {'order': ('bfs',)})
        self.assertEqual(CONCEPT_SCENE_LIMITS['dfs'], {'order': ('dfs',)})
        self.assertEqual(CONCEPT_SCENE_LIMITS['sorting-basics'], {'algorithm': ('insertion',)})
        self.assertLessEqual(set(CONCEPT_SCENE_LIMITS), set(CONCEPT_SCENES))
        self.assertLessEqual(set(CONCEPT_SCENES), {c.id for c in load_concept_map().concepts})

    # 검증 목적: priority-queue 보충 장면은 최소 힙만 허용(schema enum·형식 예시·응답 검증 모두)
    # - 이 저장소의 priority-queue 문항은 모두 "숫자가 작을수록 먼저"인데, 운영 E2E(2026-10-26)에서 최대 힙 장면이 나와 어긋났음
    # - heap·heap-sort는 최대·최소 모두 다루므로 제한하지 않음
    def test_priority_queue_uses_min_heap(self):
        limits = CONCEPT_SCENE_LIMITS['priority-queue']
        self.assertEqual(limits, {'order': ('min',)})
        (option,) = scene_schema(CONCEPT_SCENES['priority-queue'], limits)['properties']['scene']['anyOf']
        self.assertEqual(option['properties']['order']['enum'], ['min'])
        self.assertEqual(scene_formats(CONCEPT_SCENES['priority-queue'], limits)['heap']['order'], 'min')
        max_heap = AnimationRequestModel.model_validate(dict(SCENE_FORMATS['heap'], order='max')).root
        with self.assertRaises(ValueError):
            check_limits(max_heap, limits)
        check_limits(AnimationRequestModel.model_validate(dict(SCENE_FORMATS['heap'], order='min')).root, limits)
        self.assertNotIn('heap', CONCEPT_SCENE_LIMITS)
        self.assertNotIn('heap-sort', CONCEPT_SCENE_LIMITS)

    # 검증 목적: merge-sort의 장면 선택 schema가 algorithm merge만 허용하고, 형식 예시도 merge로 바뀌는지 확인
    # - 가이드 디코딩이 허용 밖 알고리즘을 형식 단계에서 막아 "병합 정렬 보충에 삽입 정렬 장면"이 나오지 않게 함
    def test_concept_scenes_restrict_algorithm(self):
        schema = scene_schema(CONCEPT_SCENES['merge-sort'], CONCEPT_SCENE_LIMITS['merge-sort'])
        (option,) = schema['properties']['scene']['anyOf']
        self.assertEqual(option['properties']['algorithm']['enum'], ['merge'])
        for keyword in ('$ref', 'discriminator', 'oneOf'):
            self.assertNotIn(keyword, json.dumps(schema))
        formats = scene_formats(CONCEPT_SCENES['merge-sort'], CONCEPT_SCENE_LIMITS['merge-sort'])
        self.assertEqual(formats['sort']['algorithm'], 'merge')
        self.assertEqual(SCENE_FORMATS['sort']['algorithm'], 'insertion')
        bfs = scene_schema(CONCEPT_SCENES['dfs'], CONCEPT_SCENE_LIMITS['dfs'])['properties']['scene']['anyOf'][0]
        self.assertEqual(bfs['properties']['order']['enum'], ['dfs'])
        tree = scene_schema(CONCEPT_SCENES['tree-traversal'])['properties']['scene']['anyOf'][0]
        self.assertEqual(set(tree['properties']['order']['enum']), {'preorder', 'inorder', 'postorder'})

    # 검증 목적: 형식 예시가 모든 장면 종류에 있고, 그대로 검증·실행되는지 확인(틀린 예시는 모델이 틀린 형식을 따라 하게 함)
    def test_scene_formats_cover_new_kinds(self):
        self.assertEqual(set(SCENE_FORMATS), set(SIMULATORS))
        for kind in ('graph_traversal', 'sort', 'tree_traversal'):
            self.assertEqual(run(SCENE_FORMATS[kind]).kind, kind)


if __name__ == '__main__':
    unittest.main()
