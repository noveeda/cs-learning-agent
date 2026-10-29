## @file
# @brief 자료구조·알고리즘(dsa) 과목의 새 장면 시뮬레이터와 개념별 예시 장면(scenes.json)을 검증
# - 시뮬레이터: 계산한 상태가 실제 알고리즘 결과와 같은지, 경계 입력과 실행할 수 없는 입력을 한국어 오류로 거부하는지 확인
# - scenes.json: 화면에 남는 dsa 개념마다 서로 다른 장면 3개가 있고, 모두 검증·실행되며 분량 상한을 지키는지 확인
import json
import unittest
from pathlib import Path

from pydantic import ValidationError
from serving.animation import SCENE_FORMATS, SIMULATORS, simulate
from serving.animation.model import AnimationRequestModel

SCENES_PATH = Path(__file__).resolve().parents[1] / 'content' / 'dsa' / 'scenes.json'
# 화면에 남는 dsa 개념(과목 안 선수 관계 5단계 이하). 6단계 이상 18개는 화면에서 가려 장면을 만들지 않음
VISIBLE_CONCEPTS = {
    'memory-address', 'reference', 'recursion', 'logarithm', 'big-o', 'complexity-analysis', 'recurrence-relation',
    'array', 'dynamic-array', 'linked-list', 'hash-function', 'tree', 'binary-tree', 'binary-search', 'heap',
    'sorting-basics', 'graph', 'dynamic-programming',
}
# 이 과목이 추가한 장면 종류
DSA_KINDS = {'dsa_memory', 'dsa_reference', 'dynamic_array', 'list_walk', 'dsa_search', 'selection_sort',
             'log_steps', 'growth', 'loop_count', 'recursion_tree', 'dsa_recursion', 'fib_dp', 'dsa_tree', 'graph_parts'}
# 장면 하나의 권장 프레임 수 상한: 막힘 보충 한 번에 끝까지 따라갈 수 있는 분량
SCENE_FRAME_LIMIT = 12
# 라벨 글자 수 상한: 테두리 상자(cell·node·slot)는 세 글자까지 줄이지 않고 들어감, 이름표(label)는 칸 두 개 폭
BOX_LABEL_LIMIT = 3
NAME_LABEL_LIMIT = 12


## @brief dict 요청을 스키마로 검증한 뒤 시뮬레이터를 실행
def run(data: dict):
    return simulate(AnimationRequestModel.model_validate(data).root)


## @brief 프레임에서 지정 모양 상자의 라벨을 x(왼→오), y(위→아래) 순서로 꺼냄
def labels(frame, shape='cell', y=None):
    boxes = (b for b in frame.boxes if b.shape == shape and (y is None or b.y == y))
    return [b.label for b in sorted(boxes, key=lambda b: (b.y, b.x))]


## @brief 지정 tone인 상자의 라벨 집합
def toned(frame, tone):
    return {b.label for b in frame.boxes if b.tone == tone}


class RegistryTests(unittest.TestCase):
    # 검증 목적: 새 장면 종류가 요청 스키마·시뮬레이터·형식 예시에 빠짐없이 등록되고 예시가 그대로 실행되는지 확인
    # - 셋 중 하나라도 빠지면 막힘 장면 선택·채팅 도구 검증이 어긋남
    def test_dsa_kinds_registered_with_runnable_formats(self):
        self.assertLessEqual(DSA_KINDS, set(SIMULATORS))
        for kind in DSA_KINDS:
            self.assertEqual(run(SCENE_FORMATS[kind]).kind, kind)


class MemoryTests(unittest.TestCase):
    # 검증 목적: i번째 원소 주소를 시작 주소 + 원소 크기 × i로 계산하고, 주소 이름표가 그 값과 같은지 확인
    def test_access_computes_address_from_base_and_width(self):
        animation = run({'kind': 'dsa_memory', 'base': 100, 'width': 4, 'values': [7, 3, 9], 'ops': [{'op': 'access', 'index': 2}]})
        self.assertEqual(labels(animation.frames[0], 'label', y=0), ['100', '104', '108'])
        self.assertIn('100 + 4×2 = 108', animation.frames[1].caption)
        self.assertEqual(toned(animation.frames[1], 'active'), {'9', '108'})

    # 검증 목적: 없는 인덱스 접근과 허용하지 않은 원소 크기는 그림 없이 거부되는지 확인
    def test_rejects_out_of_range_and_bad_width(self):
        with self.assertRaises(ValueError):
            run({'kind': 'dsa_memory', 'base': 100, 'width': 4, 'values': [7], 'ops': [{'op': 'access', 'index': 1}]})
        with self.assertRaises(ValidationError):
            run({'kind': 'dsa_memory', 'base': 100, 'width': 3, 'values': [7], 'ops': [{'op': 'access', 'index': 0}]})


