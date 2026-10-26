# 파일 역할: 선형 구조 문항 은행의 구성·스키마 거부 동작과, 단답 정답을 문항 내용으로부터 독립 계산해 대조
import json
import math
import re
import unittest
from collections import Counter, deque
from difflib import SequenceMatcher
from pathlib import Path

from pydantic import ValidationError

from serving.concept_map import load_concept_map
from serving.item_bank import Item, load_item_bank, normalize_short_answer, validate_against_map

# PR3a 범위: 선형 구조와, 그 개념에서 막혔을 때 진단 깊이 3 안에서 만나는 기초 개념
LINEAR_SCOPE = {'memory-address', 'reference', 'logarithm', 'big-o', 'complexity-analysis', 'array',
                'dynamic-array', 'linked-list', 'adt', 'stack', 'queue'}
CS300 = Path(__file__).resolve().parents[2] / 'codex' / 'model-evaluation' / 'cs-300-v1' / 'questions.jsonl'


# 역할: 문항 본문의 연산 열("P1 P5 X" 또는 "E3 D")을 그대로 실행해 꺼낸 값과 남은 원소 반환
# - 연산 열은 콜론 뒤부터 기호 설명 괄호 앞까지만 읽어, 안내 문장 속 단어를 연산으로 오인하지 않게 함
def run_container(question, lifo):
    box, removed = [], []
    for token in re.search(r':\s*([PEXD0-9 ]+?)\s*\(', question).group(1).split():
        if token[0] in 'PE':
            box.append(int(token[1:]))
        else:
            removed.append(box.pop() if lifo else box.pop(0))
    return removed, box


# 역할: 문항 본문의 코드 조각을 실제로 실행해 count 값 반환; 손 계산 실수를 막기 위함
def run_snippet(question, n):
    scope = {'n': n, 'count': 0}
    exec(question.split('\n', 1)[1], {}, scope)
    return scope['count']


# 역할: 빈 동적 배열에 k개를 추가하며 재할당 횟수와 복사한 원소 수 합계를 셈
def grow(k, policy, capacity=1):
    size, reallocations, copies = 0, 0, 0
    for _ in range(k):
        if size == capacity:
            reallocations, copies, capacity = reallocations + 1, copies + size, policy(capacity)
        size += 1
    return reallocations, copies


# 역할: {칸: (값, next)} 메모리 표에서 head부터 next를 따라가며 방문한 값 목록 반환
def follow(table, head, steps=None):
    values, cell = [], head
    while cell is not None and (steps is None or len(values) <= steps):
        value, cell = table[cell]
        values.append(value)
    return values


# 역할: 단일 연결 리스트 노드를 참조로 이어 붙여 삽입·삭제를 실제로 수행
class Node:
    def __init__(self, value, next=None):
        self.value, self.next = value, next


def to_list(head):
    values = []
    while head:
        values.append(head.value)
        head = head.next
    return values


def build_list(values):
    head = None
    for value in reversed(values):
        head = Node(value, head)
    return head


def join(values):
    return ','.join(str(v) for v in values)


# 역할: 연결 리스트에서 head부터 next를 따라가 index 노드에 닿을 때까지의 이동 횟수
def steps_to(head, index):
    steps = 0
    while steps < index:
        head, steps = head.next, steps + 1
    return steps


# 역할: 순차 탐색을 실제로 돌려 비교 횟수를 셈
def comparisons(values, target):
    count = 0
    for value in values:
        count += 1
        if value == target:
            break
    return count


