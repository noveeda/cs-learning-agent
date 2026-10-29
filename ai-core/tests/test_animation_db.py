## @file
# @brief 데이터베이스 과목 장면(표·속성 닫힘·조인·B-트리·해시 인덱스·트랜잭션)과 개념별 예시 장면 파일(scenes.json)을 검증
# - 시뮬레이터는 파이썬으로 따로 계산한 참조 결과(필터·집계·닫힘·조인·정렬)와 마지막 상태를 비교해 틀린 그림을 잡음
# - 실행할 수 없는 요청(없는 표·열, 키 중복, 범위 밖 크기)은 그림 없이 ValueError로 거부되는지 확인
# - scenes.json은 개념마다 서로 다른 장면 3개가 실제로 실행되고 화면 분량 안에 드는지 확인
import json
import unittest
from collections import Counter
from itertools import combinations
from pathlib import Path

from serving.animation import SCENE_FORMATS, SIMULATORS, simulate
from serving.animation.model import AnimationRequestModel

SCENES = Path(__file__).resolve().parents[1] / 'content' / 'db' / 'scenes.json'
# 과목 안 6단계 미만이라 화면에 남는 데이터베이스 개념 전부
CONCEPTS = {'relational-model', 'sql-query', 'normalization', 'btree-index', 'hash-index', 'join-algorithms',
            'transaction-acid'}
# 교육적 분량 권장치: 이전·다음 버튼으로 한 장면을 끝까지 따라가기 쉬운 단계 수(엔진 상한 16보다 작게 잡음)
SCENE_MAX_FRAMES = 12


## @brief dict 요청을 스키마로 검증한 뒤 시뮬레이터를 실행
def run(data: dict):
    return simulate(AnimationRequestModel.model_validate(data).root)


## @brief 화면에서 차지하는 글자 폭. 한글은 영문·숫자 두 글자 폭으로 셈
def width(text: str) -> int:
    return sum(2 if '가' <= ch <= '힣' else 1 for ch in text)


## @brief 프레임에서 한 표(이름 label 아래 x 범위)의 행 값을 위에서부터 읽음
# - 거부·중복 제거로 이번 단계에만 보이는 removed 행은 상태가 아니므로 뺌
def table_rows(frame, name):
    title = next(b for b in frame.boxes if b.shape == 'label' and b.y == 0 and b.label == name)
    headers = sorted((b for b in frame.boxes if b.shape == 'label' and b.y == 1 and b.x >= title.x), key=lambda b: b.x)
    columns = []
    for box in headers:
        if columns and box.x != columns[-1] + 1:
            break
        columns.append(box.x)
    rows = {}
    for box in frame.boxes:
        if box.shape == 'cell' and box.x in columns and box.tone != 'removed':
            rows.setdefault(box.y, {})[box.x] = box.label
    return [[row[x] for x in columns] for _, row in sorted(rows.items())]


## @brief 프레임의 모든 상자를 비교용 튜플로 바꿈(장면끼리 같은 그림인지 판단)
def picture(animation):
    return tuple((tuple(sorted((b.label, b.x, b.y, b.shape, b.tone) for b in f.boxes)), f.caption) for f in animation.frames)


