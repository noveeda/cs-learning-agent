## @file
# @brief 컴퓨터구조 콘텐츠의 로딩·문항 구성·계산 정답·기존 과목 경계 진단을 검증
# - 실제 로더와 막힘 엔진을 사용해 새 과목 누락 및 기존 학생의 선수 경로 변경을 방지
import json
import re
import unittest
from collections import Counter
from fractions import Fraction

from serving.concept_map import CONTENT_DIR, load_concept_map
from serving.item_bank import load_item_bank, normalize_short_answer
from test_stuck_flow import FakeGateway, Session

ARCH_SCOPE = {
    'binary-twos-complement', 'instruction-cycle', 'pipelining-hazards',
    'memory-hierarchy', 'cache-locality', 'virtual-memory-pages',
}
SHORT_IDS = {
    'binary-twos-complement-d1', 'binary-twos-complement-c1', 'binary-twos-complement-c2',
    'instruction-cycle-d2', 'instruction-cycle-c1', 'pipelining-hazards-d1', 'pipelining-hazards-c1',
    'memory-hierarchy-d2', 'memory-hierarchy-c1', 'cache-locality-d2', 'cache-locality-c1',
    'virtual-memory-pages-d1', 'virtual-memory-pages-c1',
}


## @brief 질문에 적힌 수치를 읽어 정답 키·rationale과 별개로 단답 정답을 계산
# - 질문의 숫자가 바뀌고 정답 키만 남는 오류도 감지하도록 본문에서 입력값을 추출
# - 캐시는 블록 주소와 라인 번호를 직접 추적해 적중·미스를 재현
# - 평균 접근 시간은 Fraction으로 계산해 부동소수점 반올림을 정답에 섞지 않음
def recompute(item):
    question, item_id = item.question, item.id
    if item_id == 'binary-twos-complement-d1':
        bits = re.search(r'비트열 ([01]{8})', question)[1]
        return str(int.from_bytes(bytes([int(bits, 2)]), signed=True))
    if item_id == 'binary-twos-complement-c1':
        value = int(re.search(r'10진수 (-?\d+)', question)[1])
        return format(int.from_bytes(value.to_bytes(1, signed=True)), '08b')
    if item_id == 'binary-twos-complement-c2':
        bits = re.findall(r'(?<![01])[01]{8}(?![01])', question)
        values = [int.from_bytes(bytes([int(bit, 2)]), signed=True) for bit in bits]
        total = sum(values)
        return f'{total & 255:08b},{int(not -128 <= total <= 127)}'
    if item_id.startswith('instruction-cycle-'):
        start = int(re.search(r'PC=(\d+)', question)[1])
        width = int(re.search(r'길이는 (\d+)바이트', question)[1])
        count = int(re.search(r'명령어 (\d+)개', question)[1])
        return str(start + width * count)
    if item_id.startswith('pipelining-hazards-'):
        stages = int(re.search(r'(\d+)단계 파이프라인', question)[1])
        count = int(re.search(r'명령어 (\d+)개', question)[1])
        stalls = int(re.search(r'추가 정지 (\d+)사이클', question)[1])
        return str(stages + count - 1 + stalls)
    if item_id.startswith('memory-hierarchy-'):
        hit = int(re.search(r'적중 시간 (\d+)ns', question)[1])
        rate = Fraction(int(re.search(r'미스율 (\d+)%', question)[1]), 100)
        penalty = int(re.search(r'추가 미스 페널티 (\d+)ns', question)[1])
        return str(hit + rate * penalty)
    if item_id.startswith('cache-locality-'):
        block_size = int(re.search(r'블록 크기 (\d+)바이트', question)[1])
        line_count = int(re.search(r'캐시 라인 (\d+)개', question)[1])
        addresses = [int(value) for value in re.search(r'주소 열: ([\d, ]+)\.', question)[1].split(',')]
        cache, hits, misses = {}, 0, 0
        for address in addresses:
            block = address // block_size
            line = block % line_count
            if cache.get(line) == block:
                hits += 1
            else:
                misses += 1
                cache[line] = block
        return f'{misses},{hits}'
    if item_id.startswith('virtual-memory-pages-'):
        size = int(re.search(r'페이지 크기 (\d+)바이트', question)[1])
        address = int(re.search(r'가상 주소 (\d+)', question)[1])
        page, offset = divmod(address, size)
        if item_id.endswith('-d1'):
            return f'{page},{offset}'
        mappings = {int(page): int(frame) for page, frame in re.findall(r'페이지 (\d+) → 프레임 (\d+)', question)}
        return str(mappings[page] * size + offset)
    raise AssertionError(f'독립 계산이 없는 단답 문항: {item_id}')


