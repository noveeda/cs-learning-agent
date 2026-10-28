## @file
# @brief 네트워크 콘텐츠의 문항 완비·과목 경계 방향·계산 정답을 실제 로더와 독립 계산으로 검증
import ipaddress
import json
import math
import re
import unittest
from collections import Counter
from itertools import permutations

from serving.concept_map import CONTENT_DIR, load_concept_map
from serving.item_bank import load_item_bank


NETWORK_IDS = {
    'network-layers', 'ip-subnet', 'routing', 'tcp-reliability',
    'flow-congestion-control', 'dns',
}


## @brief 네트워크 과목이 기존 진단 경로를 보존하면서 로드되고 학습 목표별 문항을 제공하는지 확인
class NetworkContentTests(unittest.TestCase):
    ## @brief 모델 서버 없이 실제 개념 지도·문항을 읽어 신규 데이터 누락을 검출
    @classmethod
    def setUpClass(cls):
        cls.map = load_concept_map()
        cls.concepts = {c.id: c for c in cls.map.concepts if c.subject == 'network'}
        cls.items = [i for i in load_item_bank() if i.concept in NETWORK_IDS]

    ## @brief 과목 순서 등록 누락과 신규 개념 누락으로 로드맵에서 과목이 사라지는 상황 방지
    def test_network_is_loaded_as_a_subject(self):
        self.assertIn(('network', '네트워크'), [(s.id, s.title) for s in self.map.subjects])
        self.assertEqual(set(self.concepts), NETWORK_IDS)

    ## @brief 개념마다 진단·확인·서술 문항이 있고 두 객관 채점 단계에서 모든 목표를 확인하는지 검증
    def test_each_concept_has_complete_items_and_objective_coverage(self):
        self.assertEqual({i.concept for i in self.items}, NETWORK_IDS)
        for cid, concept in self.concepts.items():
            own = [i for i in self.items if i.concept == cid]
            with self.subTest(concept=cid):
                self.assertEqual(Counter(i.purpose for i in own), {'diagnosis': 2, 'check': 2, 'explanation': 1})
                for purpose in ('diagnosis', 'check'):
                    self.assertEqual({i.objective for i in own if i.purpose == purpose},
                                     set(range(len(concept.objectives))))

    ## @brief 기존 과목의 선수 관계가 새 과목을 향하거나 기존 학생의 진단 경로가 바뀌는 상황 방지
    def test_old_prerequisites_are_preserved_and_cross_edges_point_outward(self):
        self.assertEqual(set(self.concepts), NETWORK_IDS)
        merged = {c.id: c for c in self.map.concepts}
        for subject in ('dsa', 'os'):
            originals = json.loads((CONTENT_DIR / subject / 'concept-map.json').read_text(encoding='utf-8'))['concepts']
            for concept in originals:
                self.assertEqual(merged[concept['id']].model_dump(exclude={'subject'}), concept)
                self.assertFalse(NETWORK_IDS.intersection(p['id'] for p in concept['prerequisites']))
        cross = {(c.id, p.id) for c in self.concepts.values() for p in c.prerequisites
                 if merged[p.id].subject in ('dsa', 'os')}
        self.assertGreaterEqual(len(cross), 2)
        self.assertTrue({('routing', 'shortest-path'), ('flow-congestion-control', 'queue'), ('dns', 'tree')} <= cross)

    ## @brief 선택지 위치만 외워 정답을 고르지 않도록 정답 위치의 편중 확인
    def test_choice_answer_positions_are_balanced(self):
        labels = Counter(i.answer for i in self.items if i.format == 'multiple_choice')
        self.assertEqual(set(labels), set('ABCD'))
        self.assertLessEqual(max(labels.values()), math.ceil(sum(labels.values()) / 4))

    ## @brief 단답 정답을 본문의 숫자·간선으로 다시 계산해 서브넷 경계와 TCP 바이트 단위 실수 검출
    def test_short_answers_match_independent_computation(self):
        short = {i.id: i for i in self.items if i.format == 'short_exact'}
        expected = {}
        for item_id, item in short.items():
            q = item.question
            if item_id == 'network-layers-c1':
                expected[item_id] = str(sum(map(int, re.findall(r'(\d+)바이트', q))))
            elif item_id == 'ip-subnet-d1':
                network = ipaddress.ip_network(re.search(r'\d+(?:\.\d+){3}/\d+', q).group(), strict=False)
                expected[item_id] = str(network.network_address)
            elif item_id == 'ip-subnet-c1':
                base, prefix = re.search(r'(\d+(?:\.\d+){3}/\d+).*?/(\d+)', q).groups()
                subnets = list(ipaddress.ip_network(base).subnets(new_prefix=int(prefix)))
                expected[item_id] = f'{len(subnets)},{subnets[0].num_addresses - 2}'
            elif item_id == 'ip-subnet-c2':
                network = ipaddress.ip_network(re.search(r'\d+(?:\.\d+){3}/\d+', q).group(), strict=False)
                expected[item_id] = f'{network.network_address},{network.broadcast_address}'
            elif item.concept == 'routing':
                edges = {(a.lower(), b.lower()): int(cost) for a, b, cost in re.findall(r'([A-Z])-([A-Z])=(\d+)', q)}
                edges.update({(b, a): cost for (a, b), cost in list(edges.items())})
                start, goal = (('a', 'd') if item_id.endswith('d1') else ('s', 't'))
                middle = sorted({n for edge in edges for n in edge} - {start, goal})
                routes = []
                # 중간 정점의 모든 단순 경로를 열거: 문항의 최단 경로를 정답 키와 독립적으로 산출
                for size in range(len(middle) + 1):
                    for order in permutations(middle, size):
                        path = (start, *order, goal)
                        pairs = list(zip(path, path[1:]))
                        if all(pair in edges for pair in pairs):
                            routes.append((sum(edges[pair] for pair in pairs), path))
                cost, path = min(routes)
                expected[item_id] = f'{path[1]},{cost}'
            elif item_id in ('tcp-reliability-d1', 'tcp-reliability-c2'):
                client, server = map(int, re.search(r'클라이언트 ISN=(\d+), 서버 ISN=(\d+)', q).groups())
                expected[item_id] = f'{server},{client + 1}' if item_id.endswith('d1') else f'{client + 1},{server + 1}'
            elif item_id == 'tcp-reliability-c1':
                seq, length = map(int, re.search(r'SEQ=(\d+)인 (\d+)바이트', q).groups())
                expected[item_id] = str(seq + length)
            elif item.concept == 'flow-congestion-control':
                rwnd, cwnd, flight = map(int, re.search(r'rwnd=(\d+), cwnd=(\d+), 미확인 데이터=(\d+)', q).groups())
                expected[item_id] = str(max(0, min(rwnd, cwnd) - flight))
            elif item.concept == 'dns':
                ttl, elapsed = map(int, re.search(r'TTL=(\d+)초.*?(\d+)초가', q).groups())
                expected[item_id] = str(max(0, ttl - elapsed))
        self.assertEqual(set(expected), set(short), '독립 계산에서 빠진 단답 문항')
        self.assertTrue(short, '과목 등록이 빠져 단답 검증 전체가 생략됨')
        for item_id, value in expected.items():
            self.assertEqual(short[item_id].answer, value, item_id)


if __name__ == '__main__':
    unittest.main()
