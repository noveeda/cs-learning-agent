# 파일 역할: 묶음 3(힙·우선순위 큐·기초 정렬·병합 정렬·퀵 정렬) 문항의 구성을 검사하고, 단답 정답을 힙 연산·정렬을 실제로 돌려 독립 계산해 대조
import heapq
import math
import unittest
from collections import Counter, deque

from serving.concept_map import load_concept_map
from serving.item_bank import load_item_bank, normalize_short_answer

SCOPE = {'heap', 'priority-queue', 'sorting-basics', 'merge-sort', 'quick-sort'}
# 오답에만 몰리면 지식 없이 오답을 지울 단서가 되는 극단 표현(CS-300 선택형이 단서로 풀린 원인)
EXTREME_WORDS = ('항상', '반드시', '절대', '전혀', '무조건')


# 역할: 완전 이진 트리를 위층부터 왼쪽부터 채우며 {인덱스: [자식 인덱스…]}를 만듦; 부모·자식 공식을 쓰지 않고 배치 규칙만으로 위치를 구하기 위함
def layout(n):
    children, waiting, nxt = {i: [] for i in range(n)}, deque([0]), 1
    while nxt < n:
        parent = waiting.popleft()
        for _ in range(2):
            if nxt < n:
                children[parent].append(nxt)
                waiting.append(nxt)
                nxt += 1
    return children


# 역할: 최소 힙 위로 재정렬; 부모와 비교한 횟수를 반환
def sift_up(heap, i):
    compared = 0
    while i > 0:
        parent = (i - 1) // 2
        compared += 1
        if heap[i] >= heap[parent]:
            break
        heap[i], heap[parent] = heap[parent], heap[i]
        i = parent
    return compared


# 역할: 최소 힙 아래로 재정렬; 두 자식 중 작은 쪽과 바꾸며 내려감
def sift_down(heap, i):
    while 2 * i + 1 < len(heap):
        child = 2 * i + 1
        if child + 1 < len(heap) and heap[child + 1] < heap[child]:
            child += 1
        if heap[child] >= heap[i]:
            break
        heap[i], heap[child] = heap[child], heap[i]
        i = child


# 역할: 선택 정렬을 steps단계만 진행; 같은 값이면 먼저 찾은 원소를 최솟값으로 둠(strict <)
def selection(values, steps=None, key=lambda v: v):
    values = list(values)
    for i in range(len(values) - 1 if steps is None else steps):
        smallest = i
        for j in range(i + 1, len(values)):
            if key(values[j]) < key(values[smallest]):
                smallest = j
        values[i], values[smallest] = values[smallest], values[i]
    return values


# 역할: 삽입 정렬로 인덱스 1..steps의 원소를 차례로 앞쪽 정렬된 부분에 끼워 넣음
def insertion(values, steps):
    values = list(values)
    for i in range(1, steps + 1):
        j = i
        while j > 0 and values[j - 1] > values[j]:
            values[j - 1], values[j] = values[j], values[j - 1]
            j -= 1
    return values


# 역할: 정렬된 두 배열을 병합하고 비교 횟수를 함께 반환
def merge(left, right):
    merged, i, j, compared = [], 0, 0, 0
    while i < len(left) and j < len(right):
        compared += 1
        if left[i] <= right[j]:
            merged.append(left[i])
            i += 1
        else:
            merged.append(right[j])
            j += 1
    return merged + left[i:] + right[j:], compared


def merge_sort(values):
    if len(values) <= 1:
        return list(values)
    mid = len(values) // 2
    return merge(merge_sort(values[:mid]), merge_sort(values[mid:]))[0]


# 역할: 로무토 분할(마지막 원소가 피벗, 피벗보다 작은 원소를 앞 구역으로 모음); 피벗 위치와 비교 횟수 반환
def lomuto(values, lo, hi):
    pivot, boundary = values[hi], lo
    for j in range(lo, hi):
        if values[j] < pivot:
            values[boundary], values[j] = values[j], values[boundary]
            boundary += 1
    values[boundary], values[hi] = values[hi], values[boundary]
    return boundary, hi - lo