class ReferenceTests(unittest.TestCase):
    # 검증 목적: 값 복사는 독립 칸, 참조 복사는 같은 객체를 가리켜 한쪽 수정이 다른 쪽에서도 보이는지 확인
    def test_value_copy_is_independent_and_reference_copy_aliases(self):
        ops = [{'op': 'let', 'name': 'a', 'value': 5}, {'op': 'copy', 'name': 'b', 'source': 'a'},
               {'op': 'set', 'name': 'b', 'value': 8}, {'op': 'new', 'name': 'p', 'value': 7},
               {'op': 'copy', 'name': 'q', 'source': 'p'}, {'op': 'set', 'name': 'q', 'value': 9}]
        animation = run({'kind': 'dsa_reference', 'ops': ops})
        third = animation.frames[3]
        self.assertEqual(labels(third, 'cell', y=0), ['5'])
        self.assertEqual(labels(third, 'cell', y=1), ['8'])
        last = animation.frames[-1]
        objects = [b for b in last.boxes if b.shape == 'node']
        self.assertEqual([b.label for b in objects], ['9'])
        self.assertEqual(sorted(a.target for a in last.arrows), [objects[0].id, objects[0].id])
        self.assertIn('p', last.caption)

    # 검증 목적: 참조를 다른 객체로 바꾸면 아무도 가리키지 않는 이전 객체가 그 단계에서 removed로 보이는지 확인
    def test_rebinding_marks_unreachable_object(self):
        ops = [{'op': 'new', 'name': 'p', 'value': 4}, {'op': 'new', 'name': 'p', 'value': 6}, {'op': 'read', 'name': 'p'}]
        animation = run({'kind': 'dsa_reference', 'ops': ops})
        self.assertIn('4', toned(animation.frames[2], 'removed'))
        self.assertNotIn('4', {b.label for b in animation.frames[3].boxes})
        self.assertIn('6', animation.frames[3].caption)

    # 검증 목적: 만들지 않은 변수를 복사·수정·읽기하면 거부되는지 확인
    def test_unknown_name_rejected(self):
        with self.assertRaises(ValueError):
            run({'kind': 'dsa_reference', 'ops': [{'op': 'read', 'name': 'x'}]})


class DynamicArrayTests(unittest.TestCase):
    # 검증 목적: 용량이 차면 두 배 배열을 새로 잡고 기존 원소를 복사하며, 누적 복사 수가 1+2+4로 맞는지 확인
    def test_doubling_resizes_and_counts_copies(self):
        animation = run({'kind': 'dynamic_array', 'capacity': 1, 'growth': 'double', 'values': [1, 2, 3, 4, 5]})
        self.assertEqual(len(animation.frames), 1 + 5 + 3)
        last = animation.frames[-1]
        self.assertEqual([b.label for b in sorted((b for b in last.boxes if b.shape == 'cell'), key=lambda b: b.x)],
                         ['1', '2', '3', '4', '5'])
        self.assertEqual(sum(b.shape == 'slot' for b in last.boxes), 3)
        self.assertIn('복사 7회', {b.label for b in last.boxes})

    # 검증 목적: 한 칸씩 늘리면 추가할 때마다 재할당·복사가 일어나는지 확인(두 배 증가와 비교하는 장면)
    def test_plus_one_growth_copies_every_time(self):
        animation = run({'kind': 'dynamic_array', 'capacity': 1, 'growth': 'plus1', 'values': [1, 2, 3, 4]})
        self.assertEqual(len(animation.frames), 1 + 4 + 3)
        self.assertIn('복사 6회', {b.label for b in animation.frames[-1].boxes})

    # 검증 목적: 격자 폭(12칸)을 넘는 용량이 필요하면 거부되는지 확인
    def test_rejects_capacity_beyond_grid(self):
        with self.assertRaises(ValueError):
            run({'kind': 'dynamic_array', 'capacity': 4, 'growth': 'double', 'values': list(range(9))})