class ScenesFileTests(unittest.TestCase):
    ## @brief 개념별 장면 파일을 한 번만 읽고 모든 장면을 실행해 둠
    @classmethod
    def setUpClass(cls):
        cls.scenes = json.loads(SCENES.read_text(encoding='utf-8'))
        cls.animations = {concept: [run(scene) for scene in scenes] for concept, scenes in cls.scenes.items()}

    # 검증 목적: 화면에 남는 데이터베이스 개념 7개 모두에 장면이 정확히 3개씩 있는지 확인(빠진 개념·남는 개념 방지)
    def test_every_concept_has_exactly_three_scenes(self):
        self.assertEqual(set(self.scenes), CONCEPTS)
        for concept, scenes in self.scenes.items():
            self.assertEqual(len(scenes), 3, concept)

    # 검증 목적: 같은 개념의 세 장면이 요청도 그림도 서로 달라, 숫자만 바꾼 복사본이 아닌지 확인
    # - 요청 JSON이 다르고, 프레임 상자·캡션 수열도 달라야 함
    # - 같은 kind끼리는 연산 이름 구성이나 설정(알고리즘·표 구성)이 달라야 같은 장면의 값만 바꾼 것으로 보지 않음
    def test_three_scenes_per_concept_differ(self):
        for concept, scenes in self.scenes.items():
            for a, b in combinations(range(3), 2):
                self.assertNotEqual(scenes[a], scenes[b], concept)
                self.assertNotEqual(picture(self.animations[concept][a]), picture(self.animations[concept][b]), concept)
                if scenes[a]['kind'] == scenes[b]['kind']:
                    self.assertNotEqual(self.shape_of(scenes[a]), self.shape_of(scenes[b]), f'{concept} {a} {b}')

    ## @brief 장면의 시나리오 모양: 값은 빼고 연산 이름 순서와 알고리즘·표 개수 같은 설정만 남김
    # - 조인은 연산 목록이 없으므로 입력 정렬 여부와 양쪽 중복 키 여부(같은 key 묶음 조합)를 시나리오로 봄
    @staticmethod
    def shape_of(scene):
        ops = tuple(op['op'] for op in scene.get('ops', []))
        join = ()
        if scene['kind'] == 'db_join':
            keys = [[row['key'] for row in scene[side]] for side in ('left', 'right')]
            join = (all(k == sorted(k) for k in keys), all(len(set(k)) < len(k) for k in keys))
        return (scene['kind'], ops, scene.get('algorithm'), len(scene.get('tables', [])), len(scene.get('starts', [])),
                scene.get('max_keys'), join)

    # 검증 목적: 모든 장면이 권장 단계 수 안에 들고, 상자 글자가 칸 폭을 넘지 않는지 확인
    # - cell·slot·node는 영문 4글자(한글 2글자) 폭까지, label은 6폭까지: 그 이상은 글자가 눌려 읽기 어려움
    def test_scenes_fit_frame_and_label_limits(self):
        for concept, animations in self.animations.items():
            for animation in animations:
                self.assertGreaterEqual(len(animation.frames), 3, f'{concept} {animation.title}')
                self.assertLessEqual(len(animation.frames), SCENE_MAX_FRAMES, f'{concept} {animation.title}')
                for frame in animation.frames:
                    for box in frame.boxes:
                        limit = 6 if box.shape == 'label' else 4
                        self.assertLessEqual(width(box.label), limit, f'{concept} {box.label}')
                    for arrow in frame.arrows:
                        self.assertLessEqual(width(arrow.label or ''), 6, f'{concept} {arrow.label}')

    # 검증 목적: 트랜잭션 장면이 끝난 트랜잭션 이름을 다시 begin하지 않는지 확인(새 트랜잭션은 새 이름)
    def test_transaction_scenes_never_reuse_names(self):
        for scene in self.scenes['transaction-acid']:
            begun = [op['tx'] for op in scene['ops'] if op['op'] == 'begin']
            self.assertEqual(len(begun), len(set(begun)), scene)

    # 검증 목적: 해시 인덱스는 기존 hash_table 장면을 쓰고, 나머지는 새 db_ 장면 종류가 개념에 맞게 쓰였는지 확인
    def test_scene_kinds_match_concepts(self):
        expected = {'relational-model': {'db_table'}, 'sql-query': {'db_table'},
                    'normalization': {'db_closure', 'db_table'}, 'btree-index': {'db_btree'},
                    'hash-index': {'hash_table', 'db_hash_index'}, 'join-algorithms': {'db_join'},
                    'transaction-acid': {'db_transaction'}}
        for concept, scenes in self.scenes.items():
            self.assertEqual({scene['kind'] for scene in scenes}, expected[concept], concept)


class RegistryTests(unittest.TestCase):
    # 검증 목적: 새 장면 종류가 시뮬레이터 표·요청 합집합·형식 예시에 모두 등록되고 예시가 그대로 실행되는지 확인
    def test_db_kinds_registered_and_examples_run(self):
        kinds = {'db_table', 'db_closure', 'db_join', 'db_btree', 'db_hash_index', 'db_transaction'}
        self.assertLessEqual(kinds, set(SIMULATORS))
        for kind in kinds:
            self.assertEqual(run(SCENE_FORMATS[kind]).kind, kind)


## @brief 학생 표: id·name·dept 세 열, 4행(이름 Kim이 두 번)
STUDENTS = {'name': 'Stu', 'columns': ['id', 'name', 'dept'],
            'rows': [[1, 'Kim', 'CS'], [2, 'Lee', 'EE'], [3, 'Kim', 'EE'], [4, 'Ahn', 'CS']]}


