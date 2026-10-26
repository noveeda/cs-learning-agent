# 파일 역할: 묶음 1(재귀·점화식·이진 탐색·해시) 문항의 구성을 검사하고, 단답 정답을 문항 내용으로부터 독립 계산해 대조
import math
import re
import unittest
from collections import Counter

from eval.cs300_overlap import find_overlaps
from serving.concept_map import load_concept_map
from serving.item_bank import load_item_bank, normalize_short_answer

SCOPE = {'recursion', 'recurrence-relation', 'binary-search', 'hash-function', 'hash-table'}
# 오답에만 몰리면 지식 없이 오답을 지울 단서가 되는 극단 표현(CS-300 선택형이 단서로 풀린 원인)
EXTREME_WORDS = ('항상', '반드시', '절대', '전혀', '무조건')


# 역할: 문항 본문 첫 줄 뒤의 코드 조각을 실제로 정의해 이름공간 반환; 손 계산 대신 코드 그대로 실행하기 위함
def define(question, scope=None):
    scope = scope if scope is not None else {}
    exec(question.split('\n', 1)[1], scope)
    return scope


# 역할: 재귀 함수 name을 감싸 호출 횟수를 셈; 함수가 이름공간에서 자기 이름을 찾아 부르므로 재귀 호출도 모두 세어짐
def count_calls(scope, name, *args):
    original, calls = scope[name], [0]

    def counted(*inner):
        calls[0] += 1
        return original(*inner)
    scope[name] = counted
    counted(*args)
    return calls[0]


# 역할: lo=0, hi=n-1, mid=(lo+hi)//2 규칙의 이진 탐색을 실제로 돌려 비교한 인덱스 순서 반환
def probes(values, target):
    lo, hi, seen = 0, len(values) - 1, []
    while lo <= hi:
        mid = (lo + hi) // 2
        seen.append(mid)
        if values[mid] == target:
            break
        if values[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return seen


# 역할: 체이닝 해시 테이블(h(k)=k mod m, 새 키는 버킷 리스트 뒤에 붙임)에 키를 차례로 넣은 버킷 목록 반환
def chain(keys, m):
    buckets = [[] for _ in range(m)]
    for key in keys:
        buckets[key % m].append(key)
    return buckets


def join(values):
    return ','.join(str(v) for v in values)


def array_in(question):
    return [int(v) for v in re.search(r'\[([\d,]+)\]', question).group(1).split(',')]


# 역할: 문항 ID → 정답 키와 별도로 계산한 기대 답
def expected_answers(items):
    q = {item.id: item.question for item in items}
    printed = []
    define(q['recursion-c1'], {'print': printed.append})['g'](3)

    def linear(n):  # 비교 1번 + 나머지 n−1개 재귀, T(1)=1
        return 1 if n == 1 else linear(n - 1) + 1

    def halving(n):  # T(n)=2T(n/2)+n, T(1)=0
        return 0 if n == 1 else 2 * halving(n // 2) + n
    sizes = [32]
    for _ in range(2):  # 재귀 트리를 깊이 2까지 실제로 펼침
        sizes = [size // 2 for size in sizes for _ in range(2)]
    sorted_1000 = list(range(1000))
    worst = max(len(probes(sorted_1000, t / 2)) for t in range(-1, 2000))  # 있는 값과 사이 값(없는 값)을 모두 시도
    letters = {c: i + 1 for i, c in enumerate('abcdefghijklmnopqrstuvwxyz')}
    found_in = chain([9, 13, 2, 5, 21, 6], 4)[21 % 4]
    return {
        'recursion-d1': define(q['recursion-d1'])['f'](4),
        'recursion-c1': join(printed),
        'recursion-c2': count_calls(define(q['recursion-c2']), 'h', 5),
        'recurrence-relation-d2': linear(6),
        'recurrence-relation-c1': halving(16),
        'recurrence-relation-c2': join([len(sizes), sum(sizes)]),
        'binary-search-d1': join(probes(array_in(q['binary-search-d1']), 23)),
        'binary-search-c1': join(probes(array_in(q['binary-search-c1']), 10)),
        'binary-search-c2': worst,
        'hash-function-d1': join(k % 7 for k in (50, 23, 64)),
        'hash-function-c1': join(sum(letters[c] for c in key) % 11 for key in ('cab', 'dog')),
        'hash-function-c2': sum(k % 8 == 3 for k in (3, 11, 19, 6, 27)),
        'hash-table-d1': join(chain([12, 7, 3, 17, 22], 5)[2]),
        'hash-table-c1': found_in.index(21) + 1,
        'hash-table-c2': join([20 / 8, 20 / 16]),
    }


class RecursionHashItemTests(unittest.TestCase):
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


class Cs300OverlapTests(unittest.TestCase):
    # 검증 목적: 공백·문장부호만 다른 질문은 겹침으로 잡고, 내용이 다른 질문은 잡지 않는지 확인
    def test_finds_only_near_duplicates(self):
        items = [{'id': 'stack-d1', 'question': '빈 스택에 1, 2를 넣고 하나를 꺼내면?'},
                 {'id': 'queue-d1', 'question': '원형 큐의 rear는 어떻게 움직이나요?'}]
        cs300 = [{'id': 'dsa-001', 'question': '빈 스택에 1,2를 넣고, 하나를 꺼내면'},
                 {'id': 'os-001', 'question': '교착 상태의 네 가지 조건은?'}]
        self.assertEqual([(item, other) for _, item, other in find_overlaps(items, cs300)], [('stack-d1', 'dsa-001')])


if __name__ == '__main__':
    unittest.main()
