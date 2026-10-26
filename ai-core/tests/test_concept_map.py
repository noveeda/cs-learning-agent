# 파일 역할: 자료구조 개념 지도 데이터와, 진단 탐색이 의존하는 그래프 규칙의 거부 동작을 검증
import unittest

from pydantic import ValidationError

from serving.concept_map import CONTENT_DIR, ConceptMap, load_concept_map


# 역할: 규칙 검사용 최소 지도 생성; edges는 {개념: [선수 개념]}
def build(edges, objective='예시를 설명할 수 있다.'):
    return {'id': 'test', 'title': '테스트', 'version': 1, 'status': 'draft', 'concepts': [
        {'id': cid, 'title': cid, 'description': cid, 'objectives': [objective],
         'prerequisites': [{'id': p, 'reason': '근거'} for p in prerequisites]}
        for cid, prerequisites in edges.items()]}


class DsaConceptMapTests(unittest.TestCase):
    # 검증 목적: 저장소의 자료구조 지도가 모든 규칙을 통과하고 합의한 규모(25~40개)인지 확인
    # - 인자 없이 읽으면 모든 과목을 합친 지도가 되므로, 자료구조 지도 파일만 지정해 읽음
    def test_dsa_map_is_valid_and_in_agreed_size(self):
        concept_map = load_concept_map(CONTENT_DIR / 'dsa' / 'concept-map.json')
        self.assertEqual(concept_map.id, 'dsa')
        self.assertTrue(25 <= len(concept_map.concepts) <= 40)

    # 검증 목적: 검수 전 초안이 검수 완료로 표시되지 않도록 상태 값을 제한
    def test_status_is_draft_or_reviewed(self):
        self.assertIn(load_concept_map().status, ('draft', 'reviewed'))


class GraphRuleTests(unittest.TestCase):
    # 검증 목적: 올바른 사슬 구조는 통과해 규칙이 정상 지도를 막지 않는지 확인
    def test_accepts_simple_chain(self):
        ConceptMap.model_validate(build({'base': [], 'mid': ['base'], 'top': ['mid']}))

    # 검증 목적: 순환이 있으면 아래부터 보충할 순서가 없으므로 거부
    def test_rejects_cycle(self):
        with self.assertRaisesRegex(ValidationError, '순환'):
            ConceptMap.model_validate(build({'base': ['top'], 'mid': ['base'], 'top': ['mid']}))

    # 검증 목적: 간접 관계를 직접 관계로 중복해 두면 가지치기가 어긋나므로 거부
    def test_rejects_redundant_indirect_edge(self):
        with self.assertRaisesRegex(ValidationError, '간접 관계'):
            ConceptMap.model_validate(build({'base': [], 'mid': ['base'], 'top': ['mid', 'base']}))

    # 검증 목적: 없는 개념을 가리키는 선수 관계 거부
    def test_rejects_unknown_prerequisite(self):
        with self.assertRaisesRegex(ValidationError, '없는 선수 개념'):
            ConceptMap.model_validate(build({'base': [], 'mid': ['missing']}))

    # 검증 목적: 어떤 관계에도 없는 개념은 진단에서 찾을 수 없으므로 거부
    def test_rejects_isolated_concept(self):
        with self.assertRaisesRegex(ValidationError, '선수 관계가 전혀 없는'):
            ConceptMap.model_validate(build({'base': [], 'mid': ['base'], 'lonely': []}))

    # 검증 목적: 개념 ID 중복과 자기 자신을 선수로 두는 경우 거부
    def test_rejects_duplicate_and_self_reference(self):
        duplicate = build({'base': [], 'mid': ['base']})
        duplicate['concepts'].append(dict(duplicate['concepts'][0]))
        with self.assertRaisesRegex(ValidationError, '중복'):
            ConceptMap.model_validate(duplicate)
        with self.assertRaisesRegex(ValidationError, '자기 자신'):
            ConceptMap.model_validate(build({'base': ['base'], 'mid': ['base']}))

    # 검증 목적: 확인 문항을 붙일 수 없는 모호한 학습 목표("~를 안다") 거부
    def test_rejects_unmeasurable_objective(self):
        with self.assertRaisesRegex(ValidationError, '할 수 있다'):
            ConceptMap.model_validate(build({'base': [], 'mid': ['base']}, objective='개념을 안다.'))


if __name__ == '__main__':
    unittest.main()