class TableTests(unittest.TestCase):
    # 검증 목적: 키 검사가 중복 값을 찾아 키가 될 수 없다고 하고, 유일하지만 더 줄일 수 있는 속성 집합은 최소가 아니라고 구분하는지 확인
    def test_check_key_reports_duplicates_and_minimality(self):
        ops = [{'op': 'check_key', 'table': 'Stu', 'columns': c} for c in (['name'], ['id'], ['id', 'name'])]
        animation = run({'kind': 'db_table', 'tables': [STUDENTS], 'ops': ops})
        dup, unique, superkey = (f.caption for f in animation.frames[1:])
        self.assertIn('Kim', dup)
        self.assertIn('키가 될 수 없음', dup)
        self.assertIn('후보 키', unique)
        self.assertIn('최소가 아님', superkey)
        removed = [b for b in animation.frames[1].boxes if b.tone == 'removed']
        self.assertEqual(sorted(b.label for b in removed), ['Kim', 'Kim'])

    # 검증 목적: 기본 키 중복과 없는 부모 키를 가리키는 외래 키 삽입을 거부하고, 제약을 지킨 삽입만 표에 남는지 확인
    def test_insert_enforces_primary_and_foreign_keys(self):
        tables = [{'name': 'Stu', 'columns': ['id', 'dept'], 'key': ['id'], 'foreign': {'column': 'dept', 'table': 'Dept'},
                   'rows': [[1, 'CS']]},
                  {'name': 'Dept', 'columns': ['code', 'bldg'], 'key': ['code'], 'rows': [['CS', 'A']]}]
        ops = [{'op': 'insert', 'table': 'Stu', 'row': [1, 'CS']},
               {'op': 'insert', 'table': 'Stu', 'row': [2, 'ME']},
               {'op': 'insert', 'table': 'Dept', 'row': ['ME', 'B']},
               {'op': 'insert', 'table': 'Stu', 'row': [2, 'ME']}]
        animation = run({'kind': 'db_table', 'tables': tables, 'ops': ops})
        captions = [f.caption for f in animation.frames]
        self.assertIn('기본 키', captions[1])
        self.assertIn('거부', captions[1])
        self.assertIn('참조 무결성', captions[2])
        self.assertNotIn('거부', captions[4])
        self.assertEqual(table_rows(animation.frames[2], 'Stu'), [['1', 'CS']])
        self.assertEqual(table_rows(animation.frames[-1], 'Stu'), [['1', 'CS'], ['2', 'ME']])
        self.assertTrue(any(a.label == 'FK' for a in animation.frames[0].arrows))

    # 검증 목적: WHERE가 행마다 한 단계씩 조건을 검사하고, 남는 행이 파이썬 필터 결과와 같은지 확인
    def test_where_scans_each_row(self):
        rows = [['Kim', 300], ['Lee', 200], ['Ahn', 450]]
        animation = run({'kind': 'db_table', 'tables': [{'name': 'Emp', 'columns': ['name', 'pay'], 'rows': rows}],
                         'ops': [{'op': 'where', 'table': 'Emp', 'column': 'pay', 'cmp': '>=', 'value': 300},
                                 {'op': 'check_key', 'table': 'Emp', 'columns': ['name']}]})
        self.assertEqual(len(animation.frames), 1 + len(rows) + 1)
        self.assertEqual(table_rows(animation.frames[-1], 'Emp'), [[n, str(p)] for n, p in rows if p >= 300])

    # 검증 목적: GROUP BY 집계 결과가 파이썬 참조 합계와 같고, HAVING은 그룹 결과에만 적용되는지 확인
    def test_group_sum_and_having_match_reference(self):
        rows = [['Kim', 'CS', 300], ['Lee', 'EE', 200], ['Ahn', 'CS', 450], ['Cho', 'EE', 100], ['Han', 'ME', 500]]
        animation = run({'kind': 'db_table',
                         'tables': [{'name': 'Emp', 'columns': ['name', 'dept', 'pay'], 'rows': rows}],
                         'ops': [{'op': 'group', 'table': 'Emp', 'by': 'dept', 'agg': 'sum', 'column': 'pay'},
                                 {'op': 'having', 'cmp': '>=', 'value': 500}]})
        sums = Counter()
        for _, dept, pay in rows:
            sums[dept] += pay
        grouped = animation.frames[-2]
        self.assertEqual(table_rows(grouped, '결과'), [[d, str(s)] for d, s in sums.items()])
        self.assertEqual(table_rows(animation.frames[-1], '결과'), [[d, str(s)] for d, s in sums.items() if s >= 500])
        # HAVING 캡션도 GROUP BY 단계처럼 집계 대상 열까지 적어 SQL 원문(SUM(pay))과 같게 읽히는지 확인
        self.assertIn('HAVING SUM(pay) >= 500', animation.frames[-1].caption)
        # 그룹으로 모은 단계에서는 같은 dept 행이 이웃해야 함
        depts = [r[1] for r in table_rows(animation.frames[1], 'Emp')]
        self.assertEqual(depts, ['CS', 'CS', 'EE', 'EE', 'ME'])

    # 검증 목적: COUNT는 열 없이 행 수를 세고, 문자열 열 합계·그룹 없는 HAVING은 실행 전에 거부되는지 확인
    def test_count_and_invalid_aggregates(self):
        animation = run({'kind': 'db_table', 'tables': [STUDENTS],
                         'ops': [{'op': 'group', 'table': 'Stu', 'by': 'dept', 'agg': 'count'}]})
        self.assertEqual(table_rows(animation.frames[-1], '결과'), [['CS', '2'], ['EE', '2']])
        with self.assertRaises(ValueError):
            run({'kind': 'db_table', 'tables': [STUDENTS],
                 'ops': [{'op': 'group', 'table': 'Stu', 'by': 'dept', 'agg': 'sum', 'column': 'name'}]})
        with self.assertRaises(ValueError):
            run({'kind': 'db_table', 'tables': [STUDENTS], 'ops': [{'op': 'having', 'cmp': '>', 'value': 1}]})
        with self.assertRaises(ValueError):
            run({'kind': 'db_table', 'tables': [STUDENTS],
                 'ops': [{'op': 'where', 'table': 'Stu', 'column': 'name', 'cmp': '<', 'value': 3}]})

    # 검증 목적: 분해(투영)가 중복 행을 없앤 새 표를 만들고, 그 표에서 결정자 열이 키가 되는지 확인
    def test_project_removes_duplicates(self):
        rows = [[1, 'CS', 'Kim'], [2, 'CS', 'Kim'], [3, 'EE', 'Lee']]
        animation = run({'kind': 'db_table', 'tables': [{'name': 'R', 'columns': ['id', 'dept', 'head'], 'rows': rows}],
                         'ops': [{'op': 'project', 'table': 'R', 'columns': ['dept', 'head'], 'name': 'R2'},
                                 {'op': 'check_key', 'table': 'R2', 'columns': ['dept']}]})
        self.assertEqual(table_rows(animation.frames[1], 'R2'), [['CS', 'Kim'], ['EE', 'Lee']])
        self.assertIn('1행', animation.frames[1].caption)
        self.assertIn('후보 키', animation.frames[2].caption)

    # 검증 목적: 표 정의 오류(행 길이, 없는 키 열, 초기 키 중복, 없는 표)와 그릴 수 없는 너비를 거부하는지 확인
    def test_invalid_tables_rejected(self):
        bad = [
            {'tables': [{'name': 'T', 'columns': ['a', 'b'], 'rows': [[1]]}]},
            {'tables': [{'name': 'T', 'columns': ['a'], 'key': ['z'], 'rows': []}]},
            {'tables': [{'name': 'T', 'columns': ['a'], 'key': ['a'], 'rows': [[1], [1]]}]},
            {'tables': [{'name': 'T', 'columns': ['a'], 'foreign': {'column': 'a', 'table': 'P'}, 'rows': []}]},
        ]
        for case in bad:
            with self.assertRaises(ValueError):
                run({'kind': 'db_table', **case, 'ops': [{'op': 'check_key', 'table': 'T', 'columns': ['a']}]})
        with self.assertRaises(ValueError):
            run({'kind': 'db_table', 'tables': [STUDENTS], 'ops': [{'op': 'check_key', 'table': 'X', 'columns': ['id']}]})
        wide = {'name': 'W', 'columns': ['a', 'b', 'c', 'd'], 'rows': [[1, 2, 3, 4]]}
        with self.assertRaises(ValueError):
            run({'kind': 'db_table', 'tables': [wide, {**wide, 'name': 'V'}],
                 'ops': [{'op': 'project', 'table': 'W', 'columns': ['a', 'b', 'c'], 'name': 'P'}]})


