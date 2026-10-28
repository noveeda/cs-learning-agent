## @file
# @brief 데이터베이스 콘텐츠의 실제 로더 연결·진단 범위·과목 경계와 단답 계산 정답을 검증
import json
import sqlite3
import unittest
from collections import Counter

from serving.animation import CONCEPT_SCENES, create_animation
from serving.concept_map import CONTENT_DIR, load_concept_map
from serving.item_bank import load_item_bank, normalize_short_answer


## @brief DB가 과목 목록·막힘 문항·기존 자료구조 장면과 함께 읽히는지 확인
class DatabaseContentTests(unittest.TestCase):
    ## @brief 저장소 콘텐츠를 실제 로더로 읽어 과목 등록 누락과 잘못된 문항 참조도 함께 드러냄
    @classmethod
    def setUpClass(cls):
        cls.map = load_concept_map()
        cls.concepts = {c.id: c for c in cls.map.concepts if c.subject == 'db'}
        cls.items = [item for item in load_item_bank() if item.concept in cls.concepts]

    ## @brief 과목 미등록·문항 누락으로 지도만 보이고 진단·보충을 시작하지 못하는 상황을 방지
    def test_database_has_complete_items_for_every_objective(self):
        self.assertIn(('db', '데이터베이스'), [(s.id, s.title) for s in self.map.subjects])
        self.assertTrue(5 <= len(self.concepts) <= 7)
        for concept, data in self.concepts.items():
            with self.subTest(concept=concept):
                own = [item for item in self.items if item.concept == concept]
                self.assertEqual(Counter(item.purpose for item in own),
                                 {'diagnosis': 2, 'check': 2, 'explanation': 1})
                for purpose in ('diagnosis', 'check'):
                    self.assertEqual({i.objective for i in own if i.purpose == purpose},
                                     set(range(len(data.objectives))))

    ## @brief 새 과목에서 기존 과목으로 내려가는 진단 경로를 제공하면서 기존 실험 경로는 보존
    def test_cross_subject_edges_only_point_from_database_to_existing_concepts(self):
        all_concepts = {c.id: c for c in self.map.concepts}
        boundary = [(c.id, p.id) for c in self.concepts.values() for p in c.prerequisites
                    if all_concepts[p.id].subject in ('dsa', 'os')]
        self.assertGreaterEqual(len(boundary), 2)
        for subject in ('dsa', 'os'):
            source = json.loads((CONTENT_DIR / subject / 'concept-map.json').read_text(encoding='utf-8'))
            for original in source['concepts']:
                actual = all_concepts[original['id']]
                self.assertEqual([p.model_dump() for p in actual.prerequisites], original['prerequisites'])
                self.assertFalse({p.id for p in actual.prerequisites} & self.concepts.keys())

    ## @brief 해시 인덱스 보충이 실제 버킷·충돌 탐색을 보여 주고 B-트리·조인을 다른 알고리즘으로 오인시키지 않음
    def test_only_hash_index_uses_a_matching_existing_scene(self):
        self.assertEqual(CONCEPT_SCENES.get('hash-index'), ('hash_table',))
        result = create_animation(json.dumps({'kind': 'hash_table', 'buckets': 5, 'ops': [
            {'op': 'insert', 'key': 12}, {'op': 'insert', 'key': 27}, {'op': 'search', 'key': 27}]}))
        self.assertTrue(result['ok'])
        self.assertGreater(len(result['animation']['frames']), 3)
        self.assertNotIn('btree-index', CONCEPT_SCENES)
        self.assertNotIn('join-algorithms', CONCEPT_SCENES)

    ## @brief 질문과 같은 입력의 SQL·조인·버킷 연산을 독립 실행해 손 계산 정답의 오기를 방지
    def test_short_answers_match_independent_calculation(self):
        short = {item.id: item for item in self.items if item.format == 'short_exact'}
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE Sale(shop TEXT, amount INTEGER)')
            db.executemany('INSERT INTO Sale VALUES (?, ?)', [('a', 4), ('a', 9), ('b', 7), ('b', 2), ('c', 8)])
            rows = db.execute('SELECT shop, SUM(amount) FROM Sale WHERE amount >= 4 GROUP BY shop HAVING SUM(amount) >= 8 ORDER BY shop').fetchall()
            sales = ','.join(f'{shop}:{total}' for shop, total in rows)
            db.execute('CREATE TABLE Visit(team TEXT, score INTEGER)')
            db.executemany('INSERT INTO Visit VALUES (?, ?)', [('x', 2), ('x', 5), ('y', 4), ('y', 8), ('z', 1)])
            count = db.execute('SELECT COUNT(*) FROM (SELECT team FROM Visit WHERE score >= 2 GROUP BY team HAVING COUNT(*) >= 2)').fetchone()[0]
        expected = {
            'relational-model-d1': 2 * 3,
            'relational-model-c1': 'studentid,courseid',
            'sql-query-d1': sales,
            'sql-query-c1': count,
            'normalization-d1': 'a,b,c',
            'normalization-c1': 'a,c',
            'btree-index-d1': 1 + 1 + 1,
            'btree-index-c1': 20 ** 2,
            'hash-index-d1': 27 % 5,
            'hash-index-c1': ','.join(str(k) for k in [14, 24, 34, 9] if k % 5 == 4),
            'join-algorithms-d1': sum(a == b for a in [2, 2, 5] for b in [2, 2, 2, 5]),
            'join-algorithms-c1': 4 * 3,
            'transaction-acid-d1': '70,30',
            'transaction-acid-c1': 45 - 12,
        }
        # 함수 종속을 반복 적용해 닫힘을 구함: A→B, B→C이면 A+={A,B,C}; A→B, C→D이면 AC+가 전체
        closure = {'a'}
        for left, right in [({'a'}, {'b'}), ({'b'}, {'c'})]:
            if left <= closure:
                closure |= right
        self.assertEqual(closure, {'a', 'b', 'c'})
        ac_closure = {'a', 'c'}
        for left, right in [({'a'}, {'b'}), ({'c'}, {'d'})]:
            if left <= ac_closure:
                ac_closure |= right
        self.assertEqual(ac_closure, {'a', 'b', 'c', 'd'})
        self.assertEqual(set(short), set(expected))
        for item_id, answer in expected.items():
            with self.subTest(item=item_id):
                self.assertEqual(short[item_id].answer, normalize_short_answer(str(answer)))


if __name__ == '__main__':
    unittest.main()
