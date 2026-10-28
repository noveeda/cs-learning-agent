## @file
# @brief 여러 과목 지도·문항을 하나의 연결 지도로 합치는 로더와, 합친 지도 기준의 그래프 규칙 검증을 확인
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from serving import concept_map as concept_map_module
from serving.concept_map import CONTENT_DIR, load_concept_map
from serving.item_bank import load_item_bank


## @brief 과목 지도 파일 내용 생성; edges는 {개념: [선수 개념]}, extra는 개념마다 덧붙일 필드
def subject_map(subject, title, edges, status='reviewed', extra=None):
    return {'id': subject, 'title': title, 'version': 1, 'status': status, 'concepts': [
        {'id': cid, 'title': cid, 'description': cid, 'objectives': ['예시를 설명할 수 있다.'],
         'prerequisites': [{'id': p, 'reason': '근거'} for p in prerequisites], **(extra or {})}
        for cid, prerequisites in edges.items()]}


## @brief 문항 한 줄 생성; 로더가 과목 폴더마다 items-*.jsonl을 읽는지 확인하는 용도
def check_item(concept):
    return {'id': f'{concept}-c1', 'concept': concept, 'purpose': 'check', 'objective': 0,
            'format': 'short_exact', 'question': '질문', 'answer': '1', 'rationale': '근거'}


class MultiSubjectLoaderTests(unittest.TestCase):
    ## @brief 임시 content 폴더를 만들고 로더의 CONTENT_DIR을 그쪽으로 돌림
    # - 기본 지도: dsa 2개념(queue ← priority-queue), os 1개념(round-robin → queue, 과목 사이 선수 관계)
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patcher = patch.object(concept_map_module, 'CONTENT_DIR', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.write('dsa', subject_map('dsa', '자료구조·알고리즘', {'queue': [], 'priority-queue': ['queue']}))
        self.write('os', subject_map('os', '운영체제', {'round-robin': ['queue']}))

    def write(self, subject, data, items=()):
        folder = self.root / subject
        folder.mkdir(exist_ok=True)
        (folder / 'concept-map.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        if items:
            lines = '\n'.join(json.dumps(item, ensure_ascii=False) for item in items)
            (folder / 'items-test.jsonl').write_text(lines, encoding='utf-8')

    # 검증 목적: 과목 지도를 합치면 개념마다 과목이 붙고, 운영체제 개념의 선수 ID가 자료구조 개념을 그대로 가리키는지 확인
    # - 운영체제 지도만 따로 검증하면 queue가 "없는 선수 개념"이 되므로, 합친 지도 기준으로 검증해야 통과함
    def test_merges_subjects_into_one_map(self):
        merged = load_concept_map()
        subjects = {c.id: c.subject for c in merged.concepts}
        self.assertEqual(subjects, {'queue': 'dsa', 'priority-queue': 'dsa', 'round-robin': 'os'})
        rr = next(c for c in merged.concepts if c.id == 'round-robin')
        self.assertEqual([p.id for p in rr.prerequisites], ['queue'])
        self.assertEqual([(s.id, s.title) for s in merged.subjects], [('dsa', '자료구조·알고리즘'), ('os', '운영체제')])
        self.assertEqual((merged.id, merged.title, merged.version, merged.status), ('cs', 'CS 전공', 2, 'reviewed'))

    # 검증 목적: 한 과목이라도 검수 전(draft)이면 합친 지도도 draft로 보아 막힘 엔진이 검수 완료로 오인하지 않게 함
    def test_merged_status_is_reviewed_only_when_all_reviewed(self):
        self.write('os', subject_map('os', '운영체제', {'round-robin': ['queue']}, status='draft'))
        self.assertEqual(load_concept_map().status, 'draft')

    # 검증 목적: 존재하는 과목 폴더만 읽어, 운영체제 지도가 아직 없을 때도 자료구조 지도만으로 동작하는지 확인
    def test_reads_only_existing_subject_folders(self):
        (self.root / 'os' / 'concept-map.json').unlink()
        (self.root / 'os').rmdir()
        merged = load_concept_map()
        self.assertEqual([s.id for s in merged.subjects], ['dsa'])
        self.assertEqual(len(merged.concepts), 2)

    # 검증 목적: 다른 과목을 거쳐 이미 이어지는 관계를 직접 두면 거부(Review Focus 1)
    # - sjf → process-state → priority-queue → queue 경로가 있는데 sjf → queue를 직접 두는 경우
    def test_rejects_indirect_edge_through_other_subject(self):
        self.write('os', subject_map('os', '운영체제', {
            'process-state': ['priority-queue'], 'sjf': ['process-state', 'queue']}))
        with self.assertRaisesRegex(ValueError, 'sjf → queue는 다른 선수 개념을 거쳐'):
            load_concept_map()

    # 검증 목적: 과목은 로더가 폴더 이름으로 채우는 값이므로, 지도 파일에 적힌 subject는 거부해 출처가 둘이 되지 않게 함
    def test_rejects_subject_written_in_file(self):
        self.write('os', subject_map('os', '운영체제', {'round-robin': ['queue']}, extra={'subject': 'os'}))
        with self.assertRaisesRegex(ValueError, 'subject'):
            load_concept_map()

    # 검증 목적: 과목이 달라도 개념 ID가 겹치면 선수 관계가 어느 개념을 가리키는지 모호하므로 거부
    def test_rejects_duplicate_concept_id_across_subjects(self):
        self.write('os', subject_map('os', '운영체제', {'queue': [], 'round-robin': ['queue']}))
        with self.assertRaisesRegex(ValueError, '개념 ID가 중복'):
            load_concept_map()

    # 검증 목적: 인자 없이 부르면 모든 과목 폴더의 문항을 읽어 합친 지도에 맞물리는지 확인
    def test_item_bank_reads_all_subject_folders(self):
        self.write('dsa', subject_map('dsa', '자료구조·알고리즘', {'queue': [], 'priority-queue': ['queue']}),
                   items=[check_item('queue')])
        self.write('os', subject_map('os', '운영체제', {'round-robin': ['queue']}), items=[check_item('round-robin')])
        self.assertEqual(sorted(i.id for i in load_item_bank()), ['queue-c1', 'round-robin-c1'])


class RepositorySubjectTests(unittest.TestCase):
    # 검증 목적: 저장소의 실제 지도(현재 자료구조만)를 인자 없이 읽어도 기존 36개 개념이 그대로이고 모두 dsa로 표시되는지 확인
    def test_repository_map_marks_dsa_subject(self):
        merged = load_concept_map()
        dsa = [c for c in merged.concepts if c.subject == 'dsa']
        self.assertEqual(len(dsa), 36)
        self.assertEqual([c.id for c in dsa], [c.id for c in load_concept_map(CONTENT_DIR / 'dsa' / 'concept-map.json').concepts])
        self.assertIn('dsa', [s.id for s in merged.subjects])


if __name__ == '__main__':
    unittest.main()
