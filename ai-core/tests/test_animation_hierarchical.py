## @file
# @brief 해시 테이블·BST·힙 애니메이션이 dict·정렬·heapq 참조 결과와 같은 상태를 그리는지, 실행할 수 없는 연산과 분량 초과를 거부하는지 검증
import heapq
import unittest

from serving.animation import simulate
from serving.animation.model import AnimationRequestModel


## @brief dict 요청을 스키마로 검증한 뒤 시뮬레이터를 실행
def run(data: dict):
    return simulate(AnimationRequestModel.model_validate(data).root)


## @brief 프레임의 화살표를 따라 트리를 복원해 중위 순회 라벨과 (부모, 왼쪽 자식) 관계를 반환
# - 왼쪽 자식은 부모보다 x가 작은 자식. 레이아웃이 아니라 연결 구조로 순회해야 화살표 오류도 잡힘
def tree_of(frame):
    nodes = {b.id: b for b in frame.boxes if b.shape == 'node' and b.tone != 'removed'}
    children = {i: [a.target for a in frame.arrows if a.source == i and a.target in nodes] for i in nodes}
    root = next(i for i in nodes if not any(i in c for c in children.values()))
    left = {}

    def walk(i):
        kids = sorted(children[i], key=lambda c: nodes[c].x)
        lower = [c for c in kids if nodes[c].x < nodes[i].x]
        higher = [c for c in kids if nodes[c].x > nodes[i].x]
        if lower:
            left[nodes[i].label] = nodes[lower[0]].label
        return sum((walk(c) for c in lower), []) + [int(nodes[i].label)] + sum((walk(c) for c in higher), [])
    return walk(root), left


class HashTableTests(unittest.TestCase):
    # 검증 목적: k mod buckets 위치와 체이닝 순서가 파이썬으로 계산한 버킷별 리스트와 같고, 캡션에 계산식이 보이는지 확인
    def test_hash_chaining_matches_dict_reference(self):
        keys = [12, 7, 17, 3]
        animation = run({'kind': 'hash_table', 'buckets': 5, 'ops': [{'op': 'insert', 'key': k} for k in keys]})
        expected = {}
        for key in keys:
            expected.setdefault(key % 5, []).append(key)
        chains = {}
        for box in sorted((b for b in animation.frames[-1].boxes if b.shape == 'node'), key=lambda b: b.y):
            chains.setdefault(box.x, []).append(int(box.label))
        self.assertEqual(chains, expected)
        self.assertTrue(any('17 mod 5 = 2' in f.caption for f in animation.frames))


class BSTTests(unittest.TestCase):
    # 검증 목적: 자식이 둘인 노드 삭제 후에도 중위 순회가 정렬 순서이고, 중위 후속자가 그 자리를 대신하는지 확인
    def test_bst_inorder_sorted(self):
        ops = [{'op': 'insert', 'value': v} for v in (8, 3, 10, 1, 6)] + [{'op': 'delete', 'value': 3}]
        animation = run({'kind': 'bst', 'ops': ops})
        inorder, left = tree_of(animation.frames[-1])
        self.assertEqual(inorder, [1, 6, 8, 10])
        self.assertEqual(left['8'], '6')

    # 검증 목적: 이미 있는 값 삽입을 그림 없이 거부하는지 확인
    def test_bst_duplicate_rejected(self):
        with self.assertRaises(ValueError) as caught:
            run({'kind': 'bst', 'ops': [{'op': 'insert', 'value': 5}, {'op': 'insert', 'value': 5}]})
        self.assertIn('이미', str(caught.exception))


class HeapTests(unittest.TestCase):
    # 검증 목적: 최소 힙 insert·extract 뒤 배열이 heapq로 같은 연산을 한 결과와 같은지 확인
    def test_min_heap_matches_heapq(self):
        values = [5, 3, 8, 1]
        ops = [{'op': 'insert', 'value': v} for v in values] + [{'op': 'extract'}]
        animation = run({'kind': 'heap', 'order': 'min', 'ops': ops})
        reference = []
        for value in values:
            heapq.heappush(reference, value)
        heapq.heappop(reference)
        cells = sorted((b for b in animation.frames[-1].boxes if b.shape == 'cell'), key=lambda b: b.x)
        self.assertEqual([int(b.label) for b in cells], reference)

    # 검증 목적: 교환 단계까지 합한 프레임 수가 상한을 넘으면 잘린 그림 대신 분량 초과 오류를 내는지 확인
    def test_heap_swap_frames_within_limit(self):
        ops = [{'op': 'insert', 'value': v} for v in range(10, 0, -1)]
        with self.assertRaises(ValueError) as caught:
            run({'kind': 'heap', 'order': 'min', 'ops': ops})
        self.assertIn('분량', str(caught.exception))

    # 검증 목적: 빈 힙 extract를 한국어 오류로 거부하는지 확인
    def test_extract_empty_rejected(self):
        with self.assertRaises(ValueError) as caught:
            run({'kind': 'heap', 'order': 'max', 'ops': [{'op': 'extract'}]})
        self.assertIn('비어', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