## @brief 고정점까지 함수 종속을 적용한 참조 속성 닫힘
def closure_reference(start, fds):
    result = set(start)
    changed = True
    while changed:
        changed = False
        for lhs, rhs in fds:
            if set(lhs) <= result and not set(rhs) <= result:
                result |= set(rhs)
                changed = True
    return result


class ClosureTests(unittest.TestCase):
    # 검증 목적: 단계마다 하나의 함수 종속만 적용하고, 마지막 닫힘이 참조 계산과 같은지 확인
    def test_closure_matches_reference(self):
        fds = [(['A'], ['B']), (['B'], ['C']), (['C', 'D'], ['E'])]
        animation = run({'kind': 'db_closure', 'attributes': list('ABCDE'), 'starts': [['A']],
                         'fds': [{'lhs': l, 'rhs': r} for l, r in fds]})
        final = {b.label for b in animation.frames[-1].boxes if b.id.startswith('c')}
        self.assertEqual(final, closure_reference(['A'], fds))
        self.assertEqual(len(animation.frames), 1 + 2 + 1)
        self.assertIn('키 아님', animation.frames[-1].caption)

    # 검증 목적: 전체 속성을 결정하는 시작 집합 중 최소인 것만 후보 키, 더 줄일 수 있으면 최소 아님으로 구분하는지 확인
    def test_candidate_key_and_superkey(self):
        fds = [{'lhs': ['A', 'B'], 'rhs': ['C']}, {'lhs': ['C'], 'rhs': ['D']}, {'lhs': ['D'], 'rhs': ['A']}]
        animation = run({'kind': 'db_closure', 'attributes': list('ABCD'), 'fds': fds, 'starts': [['A', 'B'], ['B', 'C', 'D']]})
        finals = [f.caption for f in animation.frames if '전체' in f.caption]
        self.assertEqual(len(finals), 2)
        self.assertIn('후보 키', finals[0])
        self.assertIn('최소 아님', finals[1])

    # 검증 목적: 속성 목록에 없는 이름을 쓴 함수 종속·시작 집합을 거부하는지 확인
    def test_unknown_attribute_rejected(self):
        with self.assertRaises(ValueError):
            run({'kind': 'db_closure', 'attributes': ['A', 'B'], 'fds': [{'lhs': ['A'], 'rhs': ['Z']}], 'starts': [['A']]})
        with self.assertRaises(ValueError):
            run({'kind': 'db_closure', 'attributes': ['A', 'B'], 'fds': [{'lhs': ['A'], 'rhs': ['B']}], 'starts': [['Q']]})