class ListWalkTests(unittest.TestCase):
    # 검증 목적: i번째 노드까지 cur가 next를 i번 따라가며 한 칸씩 이동하는지 확인
    def test_cur_follows_next_to_index(self):
        animation = run({'kind': 'list_walk', 'values': [4, 8, 15, 16], 'index': 2})
        self.assertEqual(len(animation.frames), 2 + 2)
        cur_x = [next(b.x for b in f.boxes if b.id == 'cur') for f in animation.frames[1:]]
        self.assertEqual(cur_x, [0, 1, 2])
        self.assertIn('15', animation.frames[-1].caption)

    # 검증 목적: 리스트 길이를 넘는 인덱스는 거부되는지 확인
    def test_index_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            run({'kind': 'list_walk', 'values': [1, 2], 'index': 2})


class SearchTests(unittest.TestCase):
    # 검증 목적: 이진 탐색의 mid 순서가 표준 알고리즘((lo+hi)//2)과 같고 비교 횟수가 맞는지 확인
    def test_binary_search_mid_sequence(self):
        values = [1, 3, 5, 7, 9, 11, 13, 15]
        animation = run({'kind': 'dsa_search', 'algorithm': 'binary', 'values': values, 'target': 13})
        mids = [next(b.x for b in f.boxes if b.id == 'mid') for f in animation.frames if any(b.id == 'mid' for b in f.boxes)]
        self.assertEqual(mids, [3, 5, 6])
        self.assertIn('비교 3번', animation.frames[-1].caption)

    # 검증 목적: 없는 값이면 lo > hi가 되어 실패를 보여 주는 마지막 단계가 있는지 확인
    def test_binary_search_missing_target(self):
        animation = run({'kind': 'dsa_search', 'algorithm': 'binary', 'values': [2, 4, 6, 8], 'target': 5})
        self.assertIn('없', animation.frames[-1].caption)

    # 검증 목적: 선형 탐색은 앞에서부터 한 칸씩 비교하고, 첫 칸에서 찾으면 최선, 못 찾으면 최악으로 표시하는지 확인
    def test_linear_search_best_and_worst(self):
        best = run({'kind': 'dsa_search', 'algorithm': 'linear', 'values': [5, 1, 2], 'target': 5})
        self.assertEqual(len(best.frames), 2)
        self.assertIn('최선', best.frames[-1].caption)
        worst = run({'kind': 'dsa_search', 'algorithm': 'linear', 'values': [5, 1, 2], 'target': 9})
        self.assertIn('비교 3번', worst.frames[-1].caption)
        self.assertIn('최악', worst.frames[-1].caption)

    # 검증 목적: 정렬되지 않은 배열의 이진 탐색 요청은 전제 위반으로 거부되는지 확인
    def test_binary_search_requires_sorted_values(self):
        with self.assertRaises(ValidationError):
            run({'kind': 'dsa_search', 'algorithm': 'binary', 'values': [3, 1, 2], 'target': 1})


class SelectionSortTests(unittest.TestCase):
    # 검증 목적: 회전마다 남은 부분의 최솟값을 앞자리와 교환한 결과가 정렬과 같은지 확인
    def test_selection_sort_result(self):
        animation = run({'kind': 'selection_sort', 'values': [5, 3, 4, 1, 2]})
        self.assertEqual(labels(animation.frames[1]), ['1', '3', '4', '5', '2'])
        self.assertEqual(labels(animation.frames[-1]), ['1', '2', '3', '4', '5'])

    # 검증 목적: 같은 값에 a·b 꼬리표를 붙이고, 선택 정렬이 그 순서를 뒤집으면 불안정이라고 알려 주는지 확인
    def test_duplicates_show_instability(self):
        animation = run({'kind': 'selection_sort', 'values': [2, 3, 2, 1]})
        self.assertEqual(labels(animation.frames[0]), ['2a', '3', '2b', '1'])
        self.assertEqual(labels(animation.frames[-1]), ['1', '2b', '2a', '3'])
        self.assertIn('불안정', animation.frames[-1].caption)


