## @file
# @brief 비교 실험 자료(backend/src/main/resources/experiment/materials.json)의 형식·ID 규칙·문항 수를 검사하고,
# 단답 정답을 문항 내용으로부터 독립 계산해 대조하며, 문항 은행·CS-300과 겹치는 문항이 없는지 확인
# - 자료는 Spring 리소스지만 Java 로더(실험 Task 2)와 따로 이 파일로 먼저 검증함
import json
import math
import os
import re
import unittest
from collections import Counter, deque
from difflib import SequenceMatcher
from itertools import permutations
from pathlib import Path

from eval.cs300_overlap import THRESHOLD, find_overlaps
from serving.item_bank import load_item_bank, normalize_short_answer

ROOT = Path(__file__).resolve().parents[2]
MATERIALS = ROOT / 'backend' / 'src' / 'main' / 'resources' / 'experiment' / 'materials.json'
# CS-300은 Git 밖 로컬 자료라 worktree에는 없음; 환경 변수 CS300_QUESTIONS로 메인 체크아웃의 파일을 가리킬 수 있음
CS300 = Path(os.environ.get('CS300_QUESTIONS', ROOT / 'codex' / 'model-evaluation' / 'cs-300-v1' / 'questions.jsonl'))
# 실험 후보 13개: 기존 자료구조 8개 뒤에 과목 경계 진단을 관찰할 5개를 추가; 자료 순서는 배정 동점 규칙
CONCEPTS = ['hash-table', 'bst', 'heap', 'quick-sort', 'merge-sort', 'bfs', 'topological-sort', 'dynamic-programming',
            'sjf', 'priority-scheduling', 'btree-index', 'routing', 'cache-locality']
# 읽기 글 길이(글자 수): 1부는 15분 학습의 본문, 2부는 1부를 이해해야 읽히는 이어지는 내용
PART1_RANGE, PART2_RANGE = (400, 600), (200, 400)
ITEM_FIELDS = {'id', 'format', 'question', 'options', 'answer', 'rationale', 'rubric'}
# 오답에만 몰리면 지식 없이 오답을 지울 단서가 되는 극단 표현(CS-300 선택형이 단서로 풀린 원인)
EXTREME_WORDS = ('항상', '반드시', '절대', '전혀', '무조건')


def load():
    return json.loads(MATERIALS.read_text(encoding='utf-8'))


def all_items(materials):
    return [item for concept in materials['concepts'] for form in ('A', 'B') for item in concept['pre_post'][form]]


def join(values):
    return ','.join(str(v) for v in values)


def numbers_in(text):
    return [int(v) for v in re.findall(r'-?\d+', text)]


## @brief 문항 속 "[1,2,3]" 꼴 배열을 읽음; 정답 계산이 문항 본문과 어긋나지 않게 하기 위함
def array_in(question):
    return [int(v) for v in re.search(r'\[([\d,]+)\]', question).group(1).split(',')]


## @brief "키 a, b, c를(을)" 꼴 키 목록과 "mod m"의 m을 읽어 체이닝 버킷 길이 목록 반환
def chain_lengths(question):
    keys = [int(v) for v in re.search(r'키 ([\d, ]+)[을를]', question).group(1).split(', ')]
    m = int(re.search(r'mod (\d+)', question).group(1))
    buckets = Counter(k % m for k in keys)
    return [buckets[b] for b in range(m)]


## @brief BST를 반복문으로 만듦(정렬된 키 1,023개도 재귀 깊이 걱정 없이); 노드는 [키, 왼쪽, 오른쪽]
def bst(keys):
    root = None
    for key in keys:
        node = [key, None, None]
        if root is None:
            root = node
            continue
        cur = root
        while True:
            side = 1 if key < cur[0] else 2
            if cur[side] is None:
                cur[side] = node
                break
            cur = cur[side]
    return root


def leaves(node):
    if node is None:
        return []
    if node[1] is None and node[2] is None:
        return [node[0]]
    return leaves(node[1]) + leaves(node[2])


## @brief 키 key를 찾을 때 비교하는 노드 수(없는 키면 빈 자리에 닿을 때까지)
def comparisons(root, key):
    count, cur = 0, root
    while cur is not None:
        count += 1
        if key == cur[0]:
            break
        cur = cur[1] if key < cur[0] else cur[2]
    return count


def median_first(keys):
    if not keys:
        return []
    mid = len(keys) // 2
    return [keys[mid]] + median_first(keys[:mid]) + median_first(keys[mid + 1:])