# 역할: 퀵 정렬을 끝까지 수행하며 쓰인 피벗(왼쪽 부분 먼저)과 비교 횟수 합을 기록
def quick_sort(values, lo=0, hi=None, log=None):
    hi = len(values) - 1 if hi is None else hi
    log = log if log is not None else {'pivots': [], 'compared': 0}
    if lo < hi:
        log['pivots'].append(values[hi])
        p, compared = lomuto(values, lo, hi)
        log['compared'] += compared
        quick_sort(values, lo, p - 1, log)
        quick_sort(values, p + 1, hi, log)
    return log


def join(values):
    return ','.join(str(v) for v in values)


# 역할: 문항 ID → 정답 키와 별도로 계산한 기대 답
def expected_answers(items):
    tree = layout(16)
    parent_of_4 = next(p for p, cs in tree.items() if 4 in cs)
    inserted = [2, 5, 3, 8, 9, 7, 1]
    sift_up(inserted, len(inserted) - 1)
    popped = [1, 3, 6, 5, 9, 8]
    popped[0] = popped.pop()  # 마지막 원소를 루트로 옮긴 뒤 아래로 재정렬
    sift_down(popped, 0)
    fifo, pq = deque(), []
    for value in (7, 3, 9, 4):
        fifo.append(value)
        heapq.heappush(pq, value)
    tasks = []
    for number, priority in ((1, 3), (2, 1), (3, 4), (4, 2)):
        heapq.heappush(tasks, (priority, number))
    order = [heapq.heappop(tasks)[1]]
    heapq.heappush(tasks, (0, 5))
    order += [heapq.heappop(tasks)[1] for _ in range(len(tasks))]
    worst = list(range(1, 1001)) + [0]  # 모든 원소보다 작은 값을 넣어 루트까지 올라가는 최악의 경우
    split = [38, 27, 43, 3, 9, 82, 10]  # 가운데 = 길이 // 2로 나눈 뒷부분이 마지막 병합에 들어감
    merge_t = lambda n: 0 if n == 1 else 2 * merge_t(n // 2) + (n - 1)
    partitioned = [7, 2, 9, 4, 3, 8, 5]
    lomuto(partitioned, 0, len(partitioned) - 1)
    return {
        'heap-d1': join([parent_of_4] + tree[4]),
        'heap-c1': join(inserted),
        'heap-c2': join(popped),
        'priority-queue-d1': join([fifo.popleft(), fifo.popleft(), heapq.heappop(pq), heapq.heappop(pq)]),
        'priority-queue-c1': join(order),
        'priority-queue-c2': sift_up(worst, len(worst) - 1),
        'sorting-basics-d1': join(selection([29, 10, 14, 37, 13], steps=2)),
        'sorting-basics-c1': join(insertion([7, 3, 5, 8, 2, 6], steps=3)),
        'sorting-basics-c2': join(mark for _, mark in selection([(4, 1), (4, 2), (3, 3)], key=lambda v: v[0])),
        'merge-sort-d1': merge([3, 8, 12], [1, 9, 10, 15])[1],
        'merge-sort-c1': join(merge_sort(split[len(split) // 2:])),
        'merge-sort-c2': merge_t(8),
        'quick-sort-d1': join(partitioned),
        'quick-sort-c1': join(quick_sort([4, 1, 3, 9, 7])['pivots']),
        'quick-sort-c2': quick_sort(list(range(1, 7)))['compared'],
    }


class HeapSortItemTests(unittest.TestCase):
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

    # 검증 목적: 안정 정렬 선택형은 보기 중 파이썬의 안정 정렬(sorted) 결과와 같은 보기가 정답 하나뿐인지 확인
    def test_stable_sort_choice_is_unique(self):
        item = next(i for i in self.items if i.id == 'sorting-basics-d2')
        records = [(90, '민수'), (85, '지영'), (90, '하늘')]
        stable = ', '.join(f'({score},{name})' for score, name in sorted(records, key=lambda r: r[0]))
        self.assertEqual([label for label, text in item.options.items() if text == stable], [item.answer])

    # 검증 목적: 모든 단답 정답이 문항 내용으로부터 독립 계산한 값과 일치하는지 확인
    def test_short_answers_match_independent_computation(self):
        short = {i.id: i for i in self.items if i.format == 'short_exact'}
        expected = expected_answers(self.items)
        self.assertEqual(set(expected), set(short))
        for item_id, value in expected.items():
            self.assertEqual(normalize_short_answer(str(value)), short[item_id].answer, item_id)


if __name__ == '__main__':
    unittest.main()
