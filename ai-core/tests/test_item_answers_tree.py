# 파일 역할: 묶음 2(트리·이진 트리·순회·BST·균형 BST) 문항의 구성을 검사하고, 단답 정답을 문항 속 트리를 실제로 만들어 독립 계산해 대조
import math
import re
import unittest
from collections import Counter, deque

from serving.concept_map import load_concept_map
from serving.item_bank import load_item_bank, normalize_short_answer

SCOPE = {'tree', 'binary-tree', 'tree-traversal', 'bst', 'balanced-bst'}
# 오답에만 몰리면 지식 없이 오답을 지울 단서가 되는 극단 표현(CS-300 선택형이 단서로 풀린 원인)
EXTREME_WORDS = ('항상', '반드시', '절대', '전혀', '무조건')


# 역할: 문항 속 "X(L,R)" 표기(-는 자식 없음)를 {노드: (왼쪽, 오른쪽)}과 루트로 바꿈; 적히지 않은 노드는 리프
def binary(text):
    pick = lambda v: None if v == '-' else int(v)
    tree = {int(n): (pick(l), pick(r)) for n, l, r in re.findall(r'(\d+)\((\d+|-),(\d+|-)\)', text)}
    children = {c for pair in tree.values() for c in pair if c is not None}
    return tree, (set(tree) - children).pop()


# 역할: 문항 속 "부모: 자식, 자식 / …" 표기를 {부모: [자식…]}로 바꿈; 루트는 누구의 자식도 아닌 노드
def general(text):
    tree = {int(p): [int(c) for c in cs.split(', ')] for p, cs in re.findall(r'(\d+): (\d+(?:, \d+)*)', text)}
    children = {c for cs in tree.values() for c in cs}
    return tree, (set(tree) - children).pop()


def kids(tree, node):
    return tree.get(node, (None, None))


def preorder(tree, node):
    if node is None:
        return []
    left, right = kids(tree, node)
    return [node] + preorder(tree, left) + preorder(tree, right)


def postorder(tree, node):
    if node is None:
        return []
    left, right = kids(tree, node)
    return postorder(tree, left) + postorder(tree, right) + [node]


# 역할: 간선 수로 센 높이; 빈 서브트리는 −1이라 리프의 높이가 0이 됨
def height(tree, node):
    return -1 if node is None else 1 + max(height(tree, c) for c in kids(tree, node))


# 역할: 루트를 1, 왼쪽 자식을 2i, 오른쪽 자식을 2i+1로 번호 매겨 1..n이 빈틈없이 채워졌는지로 완전·포화 여부 판단
def complete_and_full(tree, root):
    numbers, stack = set(), [(root, 1)]
    while stack:
        node, number = stack.pop()
        if node is not None:
            numbers.add(number)
            left, right = kids(tree, node)
            stack += [(left, 2 * number), (right, 2 * number + 1)]
    complete = numbers == set(range(1, len(numbers) + 1))
    return complete, complete and len(numbers) == 2 ** (height(tree, root) + 1) - 1


# 역할: 일반 트리의 깊이(루트에서 간선 수)를 너비 우선으로 셈
def depths(tree, root):
    found, queue = {root: 0}, deque([root])
    while queue:
        node = queue.popleft()
        for child in tree.get(node, []):
            found[child] = found[node] + 1
            queue.append(child)
    return found


def subtree_size(tree, node):
    return 1 + sum(subtree_size(tree, c) for c in tree.get(node, []))


# 역할: BST 노드를 [키, 왼쪽, 오른쪽] 리스트로 두고 삽입·탐색 경로·삭제(자식 둘이면 오른쪽 서브트리 최솟값으로 대체)를 실제로 수행
def bst_insert(node, key):
    if node is None:
        return [key, None, None]
    side = 1 if key < node[0] else 2
    node[side] = bst_insert(node[side], key)
    return node


def bst_build(keys):
    root = None
    for key in keys:
        root = bst_insert(root, key)
    return root


def search_path(node, key):
    path = []
    while node is not None:
        path.append(node[0])
        if key == node[0]:
            break
        node = node[1] if key < node[0] else node[2]
    return path


def bst_delete(node, key):
    if node is None:
        return None
    if key < node[0]:
        node[1] = bst_delete(node[1], key)
    elif key > node[0]:
        node[2] = bst_delete(node[2], key)
    elif node[1] is None or node[2] is None:
        return node[1] or node[2]
    else:
        successor = node[2]
        while successor[1] is not None:
            successor = successor[1]
        node[0], node[2] = successor[0], bst_delete(node[2], successor[0])
    return node


def bst_pre(node):
    return [] if node is None else [node[0]] + bst_pre(node[1]) + bst_pre(node[2])


def bst_height(node):
    return -1 if node is None else 1 + max(bst_height(node[1]), bst_height(node[2]))


# 역할: AVL 삽입; 삽입 뒤 높이 차가 2가 된 노드를 LL·RR·LR·RL 경우에 맞는 회전으로 되돌림
def rotate_right(node):
    top = node[1]
    node[1], top[2] = top[2], node
    return top