## @brief 새 과목이 실제 진단 경로에 포함되면서 기존 선수 관계와 문항 채점 계약을 보존하는지 검증
class ArchContentTests(unittest.TestCase):
    ## @brief 테스트마다 같은 실제 지도·문항을 사용하되 모델 서버와 운영 서비스는 호출하지 않음
    @classmethod
    def setUpClass(cls):
        cls.concept_map = load_concept_map()
        cls.items = [item for item in load_item_bank() if item.concept in ARCH_SCOPE]
        cls.concepts = {concept.id: concept for concept in cls.concept_map.concepts if concept.subject == 'arch'}

    ## @brief SUBJECT_ORDER에서 arch가 빠져 새 과목을 읽지 못하거나 잘못된 과목 이름을 노출하는 오류 방지
    def test_loader_exposes_arch_subject_and_six_concepts(self):
        self.assertEqual(set(self.concepts), ARCH_SCOPE)
        self.assertIn(('arch', '컴퓨터구조'), [(s.id, s.title) for s in self.concept_map.subjects])
        for concept in self.concepts.values():
            self.assertTrue(1 <= len(concept.objectives) <= 3, concept.id)

    ## @brief 새→기존 경계 연결을 유지하고 기존 자료구조·운영체제 개념의 직접 선수 관계가 바뀌는 오류 방지
    def test_cross_subject_edges_preserve_existing_prerequisites(self):
        self.assertEqual(set(self.concepts), ARCH_SCOPE)
        merged = {concept.id: concept for concept in self.concept_map.concepts}
        boundary = {(concept.id, prerequisite.id) for concept in self.concepts.values()
                    for prerequisite in concept.prerequisites if merged[prerequisite.id].subject in ('dsa', 'os')}
        self.assertGreaterEqual(len(boundary), 2)
        self.assertIn(('cache-locality', 'array'), boundary)
        self.assertIn(('virtual-memory-pages', 'memory-address'), boundary)
        for subject in ('dsa', 'os'):
            source = json.loads((CONTENT_DIR / subject / 'concept-map.json').read_text(encoding='utf-8'))
            for concept in source['concepts']:
                self.assertEqual([p.id for p in merged[concept['id']].prerequisites],
                                 [p['id'] for p in concept['prerequisites']], concept['id'])
                self.assertTrue(all(p['id'] not in ARCH_SCOPE for p in concept['prerequisites']), concept['id'])

    ## @brief 어느 개념에도 진단·확인·서술 문항이나 학습 목표 검증이 빠지지 않도록 검사
    def test_each_concept_has_full_set_and_objective_coverage(self):
        self.assertEqual({item.concept for item in self.items}, ARCH_SCOPE)
        for concept_id, concept in self.concepts.items():
            own = [item for item in self.items if item.concept == concept_id]
            self.assertEqual(Counter(item.purpose for item in own), {'diagnosis': 2, 'check': 2, 'explanation': 1})
            for purpose in ('diagnosis', 'check'):
                self.assertEqual({item.objective for item in own if item.purpose == purpose},
                                 set(range(len(concept.objectives))), (concept_id, purpose))
            self.assertTrue(all(2 <= len(item.rubric) <= 4 for item in own if item.purpose == 'explanation'))

    ## @brief 선택형 정답 위치가 한쪽으로 몰려 위치만으로 답을 추측하는 단서가 되는 오류 방지
    def test_multiple_choice_keys_are_balanced(self):
        counts = Counter(item.answer for item in self.items if item.format == 'multiple_choice')
        self.assertEqual(set(counts), set('ABCD'))
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)

    ## @brief 수치·가정 변경 뒤 단답 정답 키를 고치지 않는 오류를 본문 기반 재계산으로 탐지
    def test_short_answers_match_independent_question_computation(self):
        short = {item.id: item for item in self.items if item.format == 'short_exact'}
        self.assertEqual(set(short), SHORT_IDS)
        for item in short.values():
            with self.subTest(item=item.id):
                self.assertEqual(item.answer, normalize_short_answer(recompute(item)))

    ## @brief 캐시에서 막힌 학생의 배열 빈틈을 실제 진단이 찾고 배열→캐시 순으로 보충하는지 확인
    def test_cache_diagnosis_can_find_existing_array_gap(self):
        self.assertIn('cache-locality', self.concepts)
        session = Session(self, gateway=FakeGateway(scene=None), concept_map=self.concept_map,
                          items=load_item_bank(), entry={'kind': 'roadmap', 'concept': 'cache-locality',
                                                       'return_to': '/learn?concept=cache-locality'})
        known = {concept.id for concept in self.concept_map.concepts} - {'array', 'cache-locality'}
        session.diagnose(known=known)
        self.assertIn(('array', 'gap'), [(probe.concept, probe.result) for probe in session.state.probes])
        self.assertEqual([fix.concept for fix in session.state.fixes], ['array', 'cache-locality'])


if __name__ == '__main__':
    unittest.main()
