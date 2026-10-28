## @file
# @brief 교재 절↔개념 대응표(content/rag/section-map.json)가 개념 지도·책 목록과 맞고 빈칸 없이 61개념을 덮는지 검증
# - 대응표는 사람이 검수하는 데이터라, 틀린 개념 ID·책 이름·중복 줄이 섞이면 색인(Task 3)이 엉뚱한 문단을 고름
import json
import tempfile
import unittest
from pathlib import Path

from rag.fetch import DATA_DIR
from rag.parse import Section
from rag.section_map import MAX_SECTIONS_PER_CONCEPT, load_section_map, write_review
from rag.sources import BOOKS
from serving.concept_map import load_concept_map


class SectionMapTests(unittest.TestCase):
    def setUp(self):
        self.section_map = load_section_map()
        self.concept_ids = {c.id for c in load_concept_map().concepts}

    # 검증 목적: 오타 난 개념 ID가 들어가면 그 절이 어떤 개념 검색에도 걸리지 않으므로, 합친 지도의 ID만 허용
    def test_concepts_exist_in_concept_map(self):
        used = {c for e in self.section_map.entries for c in e.concepts}
        used |= {g.concept for g in self.section_map.gaps}
        self.assertEqual(used - self.concept_ids, set())

    # 검증 목적: 출처 표기(TASL)는 BOOKS 표에서 오므로, 표에 없는 책 이름이면 답변에 출처를 붙일 수 없음
    def test_books_known(self):
        self.assertEqual({e.book for e in self.section_map.entries} - set(BOOKS), set())

    # 검증 목적: 근거가 없는 개념은 빈칸으로 명시해야 하네스가 예외 대신 근거 없음 문구를 낼 수 있음
    def test_every_concept_covered_or_gap(self):
        gaps = {g.concept for g in self.section_map.gaps}
        uncovered = {c for c in self.concept_ids if not self.section_map.sections_for(c) and c not in gaps}
        self.assertEqual(uncovered, set())
        # 빈칸이라고 적고 절도 붙이면 어느 쪽이 맞는지 알 수 없으므로 둘 중 하나만 허용
        self.assertEqual({c for c in gaps if self.section_map.sections_for(c)}, set())

    # 검증 목적: 같은 절이 두 줄로 들어가면 개념 꼬리표가 둘로 갈라져 검수와 색인이 어긋남
    def test_no_duplicate_entries(self):
        keys = [(e.book, e.section_id) for e in self.section_map.entries]
        self.assertEqual(len(keys), len(set(keys)))

    # 검증 목적: 공개 교재에 다루는 절이 없다고 확인한 개념(조인 알고리즘)이 빈칸 목록에 남아 있는지 확인
    def test_gaps_include_known_blanks(self):
        self.assertIn('join-algorithms', {g.concept for g in self.section_map.gaps})

    # 검증 목적: 한 개념에 절이 너무 많으면 핵심 절이 묻히고 사람 검수도 어려워지므로 상한을 지킴
    def test_sections_per_concept_capped(self):
        over = {c: len(self.section_map.sections_for(c)) for c in self.concept_ids
                if len(self.section_map.sections_for(c)) > MAX_SECTIONS_PER_CONCEPT}
        self.assertEqual(over, {})

    # 검증 목적: 교재를 다시 분해해 절 번호가 바뀌면 대응표가 없는 절을 가리키게 되므로, 받은 절 목록이 있을 때 대조
    # - sections.jsonl은 Git 밖 데이터라 CI에는 없음. 있을 때만 검사함
    @unittest.skipUnless((DATA_DIR / 'sections.jsonl').exists(), '절 목록(sections.jsonl)이 없음')
    def test_entries_point_to_existing_sections(self):
        lines = (DATA_DIR / 'sections.jsonl').read_text(encoding='utf-8').splitlines()
        known = {(r['book'], r['section_id']) for r in map(json.loads, lines)}
        # 아직 받지 않은 책(다른 PR에서 추가 중인 책)은 대조하지 않음
        fetched = {book for book, _ in known}
        missing = [(e.book, e.section_id) for e in self.section_map.entries
                   if e.book in fetched and (e.book, e.section_id) not in known]
        self.assertEqual(missing, [])


class LookupTests(unittest.TestCase):
    # 검증 목적: 색인은 절에서 개념으로, 하네스는 개념에서 절로 찾으므로 두 방향 조회가 같은 표에서 나와야 함
    def test_lookup_both_directions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'map.json'
            path.write_text(json.dumps({'version': 1, 'entries': [
                {'book': 'ods', 'section_id': 'a#1', 'concepts': ['stack', 'queue']},
                {'book': 'ods', 'section_id': 'a#2', 'concepts': ['stack']},
            ], 'gaps': [{'concept': 'join-algorithms', 'reason': '공개 교재 없음'}]}), encoding='utf-8')
            section_map = load_section_map(path)
        self.assertEqual(section_map.concepts_for('ods', 'a#1'), ('stack', 'queue'))
        self.assertEqual(section_map.concepts_for('ods', 'zzz'), ())
        self.assertEqual(section_map.sections_for('stack'), (('ods', 'a#1'), ('ods', 'a#2')))
        self.assertEqual(section_map.sections_for('join-algorithms'), ())


class ReviewSheetTests(unittest.TestCase):
    # 검증 목적: 사용자는 검수 시트만 읽으므로 요약표·빈칸·개념별 한국어 제목·절 첫 200자가 모두 시트에 있어야 함
    def test_review_sheet_lists_summary_gaps_and_section_heads(self):
        with tempfile.TemporaryDirectory() as tmp:
            map_path, out = Path(tmp) / 'map.json', Path(tmp) / 'review.md'
            map_path.write_text(json.dumps({'version': 1, 'entries': [
                {'book': 'ods', 'section_id': 'a#1.2', 'concepts': ['stack']},
            ], 'gaps': [{'concept': 'join-algorithms', 'reason': '공개 교재 없음'}]}), encoding='utf-8')
            sections = {('ods', 'a#1.2'): Section('ods', 'a#1.2', 'Stacks | Queues', 'x' * 300, ())}
            write_review(load_section_map(map_path), sections, out)
            sheet = out.read_text(encoding='utf-8')
        self.assertIn('| 스택 | stack | 1 |', sheet)
        self.assertIn('조인 알고리즘', sheet)
        self.assertIn('공개 교재 없음', sheet)
        self.assertIn('## 스택 (stack)', sheet)
        # 표 칸 구분자(|)가 제목에 있으면 표가 깨지므로 이스케이프하고, 본문은 200자에서 자름
        self.assertIn('Stacks \\| Queues', sheet)
        self.assertIn('x' * 200 + '…', sheet)
        self.assertNotIn('x' * 201, sheet)


if __name__ == '__main__':
    unittest.main()