## @brief 결과 칸(x 8~10)을 (key, R값, S값) 튜플 목록으로 읽음
def join_output(frame):
    rows = {}
    for box in frame.boxes:
        if box.id.startswith('o'):
            rows.setdefault(box.y, {})[box.x] = box.label
    return sorted((int(r[8]), r[9], r[10]) for r in rows.values())


class JoinTests(unittest.TestCase):
    LEFT = [{'key': 3, 'val': 'a'}, {'key': 1, 'val': 'b'}, {'key': 2, 'val': 'c'}, {'key': 2, 'val': 'd'}]
    RIGHT = [{'key': 2, 'val': 'x'}, {'key': 3, 'val': 'y'}, {'key': 2, 'val': 'z'}]

    ## @brief 두 입력의 같은 key 쌍 전체(참조 동등 조인 결과)
    def reference(self):
        return sorted((r['key'], r['val'], s['val']) for r in self.LEFT for s in self.RIGHT if r['key'] == s['key'])

    # 검증 목적: 중첩 반복 조인이 참조 결과와 같고, 비교 횟수 |R|×|S|를 캡션에 보여 주는지 확인
    def test_nested_loop_matches_reference(self):
        animation = run({'kind': 'db_join', 'algorithm': 'nested_loop', 'left': self.LEFT, 'right': self.RIGHT})
        self.assertEqual(join_output(animation.frames[-1]), self.reference())
        self.assertIn('12번', animation.frames[-1].caption)

    # 검증 목적: 정렬-병합 조인이 먼저 정렬한 뒤 같은 key 묶음의 모든 조합(2×2)을 만드는지 확인
    def test_sort_merge_sorts_and_combines_groups(self):
        animation = run({'kind': 'db_join', 'algorithm': 'sort_merge', 'left': self.LEFT, 'right': self.RIGHT})
        self.assertEqual(join_output(animation.frames[-1]), self.reference())
        self.assertIn('정렬', animation.frames[1].caption)
        self.assertTrue(any('2행 × S 2행' in f.caption for f in animation.frames))

    # 검증 목적: 결과 칸 7행을 넘는 조인을 그리기 전에 거부하는지 확인
    def test_too_many_results_rejected(self):
        rows = [{'key': 1, 'val': v} for v in 'abc']
        with self.assertRaises(ValueError):
            run({'kind': 'db_join', 'algorithm': 'nested_loop', 'left': rows, 'right': rows})