## @brief 최소 힙에 넣고 위로 재정렬; (부모와 바꾼 횟수, 최종 인덱스) 반환
def heap_push(heap, value):
    heap = heap + [value]
    i, swaps = len(heap) - 1, 0
    while i > 0 and heap[(i - 1) // 2] > heap[i]:
        heap[i], heap[(i - 1) // 2] = heap[(i - 1) // 2], heap[i]
        i, swaps = (i - 1) // 2, swaps + 1
    return swaps, i


## @brief 완전 이진 트리를 위층·왼쪽부터 채우는 규칙만으로 {자식: 부모}를 만듦(부모 공식을 쓰지 않고 확인하기 위함)
def parents(n):
    found, waiting, nxt = {}, deque([0]), 1
    while nxt < n:
        parent = waiting.popleft()
        for _ in range(2):
            if nxt < n:
                found[nxt] = parent
                waiting.append(nxt)
                nxt += 1
    return found


## @brief 로무토 분할(마지막 원소가 피벗, 피벗보다 작은 원소를 앞으로 모음); (분할 뒤 배열, 피벗 위치) 반환
def lomuto(values):
    values, pivot, boundary = list(values), values[-1], 0
    for j in range(len(values) - 1):
        if values[j] < pivot:
            values[boundary], values[j] = values[j], values[boundary]
            boundary += 1
    values[boundary], values[-1] = values[-1], values[boundary]
    return values, boundary


def merge_sort(values):
    if len(values) <= 1:
        return list(values)
    mid = len(values) // 2
    left, right, merged = merge_sort(values[:mid]), merge_sort(values[mid:]), []
    while left and right:
        merged.append((left if left[0] <= right[0] else right).pop(0))
    return merged + left + right


## @brief 문항 속 무방향 간선 "a-b"로 BFS(이웃은 작은 번호부터, 큐에 넣을 때 방문 표시); {정점: (부모, 거리)}
def bfs(question, start):
    adjacency = {}
    for a, b in re.findall(r'(\d+)-(\d+)', question):
        adjacency.setdefault(int(a), set()).add(int(b))
        adjacency.setdefault(int(b), set()).add(int(a))
    found, queue = {start: (None, 0)}, deque([start])
    while queue:
        v = queue.popleft()
        for n in sorted(adjacency[v]):
            if n not in found:
                found[n] = (v, found[v][1] + 1)
                queue.append(n)
    return found


def directed_edges(text):
    return [(int(a), int(b)) for a, b in re.findall(r'(\d+)→(\d+)', text)]


## @brief 정점 순열을 모두 시도해 모든 간선 방향을 지키는 순서(위상 순서)의 개수를 셈
def topological_orders(edges):
    vertices = sorted({v for e in edges for v in e})
    return sum(all(order.index(a) < order.index(b) for a, b in edges) for order in permutations(vertices))


## @brief 한 번에 오를 수 있는 칸 수의 순서열을 모두 나열해 n칸을 오르는 방법 수를 셈(점화식 표를 쓰지 않음)
def stair_ways(steps, n):
    if n == 0:
        return 1
    return sum(stair_ways(steps, n - s) for s in steps if s <= n)


## @brief 문항의 프로세스 표에서 비선점 실행을 독립 추적해 P3 시작과 전체 대기 합을 계산
# - 시뮬레이터를 호출하지 않아 같은 구현 오류가 정답과 검산에 함께 섞이지 않게 함
def scheduling_answer(question, priority=False):
    pending = [(name, int(arrival), int(burst), int(rank or 0))
               for name, arrival, burst, rank in re.findall(r'(P\d+)\((\d+),(\d+)(?:,(\d+))?\)', question)]
    time, starts, waits = 0, {}, []
    while pending:
        ready = [p for p in pending if p[1] <= time]
        if not ready:
            time = min(p[1] for p in pending)
            continue
        selected = min(ready, key=lambda p: (p[3] if priority else p[2], p[1], p[0]))
        name, arrival, burst, _ = selected
        starts[name] = time
        waits.append(time - arrival)
        time += burst
        pending.remove(selected)
    return join([starts['P3'], sum(waits)])


## @brief 문항에 주어진 루트·내부·리프 경로에서 첫 방문한 비상주 페이지 수를 셈
def page_read_answer(question):
    paths = re.findall(r'R-([A-Z]\d)-([A-Z]\d)', question)
    return join([len({internal for internal, _ in paths}), len({page for path in paths for page in path})])


## @brief 링크 비용을 문항에서 읽고 모든 단순 경로를 열거해 장애 전후 최소 비용을 비교
def routing_answer(question):
    edges = {(a, b): int(cost) for a, b, cost in re.findall(r'([A-Z])-([A-Z])=(\d+)', question)}
    broken = re.search(r'([A-Z])-([A-Z]) 링크만 끊긴', question).groups()

    ## @brief 현재 링크 집합에서 S→T의 모든 단순 경로 비용 중 최솟값 반환
    def cheapest():
        totals = []

        ## @brief 이미 지난 정점을 다시 방문하지 않고 인접 링크를 따라 비용 합을 모음
        def visit(node, seen, cost):
            if node == 'T':
                totals.append(cost)
                return
            for (a, b), weight in edges.items():
                neighbor = b if a == node else a if b == node else None
                if neighbor and neighbor not in seen:
                    visit(neighbor, seen | {neighbor}, cost + weight)

        visit('S', {'S'}, 0)
        return min(totals)

    before = cheapest()
    del edges[broken]
    after = cheapest()
    return join([after, after - before])


## @brief 문항의 배열 길이·원소 크기·주소·블록·라인 수에서 두 번 순회의 캐시 적중을 추적
def cache_answer(question):
    n = int(re.search(r'원소 (\d+)개인 배열', question).group(1))
    width = int(re.search(r'원소는 (\d+)바이트', question).group(1))
    start = int(re.search(r'배열 시작은 바이트 주소 (\d+)', question).group(1))
    block_bytes, lines = map(int, re.search(r'블록 (\d+)바이트·라인 (\d+)개', question).groups())
    cache, misses = {}, 0
    for _ in range(2):
        for index in range(n):
            block = (start + index * width) // block_bytes
            line = block % lines
            if cache.get(line) != block:
                misses += 1
                cache[line] = block
    return join([misses, 2 * n - misses])


## @brief 문항 ID → 정답 키와 별도로 계산한 기대 답
def expected_answers(items):
    q = {item['id']: item['question'] for item in items}
    ha, hb = chain_lengths(q['exp-hash-table-A1']), chain_lengths(q['exp-hash-table-B1'])
    heap_a = heap_push(array_in(q['exp-heap-A1']), numbers_in(q['exp-heap-A1'].split(']')[1])[0])
    heap_b = heap_push(array_in(q['exp-heap-B1']), numbers_in(q['exp-heap-B1'].split(']')[1])[0])
    qa, pa = lomuto(array_in(q['exp-quick-sort-A1']))
    qb, pb = lomuto(array_in(q['exp-quick-sort-B1']))
    ma, mb = array_in(q['exp-merge-sort-A1']), array_in(q['exp-merge-sort-B1'])
    ba, bb = bfs(q['exp-bfs-A1'], 1)[6], bfs(q['exp-bfs-B1'], 1)[6]
    answers = {
        'exp-hash-table-A1': join([ha.count(0), max(ha)]),
        'exp-hash-table-B1': join([hb.count(0), max(hb)]),
        'exp-bst-A1': join(sorted(leaves(bst(numbers_in(q['exp-bst-A1'].split(':')[1].split('.')[0]))))),
        'exp-bst-B1': join(sorted(leaves(bst(numbers_in(q['exp-bst-B1'].split(':')[1].split('.')[0]))))),
        'exp-heap-A1': join(heap_a),
        'exp-heap-B1': join(heap_b),
        'exp-quick-sort-A1': join(qa[pa + 1:]),  # A·B 모두 피벗 오른쪽: 왼쪽은 작은 값을 원래 순서대로 적기만 해도 맞아 교환을 따라가지 않아도 됨
        'exp-quick-sort-B1': join(qb[pb + 1:]),
        'exp-merge-sort-A1': join(v for i in range(0, len(ma), 2) for v in merge_sort(ma[i:i + 2])),  # 크기 2로 나눠 병합한 단계
        'exp-merge-sort-B1': join(v for i in range(0, len(mb), 2) for v in merge_sort(mb[i:i + 2])),
        'exp-bfs-A1': join(ba),
        'exp-bfs-B1': join(bb),
        'exp-topological-sort-A1': topological_orders(directed_edges(q['exp-topological-sort-A1'])),
        'exp-topological-sort-B1': topological_orders(directed_edges(q['exp-topological-sort-B1'])),
        'exp-dynamic-programming-A1': stair_ways((1, 3), 8),
        'exp-dynamic-programming-B1': stair_ways((1, 2, 4), 6),
    }
    for form in ('A', 'B'):
        for concept, compute in [('sjf', scheduling_answer),
                                 ('priority-scheduling', lambda question: scheduling_answer(question, priority=True)),
                                 ('btree-index', page_read_answer), ('routing', routing_answer),
                                 ('cache-locality', cache_answer)]:
            item_id = f'exp-{concept}-{form}1'
            answers[item_id] = compute(q[item_id])
    return answers


## @brief 선택형 중 계산으로 정답을 확인할 수 있는 문항의 기대 정답 보기 문자열
def expected_choices(items):
    sorted_tree, low_tree = bst(range(1, 1024)), bst(median_first(list(range(1, 1024))))
    absent = [k + 0.5 for k in range(0, 1024)]  # 키 사이의 없는 값을 모두 시도
    tree = parents(32)
    choices = {
        'exp-hash-table-A2': str(13 % 8),  # 재해싱: 버킷 4개에서 1번이던 키 13의 새 버킷
        'exp-bst-A2': str(max(comparisons(sorted_tree, k) for k in absent)),
        'exp-bst-B2': str(max(comparisons(low_tree, k) for k in absent)),
        'exp-heap-A2': str(next(c for c, p in tree.items() if p == tree[13] and c != 13)),
        'exp-heap-B2': str(tree[tree[10]]),
        'exp-merge-sort-A2': str(int(math.log2(16))),
    }
    questions = {item['id']: item['question'] for item in items}
    for form in ('A', 'B'):
        sjf_id, priority_id = f'exp-sjf-{form}2', f'exp-priority-scheduling-{form}2'
        long, short = map(int, re.findall(r'\(실행 (\d+)\)', questions[sjf_id]))
        choices[sjf_id] = f'②가 SJF이며 대기 시간 합은 {long}에서 {short}로 줄어든다'
        name, initial, waited = re.search(r'요청 ([A-Z])[은는] 기본 우선순위 (\d+)에서 (\d+)단위', questions[priority_id]).groups()
        newcomer = re.search(r'새 요청 ([A-Z])', questions[priority_id]).group(1)
        interval = int(re.search(r'기다린 시간 (\d+)단위마다', questions[priority_id]).group(1))
        aged = int(initial) - int(waited) // interval
        choices[priority_id] = f'{name}의 현재 숫자는 {aged}이므로 {newcomer}보다 먼저 고른다'
        tree_id = f'exp-btree-index-{form}2'
        keys = array_in(questions[tree_id])
        target = int(re.search(r'키 (\d+)을 찾기', questions[tree_id]).group(1))
        lower, upper = next((a, b) for a, b in zip(keys, keys[1:]) if a < target < b)
        choices[tree_id] = f'{lower}보다 크고 {upper}보다 작은 범위'
        cache_id = f'exp-cache-locality-{form}2'
        block_bytes, lines = map(int, re.search(r'블록 (\d+)바이트·라인 (\d+)개', questions[cache_id]).groups())
        first, second = (int(address) // block_bytes for address in re.findall(r'[A-D]=(\d+)', questions[cache_id]))
        if first % lines != second % lines:
            raise AssertionError(f'{cache_id}: 충돌을 묻는 두 블록이 다른 라인에 있음')
        choices[cache_id] = f'블록 {first}과 {second}가 같은 라인에 들어가 서로 교체한다'
    return choices


class ExperimentMaterialsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.materials = load()
        cls.items = all_items(cls.materials)

    ## @brief 확장된 13개 개념의 읽기 글·A/B형 각 자동 2+서술 1 구성과 ID 규칙 검증
    def test_structure_and_ids(self):
        self.assertIsInstance(self.materials['version'], int)
        self.assertEqual([c['id'] for c in self.materials['concepts']], CONCEPTS)
        for concept in self.materials['concepts']:
            self.assertTrue(concept['title'], concept['id'])
            self.assertTrue(PART1_RANGE[0] <= len(concept['passage']['part1']) <= PART1_RANGE[1], concept['id'])
            self.assertTrue(PART2_RANGE[0] <= len(concept['passage']['part2']) <= PART2_RANGE[1], concept['id'])
            self.assertEqual(set(concept['pre_post']), {'A', 'B'})
            for form in ('A', 'B'):
                items = concept['pre_post'][form]
                self.assertEqual([i['id'] for i in items], [f'exp-{concept["id"]}-{form}{n}' for n in (1, 2, 3)])
                self.assertEqual([i['format'] != 'explanation' for i in items], [True, True, False])
            # 같은 위치의 A·B 문항은 같은 형식(같은 학습 목표를 비슷한 난이도로 묻기 위함)
            self.assertEqual([i['format'] for i in concept['pre_post']['A']], [i['format'] for i in concept['pre_post']['B']])

    # 검증 목적: 문항 필드가 문항 은행 Item과 같은 규칙(선택지 A~D, 정규화된 단답, 서술은 루브릭 2~4개)을 따름
    def test_item_fields(self):
        for item in self.items:
            self.assertLessEqual(set(item), ITEM_FIELDS, item['id'])
            self.assertTrue(0 < len(item['question']) <= 500 and 0 < len(item['rationale']) <= 400, item['id'])
            if item['format'] == 'multiple_choice':
                self.assertEqual(tuple(item['options']), ('A', 'B', 'C', 'D'), item['id'])
                self.assertEqual(len({v.strip() for v in item['options'].values()}), 4, item['id'])
                self.assertIn(item['answer'], 'ABCD')
                self.assertNotIn('rubric', item)
            elif item['format'] == 'short_exact':
                self.assertEqual(item['answer'], normalize_short_answer(item['answer']), item['id'])
                self.assertNotIn('options', item)
            else:
                self.assertEqual(item['format'], 'explanation')
                self.assertTrue(2 <= len(item['rubric']) <= 4, item['id'])
                self.assertFalse({'answer', 'options'} & set(item), item['id'])

    # 검증 목적: 선택형 정답 위치가 고르고, 정답만 유독 길거나 극단 표현이 오답에만 있는 단서가 없음
    def test_choices_balanced_without_cue(self):
        choices = [i for i in self.items if i['format'] == 'multiple_choice']
        labels = Counter(i['answer'] for i in choices)
        self.assertEqual(set(labels), set('ABCD'))
        self.assertLessEqual(max(labels.values()), math.ceil(len(choices) / 4) + 1)
        for item in choices:
            lengths = {k: len(v) for k, v in item['options'].items()}
            self.assertLessEqual(lengths[item['answer']], max(v for k, v in lengths.items() if k != item['answer']), item['id'])
            wrong = any(w in v for k, v in item['options'].items() if k != item['answer'] for w in EXTREME_WORDS)
            self.assertFalse(wrong and not any(w in item['options'][item['answer']] for w in EXTREME_WORDS), item['id'])

    # 검증 목적: 단답 정답이 문항 내용으로부터 독립 계산한 값과 같음(정답 키를 상수로 다시 적지 않음)
    def test_short_answers_match_computation(self):
        short = {i['id']: i for i in self.items if i['format'] == 'short_exact'}
        expected = expected_answers(self.items)
        self.assertEqual(set(expected), set(short))
        for item_id, value in expected.items():
            self.assertEqual(normalize_short_answer(str(value)), short[item_id]['answer'], item_id)

    # 검증 목적: 계산으로 확인할 수 있는 선택형은 계산 결과와 같은 보기가 정답 하나뿐임
    def test_computable_choices(self):
        by_id = {i['id']: i for i in self.items}
        for item_id, value in expected_choices(self.items).items():
            item = by_id[item_id]
            self.assertEqual([k for k, v in item['options'].items() if v == value], [item['answer']], item_id)
        dag = by_id['exp-topological-sort-B2']
        acyclic = [k for k, v in dag['options'].items() if topological_orders(directed_edges(v)) > 0]
        self.assertEqual(acyclic, [dag['answer']])

    # 검증 목적: 학습 중 확인 문항(문항 은행)으로 사후 시험을 미리 연습하지 않도록, 은행 문항과 문장이 겹치지 않음
    def test_no_overlap_with_item_bank(self):
        mine = [{'id': i['id'], 'question': i['question']} for i in self.items]
        bank = [{'id': i.id, 'question': i.question} for i in load_item_bank()]
        self.assertEqual(find_overlaps(mine, bank), [])
        for item in mine:
            closest = max(SequenceMatcher(None, item['question'], other['question']).ratio() for other in bank)
            self.assertLess(closest, 0.6, item['id'])

    # 검증 목적: 평가 전용 CS-300과 문장이 겹치지 않음(정규화 0.8, 원문 0.6 기준); CS-300은 Git 밖 로컬 자료라 없으면 건너뜀
    @unittest.skipUnless(CS300.exists(), 'CS-300 로컬 자료 없음')
    def test_no_overlap_with_cs300(self):
        cs300 = [json.loads(line) for line in CS300.read_text(encoding='utf-8').splitlines() if line.strip()]
        mine = [{'id': i['id'], 'question': i['question']} for i in self.items]
        self.assertEqual(find_overlaps(mine, cs300, THRESHOLD), [])
        for item in mine:
            closest = max(SequenceMatcher(None, item['question'], other['question']).ratio() for other in cs300)
            self.assertLess(closest, 0.6, item['id'])


if __name__ == '__main__':
    unittest.main()