class LogStepsTests(unittest.TestCase):
    # 검증 목적: n을 1이 될 때까지 절반으로 나눈 횟수가 log₂n과 같은지 확인
    def test_halving_counts_log2(self):
        animation = run({'kind': 'log_steps', 'mode': 'halve', 'n': 16})
        self.assertEqual(len(animation.frames), 5)
        self.assertEqual(sum(b.shape == 'cell' and b.tone != 'removed' for b in animation.frames[-1].boxes), 1)
        self.assertIn('log₂16 = 4', animation.frames[-1].caption)

    # 검증 목적: 1에서 시작해 두 배씩 늘리면 k번 만에 2^k개가 되는지 확인(지수 관점)
    def test_doubling_reaches_n(self):
        animation = run({'kind': 'log_steps', 'mode': 'double', 'n': 8})
        self.assertEqual([sum(b.shape == 'cell' for b in f.boxes) for f in animation.frames], [1, 2, 4, 8])

    # 검증 목적: 2의 거듭제곱이 아닌 n은 거부되는지 확인
    def test_rejects_non_power_of_two(self):
        with self.assertRaises(ValidationError):
            run({'kind': 'log_steps', 'mode': 'halve', 'n': 12})


class GrowthTests(unittest.TestCase):
    # 검증 목적: 표의 각 칸 값이 함수식 계산값과 같고 마지막 단계가 큰 n에서의 순서를 알려 주는지 확인
    def test_growth_table_values(self):
        animation = run({'kind': 'growth', 'functions': ['log n', 'n', 'n²'], 'sizes': [2, 4, 16]})
        full = animation.frames[-1]
        self.assertEqual(labels(full, 'cell'), ['1', '2', '4', '2', '4', '16', '4', '16', '256'])
        self.assertIn('log n < n < n²', full.caption)

    # 검증 목적: 표 칸 상한(세 자리)을 넘는 값과 log가 정수가 아닌 n은 거부되는지 확인
    def test_rejects_large_values_and_non_power_log(self):
        with self.assertRaises(ValidationError):
            run({'kind': 'growth', 'functions': ['n', 'n²'], 'sizes': [10, 40]})
        with self.assertRaises(ValidationError):
            run({'kind': 'growth', 'functions': ['log n', 'n'], 'sizes': [3, 4]})


class LoopCountTests(unittest.TestCase):
    # 검증 목적: 중첩 반복은 n×n번, 삼각 반복은 n(n+1)/2번 칸을 채우는지 확인
    def test_nested_and_triangle_counts(self):
        nested = run({'kind': 'loop_count', 'pattern': 'nested', 'n': 4})
        self.assertEqual(sum(b.shape == 'cell' for b in nested.frames[-1].boxes), 16)
        self.assertIn('16번', {b.label for b in nested.frames[-1].boxes})
        triangle = run({'kind': 'loop_count', 'pattern': 'triangle', 'n': 5})
        self.assertEqual(sum(b.shape == 'cell' for b in triangle.frames[-1].boxes), 15)
        self.assertEqual(len(triangle.frames), 6)


class RecursionTreeTests(unittest.TestCase):
    # 검증 목적: T(n)=2T(n/2)+n의 재귀 트리가 단계마다 합 n, 단계 log₂n+1개로 총 n(log₂n+1)이 되는지 확인
    def test_merge_sort_recurrence_total(self):
        animation = run({'kind': 'recursion_tree', 'branch': 2, 'work': 'n', 'n': 8})
        last = animation.frames[-1]
        self.assertEqual(sum(b.shape == 'node' for b in last.boxes), 15)
        self.assertIn('32', last.caption)
        self.assertIn('O(n log n)', last.caption)

    # 검증 목적: 가지 하나·일 1이면 단계 수만큼(log₂n+1) 일하므로 O(log n)인지 확인
    def test_single_branch_is_logarithmic(self):
        animation = run({'kind': 'recursion_tree', 'branch': 1, 'work': '1', 'n': 8})
        self.assertIn('O(log n)', animation.frames[-1].caption)
        self.assertIn('4', animation.frames[-1].caption)


class RecursionTests(unittest.TestCase):
    # 검증 목적: sum(n) = n + sum(n-1)을 실제로 계산해 마지막 반환값이 n(n+1)/2인지 확인
    def test_sum_returns_triangular_number(self):
        animation = run({'kind': 'dsa_recursion', 'function': 'sum', 'n': 4})
        self.assertEqual(len(animation.frames), 2 * 4 + 3)
        self.assertIn('10', animation.frames[-1].caption)

    # 검증 목적: 기저 사례가 없으면 호출이 쌓이기만 하다가 그림의 스택 한도를 넘는 단계에서 멈추는지 확인
    def test_no_base_case_overflows(self):
        animation = run({'kind': 'dsa_recursion', 'function': 'no_base', 'n': 3})
        self.assertIn('오버플로', animation.frames[-1].caption)
        self.assertTrue(any(b.tone == 'removed' for b in animation.frames[-1].boxes))
        self.assertTrue(all('반환' not in f.caption for f in animation.frames))