# 역할: 문항 ID → 정답 키와 별도로 계산한 기대 답
def expected_answers(items):
    q = {item.id: item.question for item in items}
    ll = build_list([7, 3, 9, 5])
    ll.next.next = Node(4, ll.next.next)
    deleted = build_list([2, 6, 1, 8])
    deleted.next.next = deleted.next.next.next
    obj = type('Obj', (), {})()
    obj.v = 3
    a, b = 3, 3
    b, p, q_ref = 10, obj, obj
    q_ref.v = 10
    array_insert, array_delete = [3, 8, 1, 9, 4], list(range(10))
    k = 1000  # n = 2^k에서 log₂ 값으로 증가 속도 비교; 상수 계수가 무시되는 큰 n
    growth = {1: 2 * k, 2: k + math.log2(1000), 3: k + math.log2(5) + math.log2(k), 4: 2 ** k}
    depth = peak = 0
    for ch in re.search(r'[()]{8}', q['stack-c2']).group():
        depth += 1 if ch == '(' else -1
        peak = max(peak, depth)
    rear = 3
    for _ in range(3):
        rear = (rear + 1) % 5
    halvings, n = 0, 64
    while n > 1:
        n, halvings = n // 2, halvings + 1
    return {
        'memory-address-d1': 200 + 6 * 4,
        'memory-address-c1': 1048 - 3 * 8,
        'memory-address-c2': (518 - 500) // 2,
        'reference-d1': follow({100: (5, 300), 200: (9, None), 300: (2, 200)}, 100, 2)[-1],
        'reference-c1': join([a, p.v]),
        'reference-c2': join(follow({40: (1, 70), 70: (8, 10), 10: (6, None), 90: (4, 40)}, 90)),
        'logarithm-d1': halvings,
        'logarithm-c1': int(math.log2(1024)),
        'logarithm-c2': 256 ** 2 // int(math.log2(256)),
        'big-o-c2': join(sorted(growth, key=growth.get)),
        'complexity-analysis-d1': run_snippet(q['complexity-analysis-d1'], 6),
        'complexity-analysis-c1': run_snippet(q['complexity-analysis-c1'], 10),
        'complexity-analysis-c2': join([comparisons(range(8), 0), comparisons(range(8), -1)]),
        'array-d2': len(array_insert[1:]),
        'array-c1': len(array_delete[4:]),
        'array-c2': join([2, 4, 6, 8][:2] + [5] + [2, 4, 6, 8][2:]),  # list.insert와 같은 결과를 슬라이스로 직접 구성
        'dynamic-array-d1': grow(5, lambda c: c * 2, capacity=4)[1],
        'dynamic-array-d2': grow(8, lambda c: c * 2)[0],
        'dynamic-array-c1': grow(16, lambda c: c * 2)[1],
        'dynamic-array-c2': grow(5, lambda c: c + 1)[1],
        'linked-list-d1': join(to_list(ll)),
        'linked-list-d2': steps_to(build_list(range(6)), 4),
        'linked-list-c1': join(to_list(deleted)),
        'adt-c2': join([1, steps_to(build_list(range(100)), 50)]),
        'stack-d1': join(run_container(q['stack-d1'], lifo=True)[0]),
        'stack-c1': join(run_container(q['stack-c1'], lifo=True)[1]),
        'stack-c2': peak,
        'queue-d1': join(run_container(q['queue-d1'], lifo=False)[0]),
        'queue-c1': join(run_container(q['queue-c1'], lifo=False)[1]),
        'queue-c2': rear,
    }


class LinearItemBankTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 다른 묶음 문항은 각 묶음의 test_item_answers_<묶음>.py가 검사하므로 선형 범위만 고름
        cls.items = [i for i in load_item_bank() if i.concept in LINEAR_SCOPE]

    # 검증 목적: PR3a 범위의 개념이 모두 담겼는지 확인
    def test_covers_exactly_linear_scope(self):
        self.assertEqual({i.concept for i in self.items}, LINEAR_SCOPE)

    # 검증 목적: 개념마다 진단 2개·확인 2개·서술 1개 이상이 있고, 모든 학습 목표가 자동 채점 문항으로 확인되는지 확인
    def test_each_concept_has_full_set_and_objective_coverage(self):
        concepts = {c.id: c for c in load_concept_map().concepts}
        for concept in LINEAR_SCOPE:
            own = [i for i in self.items if i.concept == concept]
            purposes = Counter(i.purpose for i in own)
            self.assertGreaterEqual(purposes['diagnosis'], 2, concept)
            self.assertGreaterEqual(purposes['check'], 2, concept)
            self.assertGreaterEqual(purposes['explanation'], 1, concept)
            graded = {i.objective for i in own if i.purpose != 'explanation'}
            self.assertEqual(graded, set(range(len(concepts[concept].objectives))), concept)

    # 검증 목적: 선택형 정답 위치가 한쪽으로 몰려 위치만으로 맞히는 일을 막음
    def test_choice_answers_are_balanced(self):
        labels = Counter(i.answer for i in self.items if i.format == 'multiple_choice')
        total = sum(labels.values())
        self.assertEqual(set(labels), set('ABCD'))
        self.assertLessEqual(max(labels.values()), math.ceil(total / 4) + 1)

    # 검증 목적: 모든 단답 정답이 문항 내용으로부터 독립 계산한 값과 일치하는지 확인
    def test_short_answers_match_independent_computation(self):
        short = {i.id: i for i in self.items if i.format == 'short_exact'}
        expected = expected_answers(self.items)
        self.assertEqual(set(expected), set(short))
        for item_id, value in expected.items():
            self.assertEqual(normalize_short_answer(str(value)), short[item_id].answer, item_id)

    # 검증 목적: 평가 전용 CS-300과 문장이 겹치는 문항이 없는지 확인; CS-300은 Git 밖 로컬 자료라 없으면 건너뜀
    # - 선형 범위 필터와 상관없이 문항 은행 전체를 검사
    @unittest.skipUnless(CS300.exists(), 'CS-300 로컬 자료 없음')
    def test_no_overlap_with_cs300(self):
        cs300 = [json.loads(line)['question'] for line in CS300.read_text(encoding='utf-8').splitlines()]
        for item in load_item_bank():
            closest = max(SequenceMatcher(None, item.question, other).ratio() for other in cs300)
            self.assertLess(closest, 0.6, item.id)


class ItemSchemaTests(unittest.TestCase):
    def base(self, **changes):
        item = {'id': 'array-d9', 'concept': 'array', 'purpose': 'diagnosis', 'objective': 0, 'format': 'short_exact',
                'question': '질문', 'answer': '1,2', 'rationale': '근거'}
        item.update(changes)
        return item

    # 검증 목적: 결정적 채점이 불가능한 문항 형태를 거부
    def test_rejects_malformed_items(self):
        cases = [
            self.base(id='stack-d9'),
            self.base(id='array-c9'),
            self.base(answer='1, 2'),
            self.base(format='multiple_choice', options={'A': '1', 'B': '2', 'C': '3'}, answer='A'),
            self.base(format='multiple_choice', options={'A': '1', 'B': '1', 'C': '3', 'D': '4'}, answer='A'),
            self.base(format='multiple_choice', options={'A': '1', 'B': '2', 'C': '3', 'D': '4'}, answer='E'),
            self.base(id='array-e9', purpose='explanation', format='explanation', answer=None, rubric=['하나']),
            self.base(rubric=['가', '나']),
        ]
        for case in cases:
            with self.assertRaises(ValidationError, msg=case):
                Item.model_validate(case)

    # 검증 목적: 개념 지도에 없는 개념·목표를 가리키거나 ID가 겹치는 문항을 거부
    def test_rejects_items_outside_concept_map(self):
        concept_map = load_concept_map()
        for items in ([Item.model_validate(self.base(id='missing-d1', concept='missing'))],
                      [Item.model_validate(self.base(objective=9))],
                      [Item.model_validate(self.base())] * 2):
            with self.assertRaises(ValueError):
                validate_against_map(items, concept_map)


if __name__ == '__main__':
    unittest.main()
