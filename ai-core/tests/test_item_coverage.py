## @file
# @brief 개념 지도의 모든 개념이 막힘 흐름에 필요한 문항 구성을 갖췄는지, 실제 지도·문항 은행으로 흐름이 끝까지 가는지 검사
# - 문항이 빠지거나 개념이 새로 생겼는데 문항이 없으면 CI에서 막기 위함(설계 MVP 2 "36개 개념 E2E"의 자동 부분)
import unittest
from collections import Counter

from serving.concept_map import load_concept_map
from serving.item_bank import load_item_bank
from test_stuck_flow import FakeGateway, Session

# 막힘 흐름이 개념마다 쓰는 문항 수의 하한
# - diagnosis 2: 선수 개념 진단(S3)에서 묻는 문항
# - check 2: 보충 뒤 확인(S5)과 재보충 때 다른 문항으로 바꿔 묻기 위함
# - explanation 1: 혼합형 확인의 서술 질문
REQUIRED = {'diagnosis': 2, 'check': 2, 'explanation': 1}
# 한 세션의 요청 수 상한; 흐름이 같은 단계를 맴돌면 무한 반복 대신 실패로 끝내기 위함
MAX_STEPS = 200


## @brief 개념마다 목적별로 모자란 문항 수를 반환; 모두 갖췄으면 빈 dict
def shortfalls(concept_map, items):
    counts = Counter((i.concept, i.purpose) for i in items)
    found = {}
    for concept in concept_map.concepts:
        short = {p: n - counts[(concept.id, p)] for p, n in REQUIRED.items() if counts[(concept.id, p)] < n}
        if short:
            found[concept.id] = short
    return found


## @brief concept에서 로드맵으로 막힌 세션을 끝까지 진행하고, 완주를 막은 문제를 목록으로 반환(빈 목록이면 완주)
# - 진단은 정답 키로 모두 맞히고, 확인 문항도 정답 키로, 서술은 가짜 채점기가 통과시킴
# - 장면 선택은 끄고(scene=None) 문항 구성과 단계 전환만 봄; 애니메이션은 test_stuck_flow가 따로 검사함
def flow_problems(test, concept_map, items, concept):
    session = Session(test, gateway=FakeGateway(scene=None), concept_map=concept_map, items=items,
                      entry={'kind': 'roadmap', 'concept': concept, 'return_to': f'/learn?concept={concept}'})
    problems = []
    for _ in range(MAX_STEPS):
        state = session.state
        if 'note' in state.view:
            problems.append(f'보충 화면 안내: {state.view["note"]}')
        if state.stage in ('complete', 'out_of_scope'):
            break
        if state.stage in ('diagnose', 'check'):
            session.send({'type': 'answer', 'item': state.pending, 'answer': session.key(state.pending)})
        elif state.stage == 'explain':
            session.send({'type': 'answer', 'item': state.pending, 'answer': '자기 말 설명'})
        else:
            session.send({'type': 'continue'})
    if session.state.stage != 'complete':
        problems.append(f'완주하지 못함: stage={session.state.stage}')
    problems += [f'{f.concept} 결과 {f.outcome}' for f in session.state.fixes if f.outcome != 'passed']
    return problems


class ItemCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.map, cls.items = load_concept_map(), load_item_bank()

    # 검증 목적: 개념 지도의 모든 개념이 진단 2·확인 2·서술 1 이상을 갖춤; 실패하면 모자란 개념과 수를 그대로 보여 줌
    def test_every_concept_has_items(self):
        self.assertEqual(shortfalls(self.map, self.items), {})

    # 검증 목적: 위 검사가 문항 하나가 빠지거나 개념 하나의 문항이 모두 빠진 은행을 실제로 잡아내는지 확인
    def test_shortfalls_catch_missing_items(self):
        one_missing = [i for i in self.items if i.id != 'stack-d1']
        self.assertEqual(shortfalls(self.map, one_missing), {'stack': {'diagnosis': 1}})
        no_heap = [i for i in self.items if i.concept != 'heap']
        self.assertEqual(shortfalls(self.map, no_heap), {'heap': {'diagnosis': 2, 'check': 2, 'explanation': 1}})

    # 검증 목적: 모든 개념에서 로드맵 진입 막힘 흐름이 complete에 도달하고, 보충 대상이 모두 통과하며, "확인 문항 없음" 안내가 한 번도 나오지 않음
    def test_flow_completes_for_every_concept(self):
        for concept in self.map.concepts:
            with self.subTest(concept=concept.id):
                self.assertEqual(flow_problems(self, self.map, self.items, concept.id), [])

    # 검증 목적: 위 흐름 검사가 확인 문항이 빠진 개념과 문항이 모두 빠진 개념을 실제로 잡아내는지 확인
    def test_flow_problems_catch_missing_items(self):
        no_checks = [i for i in self.items if not (i.concept == 'heap' and i.purpose == 'check')]
        self.assertIn('보충 화면 안내: 확인 문항이 아직 없어 이해 확인을 건너뜁니다.', flow_problems(self, self.map, no_checks, 'heap'))
        no_heap = [i for i in self.items if i.concept != 'heap']
        self.assertIn('heap 결과 unchecked', flow_problems(self, self.map, no_heap, 'heap'))


if __name__ == '__main__':
    unittest.main()