class FibDpTests(unittest.TestCase):
    # 검증 목적: 단순 재귀 fib(4)는 9번 호출하고 같은 인자를 다시 계산하는 횟수가 4번인지 확인
    def test_naive_calls(self):
        animation = run({'kind': 'fib_dp', 'mode': 'naive', 'n': 4})
        self.assertEqual(sum(b.shape == 'node' for b in animation.frames[-1].boxes), 9)
        self.assertIn('9번', animation.frames[-1].caption)

    # 검증 목적: 메모이제이션 fib(5)는 9번 호출하고 memo 칸에 f2..f5 값이 채워지는지 확인
    def test_memo_fills_table(self):
        animation = run({'kind': 'fib_dp', 'mode': 'memo', 'n': 5})
        last = animation.frames[-1]
        self.assertEqual(sum(b.shape == 'node' for b in last.boxes), 9)
        self.assertEqual(labels(last, 'cell'), ['1', '2', '3', '5'])

    # 검증 목적: 표 채우기는 dp[i] = dp[i-1] + dp[i-2]를 왼쪽부터 계산한 피보나치 수열인지 확인
    def test_table_values(self):
        animation = run({'kind': 'fib_dp', 'mode': 'table', 'n': 7})
        self.assertEqual(labels(animation.frames[-1], 'cell'), ['0', '1', '1', '2', '3', '5', '8', '13'])


class TreeTests(unittest.TestCase):
    EDGES = [['A', 'B'], ['A', 'C'], ['B', 'D'], ['B', 'E'], ['C', 'F']]

    # 검증 목적: 루트·리프·깊이·높이·서브트리가 트리 정의대로 계산되는지 확인
    def test_tree_terms(self):
        ops = [{'op': 'root'}, {'op': 'leaves'}, {'op': 'depth', 'level': 2}, {'op': 'height'}, {'op': 'subtree', 'node': 'B'}]
        animation = run({'kind': 'dsa_tree', 'edges': self.EDGES, 'ops': ops})
        self.assertEqual(toned(animation.frames[1], 'new'), {'A'})
        self.assertEqual(toned(animation.frames[2], 'new'), {'D', 'E', 'F'})
        self.assertEqual(toned(animation.frames[3], 'new'), {'D', 'E', 'F'})
        self.assertIn('높이 2', animation.frames[4].caption)
        self.assertEqual(toned(animation.frames[5], 'new'), {'B', 'D', 'E'})

    # 검증 목적: 포화 이진 트리면 높이 단계에서 최대 노드 수 2^(h+1)-1을 함께 알려 주는지 확인
    def test_perfect_binary_tree_count(self):
        edges = [['A', 'B'], ['A', 'C'], ['B', 'D'], ['B', 'E'], ['C', 'F'], ['C', 'G']]
        animation = run({'kind': 'dsa_tree', 'edges': edges, 'ops': [{'op': 'height'}]})
        self.assertIn('2^3-1 = 7', animation.frames[-1].caption)

    # 검증 목적: 부모가 둘인 노드, 사이클, 없는 노드를 묻는 연산은 거부되는지 확인
    def test_rejects_non_tree(self):
        with self.assertRaises(ValidationError):
            run({'kind': 'dsa_tree', 'edges': [['A', 'B'], ['C', 'B']], 'ops': [{'op': 'root'}]})
        with self.assertRaises(ValidationError):
            run({'kind': 'dsa_tree', 'edges': [['A', 'B'], ['B', 'A']], 'ops': [{'op': 'root'}]})
        with self.assertRaises(ValueError):
            run({'kind': 'dsa_tree', 'edges': self.EDGES, 'ops': [{'op': 'subtree', 'node': 'Z'}]})