def rotate_left(node):
    top = node[2]
    node[2], top[1] = top[1], node
    return top


def avl_insert(node, key):
    node = bst_insert(node, key) if node is None else node
    if key != node[0]:
        side = 1 if key < node[0] else 2
        node[side] = avl_insert(node[side], key)
    balance = bst_height(node[1]) - bst_height(node[2])
    if balance > 1:
        if key > node[1][0]:
            node[1] = rotate_left(node[1])
        return rotate_right(node)
    if balance < -1:
        if key < node[2][0]:
            node[2] = rotate_right(node[2])
        return rotate_left(node)
    return node


# 역할: 정렬된 키를 가운데 값부터 넣어 높이가 가장 낮은 BST를 만드는 삽입 순서
def median_first(keys):
    if not keys:
        return []
    mid = len(keys) // 2
    return [keys[mid]] + median_first(keys[:mid]) + median_first(keys[mid + 1:])


def join(values):
    return ','.join(str(v) for v in values)


# 역할: 문항 ID → 정답 키와 별도로 계산한 기대 답
def expected_answers(items):
    q = {item.id: item.question for item in items}
    leaf_tree, _ = general(q['tree-d1'])
    every = set(leaf_tree) | {c for cs in leaf_tree.values() for c in cs}
    depth_tree, depth_root = general(q['tree-c1'])
    found = depths(depth_tree, depth_root)
    size_tree, size_root = general(q['tree-c2'])
    perfect = lambda h: 0 if h < 0 else 1 + 2 * perfect(h - 1)  # 높이 h 포화 이진 트리의 노드 수를 재귀로 셈
    level = [(i + 1).bit_length() - 1 for i in range(12)]  # 완전 이진 트리 i번째(0부터) 노드의 깊이
    min_height = 0
    while perfect(min_height) < 100:
        min_height += 1
    queue_tree, queue_root = binary(q['tree-traversal-c2'])
    queue, popped = deque([queue_root]), None
    while popped != 2:  # 노드 2를 꺼내 자식을 넣은 직후까지 레벨 순회를 실제로 진행
        popped = queue.popleft()
        queue.extend(c for c in kids(queue_tree, popped) if c is not None)
    deleted = bst_delete(bst_build([40, 20, 60, 10, 30, 50, 70, 25]), 20)
    skewed, balanced = bst_build(range(1, 16)), bst_build(median_first(list(range(1, 16))))
    avl = None
    for key in (50, 30, 70, 20, 40, 10):
        avl = avl_insert(avl, key)
    factor_tree, _ = binary(q['balanced-bst-c2'])
    factor = lambda n: height(factor_tree, kids(factor_tree, n)[0]) - height(factor_tree, kids(factor_tree, n)[1])
    return {
        'tree-d1': join(sorted(every - set(leaf_tree))),
        'tree-c1': join([found[50], max(found.values())]),
        'tree-c2': join(subtree_size(size_tree, c) for c in size_tree[size_root]),
        'binary-tree-d1': perfect(3),
        'binary-tree-c1': level.count(max(level)),
        'binary-tree-c2': min_height,
        'tree-traversal-d1': join(preorder(*binary(q['tree-traversal-d1']))),
        'tree-traversal-c1': join(postorder(*binary(q['tree-traversal-c1']))),
        'tree-traversal-c2': join(queue),
        'bst-d1': join(search_path(bst_build([50, 30, 70, 20, 40, 60, 80, 35]), 35)),
        'bst-c1': join(bst_pre(deleted)),
        'bst-c2': join([bst_height(bst_build(range(1, 8))), bst_height(bst_build([4, 2, 6, 1, 3, 5, 7]))]),
        'balanced-bst-d1': join([max(len(search_path(t, k)) for k in range(1, 16)) for t in (skewed, balanced)]),
        'balanced-bst-c1': join(bst_pre(avl)),
        'balanced-bst-c2': join([factor(40), factor(20)]),
    }


class TreeItemTests(unittest.TestCase):
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

    # 검증 목적: 보기가 트리 표기인 선택형은 보기마다 트리를 만들어 조건을 만족하는 보기가 정답 하나뿐인지 확인
    def test_complete_not_full_choice_is_unique(self):
        item = next(i for i in self.items if i.id == 'binary-tree-d2')
        matches = [label for label, text in item.options.items() if complete_and_full(*binary(text)) == (True, False)]
        self.assertEqual(matches, [item.answer])

    # 검증 목적: 모든 단답 정답이 문항 내용으로부터 독립 계산한 값과 일치하는지 확인
    def test_short_answers_match_independent_computation(self):
        short = {i.id: i for i in self.items if i.format == 'short_exact'}
        expected = expected_answers(self.items)
        self.assertEqual(set(expected), set(short))
        for item_id, value in expected.items():
            self.assertEqual(normalize_short_answer(str(value)), short[item_id].answer, item_id)


if __name__ == '__main__':
    unittest.main()