## @brief 프레임의 B-트리 칸을 y(깊이)별로 모아 (깊이별 키 목록, 리프 키 정렬 순서)를 반환
def btree_levels(frame):
    levels = {}
    for box in frame.boxes:
        if box.id.startswith('k'):
            levels.setdefault(box.y, []).append((box.x, int(box.label)))
    return {y: [k for _, k in sorted(cells)] for y, cells in levels.items()}


class BTreeTests(unittest.TestCase):
    # 검증 목적: 키를 차례로 넣어 만든 B-트리의 리프가 모두 같은 깊이이고 정렬돼 있으며, 노드 크기가 상한을 지키는지 확인
    def test_built_tree_is_balanced_and_sorted(self):
        keys = [10, 20, 30, 40, 50, 60, 70]
        animation = run({'kind': 'db_btree', 'max_keys': 2, 'keys': keys, 'ops': [{'op': 'search', 'key': 50}]})
        levels = btree_levels(animation.frames[0])
        self.assertEqual(sorted(levels), [0, 2, 4])
        self.assertEqual(levels[4], [10, 30, 50, 70])
        self.assertEqual(sorted(sum(levels.values(), [])), keys)
        self.assertIn('찾음', animation.frames[-1].caption)
        self.assertIn('페이지 3개', animation.frames[-1].caption)

    # 검증 목적: 없는 키 검색은 리프까지 내려가 실패하고, 넘친 노드는 가운데 키를 올려 분할하며 루트 분할로 높이가 늘어나는지 확인
    def test_search_miss_and_splits(self):
        miss = run({'kind': 'db_btree', 'max_keys': 2, 'keys': [10, 20, 30], 'ops': [{'op': 'search', 'key': 25}]})
        self.assertIn('없음', miss.frames[-1].caption)
        grow = run({'kind': 'db_btree', 'max_keys': 2, 'keys': [10, 20, 30, 40],
                    'ops': [{'op': 'insert', 'key': 50}, {'op': 'insert', 'key': 60}, {'op': 'insert', 'key': 70}]})
        self.assertEqual(len(btree_levels(grow.frames[0])), 2)
        self.assertEqual(len(btree_levels(grow.frames[-1])), 3)
        self.assertTrue(any('루트 분할' in f.caption for f in grow.frames))
        self.assertEqual(sorted(sum(btree_levels(grow.frames[-1]).values(), [])), [10, 20, 30, 40, 50, 60, 70])

    # 검증 목적: 범위 검색이 범위와 겹치는 자식만 방문하고, 모은 키가 파이썬 필터 결과와 같은지 확인
    def test_range_collects_sorted_keys(self):
        keys = [10, 20, 30, 40, 50, 60, 70, 80]
        animation = run({'kind': 'db_btree', 'max_keys': 3, 'keys': keys, 'ops': [{'op': 'range', 'low': 25, 'high': 55}]})
        result = [int(b.label) for b in sorted((b for b in animation.frames[-1].boxes if b.id.startswith('q')),
                                               key=lambda b: b.x)]
        self.assertEqual(result, [k for k in keys if 25 <= k <= 55])
        self.assertEqual(len(animation.frames), 1 + 3 + 1)

    # 검증 목적: 중복 키, 낮은 끝이 높은 끝보다 큰 범위, 가로 12칸을 넘는 트리를 거부하는지 확인
    def test_invalid_btree_rejected(self):
        with self.assertRaises(ValueError):
            run({'kind': 'db_btree', 'max_keys': 2, 'keys': [1, 1], 'ops': [{'op': 'search', 'key': 1}]})
        with self.assertRaises(ValueError):
            run({'kind': 'db_btree', 'max_keys': 2, 'keys': [1], 'ops': [{'op': 'range', 'low': 5, 'high': 1}]})
        with self.assertRaises(ValueError):
            run({'kind': 'db_btree', 'max_keys': 2, 'keys': [1], 'ops': [{'op': 'insert', 'key': 1}]})
        with self.assertRaises(ValueError):
            run({'kind': 'db_btree', 'max_keys': 3, 'keys': list(range(0, 96, 8)), 'ops': [{'op': 'insert', 'key': 96}]})