class GraphPartsTests(unittest.TestCase):
    # 검증 목적: 연결 요소를 하나씩 보여 주고 요소 사이에는 경로가 없다고 알려 주는지 확인
    def test_components_and_path(self):
        request = {'kind': 'graph_parts', 'nodes': ['A', 'B', 'C', 'D', 'E'], 'edges': [['A', 'B'], ['B', 'C'], ['D', 'E']],
                   'directed': False, 'ops': [{'op': 'components'}, {'op': 'path', 'source': 'A', 'target': 'C'},
                                              {'op': 'path', 'source': 'A', 'target': 'E'}]}
        animation = run(request)
        self.assertEqual(toned(animation.frames[1], 'new'), {'A', 'B', 'C'})
        self.assertEqual(toned(animation.frames[2], 'new'), {'D', 'E'})
        self.assertIn('2개', animation.frames[2].caption)
        self.assertIn('A → B → C', animation.frames[3].caption)
        self.assertIn('없', animation.frames[4].caption)

    # 검증 목적: 트리에 간선 하나를 더하면 사이클이 생기는지(트리 = 사이클 없는 연결 그래프) 확인
    def test_adding_edge_to_tree_creates_cycle(self):
        request = {'kind': 'graph_parts', 'nodes': ['A', 'B', 'C', 'D'], 'edges': [['A', 'B'], ['B', 'C'], ['B', 'D']],
                   'directed': False, 'ops': [{'op': 'cycle'}, {'op': 'add', 'edge': ['C', 'D']}, {'op': 'cycle'}]}
        animation = run(request)
        self.assertIn('트리', animation.frames[1].caption)
        self.assertEqual(toned(animation.frames[3], 'new'), {'B', 'C', 'D'})

    # 검증 목적: 방향 그래프는 간선 방향으로만 경로를 찾는지 확인
    def test_directed_path_follows_direction(self):
        request = {'kind': 'graph_parts', 'nodes': ['A', 'B'], 'edges': [['A', 'B']], 'directed': True,
                   'ops': [{'op': 'path', 'source': 'B', 'target': 'A'}]}
        self.assertIn('없', run(request).frames[-1].caption)

    # 검증 목적: 이미 있는 간선 추가와 방향 그래프의 연결 요소 요청은 거부되는지 확인
    def test_rejects_duplicate_edge_and_directed_components(self):
        with self.assertRaises(ValueError):
            run({'kind': 'graph_parts', 'nodes': ['A', 'B'], 'edges': [['A', 'B']], 'directed': False,
                 'ops': [{'op': 'add', 'edge': ['B', 'A']}]})
        with self.assertRaises(ValueError):
            run({'kind': 'graph_parts', 'nodes': ['A', 'B'], 'edges': [['A', 'B']], 'directed': True,
                 'ops': [{'op': 'components'}]})


## @brief 프레임 수열을 비교용 값으로 바꿈(상자 위치·라벨·tone과 화살표)
def frame_signature(animation):
    return [(sorted((b.x, b.y, b.label, b.shape, b.tone) for b in f.boxes),
             sorted((a.source, a.target) for a in f.arrows)) for f in animation.frames]


class SceneFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenes = json.loads(SCENES_PATH.read_text(encoding='utf-8'))

    # 검증 목적: 화면에 남는 개념 전부에 장면이 정확히 3개씩 있고, 가린 개념의 장면은 없는지 확인
    def test_every_visible_concept_has_three_scenes(self):
        self.assertEqual(set(self.scenes), VISIBLE_CONCEPTS)
        for concept, scenes in self.scenes.items():
            self.assertEqual(len(scenes), 3, concept)

    # 검증 목적: 모든 장면이 스키마 검증·시뮬레이션을 통과하고 프레임 수·라벨 길이 상한을 지키는지 확인
    def test_scenes_run_within_limits(self):
        for concept, scenes in self.scenes.items():
            for index, scene in enumerate(scenes):
                with self.subTest(concept=concept, index=index):
                    animation = run(scene)
                    self.assertLessEqual(len(animation.frames), SCENE_FRAME_LIMIT)
                    for frame in animation.frames:
                        for box in frame.boxes:
                            limit = NAME_LABEL_LIMIT if box.shape == 'label' else BOX_LABEL_LIMIT
                            self.assertLessEqual(len(box.label), limit, box.label)

    # 검증 목적: 한 개념의 장면 3개가 요청도 프레임 수열도 서로 달라, 숫자만 바꾼 같은 장면이 아님을 확인
    # - 같은 kind끼리는 연산 순서·관점이 달라야 하므로 kind 외 입력 필드(연산·알고리즘·모드)가 하나 이상 달라야 함
    def test_three_scenes_are_distinct(self):
        for concept, scenes in self.scenes.items():
            with self.subTest(concept=concept):
                requests = [json.dumps(s, sort_keys=True) for s in scenes]
                self.assertEqual(len(set(requests)), 3)
                signatures = [json.dumps(frame_signature(run(s))) for s in scenes]
                self.assertEqual(len(set(signatures)), 3)


if __name__ == '__main__':
    unittest.main()