class HashIndexTests(unittest.TestCase):
    # 검증 목적: 동등 검색은 버킷 하나의 체인만 비교하고, 범위 검색은 모든 버킷을 읽어 범위 안 키를 다 찾는지 확인
    def test_lookup_reads_one_bucket_and_range_reads_all(self):
        keys = [21, 22, 23, 24, 30, 13]
        animation = run({'kind': 'db_hash_index', 'buckets': 5, 'keys': keys,
                         'ops': [{'op': 'lookup', 'key': 13}, {'op': 'range', 'low': 21, 'high': 23}]})
        lookup = animation.frames[1]
        self.assertIn('h(13) = 3', lookup.caption)
        self.assertIn('2개 비교', lookup.caption)
        self.assertEqual(len(animation.frames), 1 + 1 + 5 + 1)
        found = sorted(int(b.label) for b in animation.frames[-1].boxes if b.tone == 'new')
        self.assertEqual(found, [21, 22, 23])
        self.assertIn('5개', animation.frames[-1].caption)

    # 검증 목적: 중복 키와 한 버킷 체인이 세로 칸을 넘는 입력을 거부하는지 확인
    def test_invalid_hash_index_rejected(self):
        with self.assertRaises(ValueError):
            run({'kind': 'db_hash_index', 'buckets': 3, 'keys': [1, 1], 'ops': [{'op': 'lookup', 'key': 1}]})
        with self.assertRaises(ValueError):
            run({'kind': 'db_hash_index', 'buckets': 3, 'keys': [0, 3, 6, 9, 12, 15, 18, 21],
                 'ops': [{'op': 'lookup', 'key': 1}]})


## @brief 프레임의 DB 열(x=1) 칸을 항목 이름 → 값으로 읽음
def committed(frame):
    return {b.id[2:]: int(b.label) for b in frame.boxes if b.id.startswith('db')}


class TransactionTests(unittest.TestCase):
    # 검증 목적: rollback은 DB 값을 바꾸지 않고, commit만 DB에 반영하며, 장애 뒤에는 커밋된 값만 남는지 확인
    def test_commit_rollback_and_crash(self):
        ops = [{'op': 'begin', 'tx': 'T1'}, {'op': 'write', 'tx': 'T1', 'item': 'A', 'value': 70},
               {'op': 'rollback', 'tx': 'T1'},
               {'op': 'begin', 'tx': 'T1'}, {'op': 'write', 'tx': 'T1', 'item': 'B', 'value': 80},
               {'op': 'commit', 'tx': 'T1'},
               {'op': 'begin', 'tx': 'T2'}, {'op': 'write', 'tx': 'T2', 'item': 'A', 'value': 0}, {'op': 'crash'}]
        animation = run({'kind': 'db_transaction', 'items': {'A': 100, 'B': 50}, 'ops': ops})
        self.assertEqual(committed(animation.frames[3]), {'A': 100, 'B': 50})
        self.assertIn('원자성', animation.frames[3].caption)
        self.assertEqual(committed(animation.frames[6]), {'A': 100, 'B': 80})
        self.assertEqual(committed(animation.frames[-1]), {'A': 100, 'B': 80})
        self.assertIn('지속성', animation.frames[-1].caption)

    # 검증 목적: commit은 트랜잭션 값을 removed로 그리지 않고(롤백과 구별), rollback만 removed로 그리는지 확인
    # - 커밋한 값이 빨간 점선으로 보이면 학생이 커밋을 취소로 오해하기 때문
    def test_commit_is_not_drawn_as_rollback(self):
        ops = [{'op': 'begin', 'tx': 'T1'}, {'op': 'write', 'tx': 'T1', 'item': 'A', 'value': 70}, {'op': 'commit', 'tx': 'T1'},
               {'op': 'begin', 'tx': 'T2'}, {'op': 'write', 'tx': 'T2', 'item': 'A', 'value': 5}, {'op': 'rollback', 'tx': 'T2'}]
        animation = run({'kind': 'db_transaction', 'items': {'A': 100}, 'ops': ops})
        commit, rollback = animation.frames[3], animation.frames[6]
        self.assertNotEqual(next(b.tone for b in commit.boxes if b.id == 'T1A'), 'removed')
        self.assertEqual(next(b.tone for b in commit.boxes if b.id == 'dbA'), 'new')
        self.assertEqual(next(b.tone for b in rollback.boxes if b.id == 'T2A'), 'removed')

    # 검증 목적: 쓰기 캡션이 "DB 값은 그대로"라는 일반 규칙이 아니라 다른 트랜잭션이 보는 커밋된 값이 그대로임을 말하는지 확인
    # - 실제 DBMS는 커밋 전에도 디스크에 쓸 수 있으므로(장애 시 되돌림) 지연 갱신 모델을 일반 규칙처럼 가르치지 않기 위함
    def test_write_caption_describes_committed_value(self):
        animation = run({'kind': 'db_transaction', 'items': {'A': 100},
                         'ops': [{'op': 'begin', 'tx': 'T1'}, {'op': 'write', 'tx': 'T1', 'item': 'A', 'value': 70}]})
        self.assertIn('다른 트랜잭션', animation.frames[-1].caption)
        self.assertNotIn('DB 값은 그대로', animation.frames[-1].caption)

    # 검증 목적: 세 번째 트랜잭션 T3를 별도 열(x=7)에 그려, 끝난 트랜잭션 이름을 다시 쓰지 않고 새 트랜잭션을 보여 줄 수 있는지 확인
    def test_third_transaction_has_own_column(self):
        ops = [{'op': 'begin', 'tx': 'T1'}, {'op': 'commit', 'tx': 'T1'}, {'op': 'begin', 'tx': 'T3'},
               {'op': 'read', 'tx': 'T3', 'item': 'A'}]
        animation = run({'kind': 'db_transaction', 'items': {'A': 100}, 'ops': ops})
        self.assertEqual(next(b.x for b in animation.frames[-1].boxes if b.id == 'T3A'), 7)

    # 검증 목적: 다른 트랜잭션은 커밋 전 값을 읽지 못하고 커밋 뒤에야 새 값을 읽는지 확인(격리성)
    def test_read_sees_only_committed_values(self):
        ops = [{'op': 'begin', 'tx': 'T1'}, {'op': 'write', 'tx': 'T1', 'item': 'A', 'value': 50},
               {'op': 'begin', 'tx': 'T2'}, {'op': 'read', 'tx': 'T2', 'item': 'A'}]
        animation = run({'kind': 'db_transaction', 'items': {'A': 100}, 'ops': ops})
        read = animation.frames[-1]
        self.assertEqual(next(b.label for b in read.boxes if b.id == 'T2A'), '100')
        self.assertIn('격리성', read.caption)
        # 커밋된 값만 읽는 방식(read committed·MVCC)이라는 가정을 캡션에 밝히는지 확인
        self.assertIn('커밋된 값만 읽는', read.caption)

    # 검증 목적: 시작하지 않은 트랜잭션의 연산, 이미 진행 중인 트랜잭션의 재시작, 커밋 전 같은 항목 쓰기 충돌을 거부하는지 확인
    def test_invalid_transactions_rejected(self):
        cases = [
            [{'op': 'write', 'tx': 'T1', 'item': 'A', 'value': 1}],
            [{'op': 'begin', 'tx': 'T1'}, {'op': 'begin', 'tx': 'T1'}],
            [{'op': 'begin', 'tx': 'T1'}, {'op': 'begin', 'tx': 'T2'}, {'op': 'write', 'tx': 'T1', 'item': 'A', 'value': 1},
             {'op': 'write', 'tx': 'T2', 'item': 'A', 'value': 2}],
            [{'op': 'begin', 'tx': 'T1'}, {'op': 'read', 'tx': 'T1', 'item': 'C'}],
        ]
        for ops in cases:
            with self.assertRaises(ValueError):
                run({'kind': 'db_transaction', 'items': {'A': 100}, 'ops': ops})


if __name__ == '__main__':
    unittest.main()
