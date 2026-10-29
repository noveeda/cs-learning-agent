## @file
# @brief 네트워크 장면(계층·서브넷·라우팅·TCP·흐름/혼잡 제어·DNS) 시뮬레이터의 계산 정확성, 잘못된 입력 거부,
# 개념별 예시 장면 3종(content/network/scenes.json)의 실행·분량·서로 다름을 검증
# - 기대값은 시뮬레이터를 거치지 않고 손으로 계산해 적음: 코드와 같은 실수를 테스트가 따라 하지 않게 하기 위함
import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from serving.animation import SCENE_FORMATS, SIMULATORS, simulate
from serving.animation.model import AnimationRequestModel

SCENES_PATH = Path(__file__).resolve().parents[1] / 'content' / 'network' / 'scenes.json'
CONCEPTS = ('network-layers', 'ip-subnet', 'routing', 'tcp-reliability', 'flow-congestion-control', 'dns')
# 확인 페이지·막힘 보충에서 한 장면을 끝까지 넘겨 볼 수 있는 권장 분량
SCENE_FRAME_LIMIT = 12
# cell·slot·node 안 글자 수 상한: 재생기가 4글자를 넘으면 글자 폭을 크게 줄여 읽기 어려워짐
BOX_LABEL_LIMIT = 4


## @brief 공개 요청 검증 경로(AnimationRequestModel)로 장면을 실행해 실제 도구·막힘 보충 입력 계약을 검증
def run(request):
    return simulate(AnimationRequestModel.model_validate(request).root)


## @brief 프레임에서 id로 상자 라벨을 읽음. 없으면 None
def label(frame, box_id):
    return next((b.label for b in frame.boxes if b.id == box_id), None)


## @brief 프레임의 한 줄(y)에 놓인 상자 라벨을 x 순서로 읽음
def row(frame, y, shape=None):
    return [b.label for b in sorted(frame.boxes, key=lambda b: b.x) if b.y == y and (shape is None or b.shape == shape)]


## @brief 장면의 단계별 캡션 목록
def captions(animation):
    return [f.caption for f in animation.frames]


class EncapsulationTests(unittest.TestCase):
    # 검증 목적: 송신 쪽에서 계층마다 헤더가 붙고 크기가 100 → 120 → 140 → 158바이트로 계산되는지
    def test_send_sizes(self):
        animation = run({'kind': 'encapsulation', 'mode': 'send', 'data_bytes': 100})
        sizes = [label(f, f'size{y}') for f, y in zip(animation.frames[:4], (1, 2, 3, 4))]
        self.assertEqual(sizes, ['100B', '120B', '140B', '158B'])
        self.assertEqual(row(animation.frames[3], 4, 'cell'), ['Eth', 'IP', 'TCP', 'DATA', 'FCS'])

    # 검증 목적: 라우터는 인터넷 계층까지만 올라가고 전송 계층 칸이 없음
    def test_router_stops_at_internet_layer(self):
        animation = run({'kind': 'encapsulation', 'mode': 'router'})
        router = [b for b in animation.frames[0].boxes if b.id.startswith('R-')]
        self.assertEqual(sorted(b.label for b in router), ['IP', '링크'])
        visited = [next(b for b in f.boxes if b.id == 'pkt') for f in animation.frames]
        self.assertTrue(any(b.x == 6 and b.y == 3 for b in visited))
        self.assertFalse(any(b.x == 6 and b.y < 3 for b in visited))

    # 검증 목적: OSI 7계층이 TCP/IP 4계층에 7·6·5 → 응용, 4 → 전송, 3 → 인터넷, 2·1 → 링크로 대응하는지
    def test_osi_mapping(self):
        animation = run({'kind': 'encapsulation', 'mode': 'osi'})
        arrows = {(a.source, a.target) for a in animation.frames[-1].arrows}
        self.assertEqual(arrows, {('osi7', 'tcp4'), ('osi6', 'tcp4'), ('osi5', 'tcp4'), ('osi4', 'tcp3'),
                                  ('osi3', 'tcp2'), ('osi2', 'tcp1'), ('osi1', 'tcp1')})

    # 검증 목적: 이더넷 MTU 1500을 넘는 데이터(1460바이트 초과)는 거부
    def test_rejects_oversized_data(self):
        with self.assertRaises(ValidationError):
            run({'kind': 'encapsulation', 'mode': 'send', 'data_bytes': 1461})


class SubnetTests(unittest.TestCase):
    # 검증 목적: 192.168.10.77/26 → 네트워크 .64, 브로드캐스트 .127, 호스트 62개
    def test_mask_inside_last_octet(self):
        last = run({'kind': 'subnet', 'mode': 'mask', 'address': [192, 168, 10, 77], 'prefix': 26}).frames[-1]
        self.assertEqual(row(last, 6, 'cell'), ['192', '168', '10', '64'])
        self.assertEqual(row(last, 7, 'cell'), ['192', '168', '10', '127'])
        self.assertIn('62', last.caption)

    # 검증 목적: 경계가 셋째 옥텟에 있으면(/20) 넷째 옥텟은 통째로 호스트 부분
    def test_mask_inside_third_octet(self):
        last = run({'kind': 'subnet', 'mode': 'mask', 'address': [172, 16, 5, 9], 'prefix': 20}).frames[-1]
        self.assertEqual(row(last, 6, 'cell'), ['172', '16', '0', '0'])
        self.assertEqual(row(last, 7, 'cell'), ['172', '16', '15', '255'])
        self.assertIn('4094', last.caption)

    # 검증 목적: /24를 /26으로 나누면 서브넷 4개(.0 .64 .128 .192), 각 호스트 62개
    def test_split(self):
        animation = run({'kind': 'subnet', 'mode': 'split', 'address': [192, 168, 10, 0], 'prefix': 24, 'new_prefix': 26})
        entries = [b.label for b in animation.frames[-1].boxes if b.id.startswith('sub')]
        self.assertEqual(entries, ['S1 .0~.63', 'S2 .64~.127', 'S3 .128~.191', 'S4 .192~.255'])
        self.assertIn('62', animation.frames[-1].caption)

    # 검증 목적: 같은 주소 쌍도 접두사 길이에 따라 같은 서브넷(/24)·다른 서브넷(/25)으로 갈림
    def test_same_subnet(self):
        base = {'kind': 'subnet', 'mode': 'same', 'address': [192, 168, 1, 10], 'other': [192, 168, 1, 200]}
        self.assertEqual(label(run({**base, 'prefix': 24}).frames[-1], 'verdict'), '같은 서브넷')
        self.assertEqual(label(run({**base, 'prefix': 25}).frames[-1], 'verdict'), '다른 서브넷')

    # 검증 목적: 나눌 수 없는 입력(호스트 비트가 남은 기준 주소, 3비트 초과 빌림, 짝 주소 누락)을 거부
    def test_rejects_invalid(self):
        for bad in ({'mode': 'split', 'address': [192, 168, 10, 5], 'prefix': 24, 'new_prefix': 26},
                    {'mode': 'split', 'address': [192, 168, 10, 0], 'prefix': 24, 'new_prefix': 28},
                    {'mode': 'split', 'address': [10, 0, 0, 0], 'prefix': 16, 'new_prefix': 18},
                    {'mode': 'same', 'address': [10, 0, 0, 1], 'prefix': 24},
                    {'mode': 'mask', 'address': [10, 0, 0, 256], 'prefix': 24}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                run({'kind': 'subnet', **bad})


LINKS = [{'a': 'A', 'b': 'B', 'cost': 2}, {'a': 'A', 'b': 'C', 'cost': 5}, {'a': 'B', 'b': 'C', 'cost': 1},
         {'a': 'B', 'b': 'D', 'cost': 4}, {'a': 'C', 'b': 'D', 'cost': 1}]


class RoutingTests(unittest.TestCase):
    # 검증 목적: A에서 최소 비용 B 2, C 3(B 경유), D 4(B·C 경유)이고 다음 홉은 모두 B. 확정 순서 A, B, C, D
    def test_dijkstra_table(self):
        animation = run({'kind': 'link_state', 'mode': 'dijkstra', 'nodes': ['A', 'B', 'C', 'D'], 'links': LINKS,
                         'source': 'A'})
        last = animation.frames[-1]
        self.assertEqual([label(last, f'cost{n}') for n in 'ABCD'], ['0', '2', '3', '4'])
        self.assertEqual([label(last, f'hop{n}') for n in 'BCD'], ['B', 'B', 'B'])
        self.assertEqual([c.split()[0] for c in captions(animation)[1:5]], ['A', 'B', 'C', 'D'])

    # 검증 목적: 패킷이 각 라우터의 자기 표를 보고 A → B → C → D로 전달되는지(B의 표에서 D의 다음 홉은 C)
    def test_forward_hop_by_hop(self):
        animation = run({'kind': 'link_state', 'mode': 'forward', 'nodes': ['A', 'B', 'C', 'D'], 'links': LINKS,
                         'source': 'A', 'destination': 'D'})
        holders = [next(b.label for b in f.boxes if b.id == 'owner') for f in animation.frames[:-1]]
        self.assertEqual(holders, ['A의 표', 'B의 표', 'C의 표'])
        self.assertEqual(label(animation.frames[1], 'hopD'), 'C')
        self.assertIn('A→B→C→D', animation.frames[-1].caption)

    # 검증 목적: 끊긴 망, 없는 정점, 목적지 누락을 거부
    def test_rejects_invalid(self):
        base = {'kind': 'link_state', 'mode': 'dijkstra', 'nodes': ['A', 'B', 'C'], 'source': 'A'}
        for bad in ({'links': [{'a': 'A', 'b': 'B', 'cost': 1}]},
                    {'links': [{'a': 'A', 'b': 'Z', 'cost': 1}, {'a': 'B', 'b': 'C', 'cost': 1}]},
                    {'links': [{'a': 'A', 'b': 'B', 'cost': 1}, {'a': 'B', 'b': 'C', 'cost': 1}], 'mode': 'forward'}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                run({**base, **bad})

    # 검증 목적: 목적지가 여러 항목과 일치하면 가장 긴 접두사(/24)를, 짧은 것만 맞으면 그것을 고름
    def test_longest_prefix_match(self):
        entries = [{'prefix': [10, 0, 0, 0], 'length': 8, 'hop': 'R1'},
                   {'prefix': [10, 1, 0, 0], 'length': 16, 'hop': 'R2'},
                   {'prefix': [10, 1, 2, 0], 'length': 24, 'hop': 'R3'},
                   {'prefix': [0, 0, 0, 0], 'length': 0, 'hop': 'R4'}]
        hit = run({'kind': 'prefix_match', 'destination': [10, 1, 2, 5], 'entries': entries}).frames[-1]
        self.assertEqual([label(hit, f'match{i}') for i in range(4)], ['O', 'O', 'O', 'O'])
        self.assertEqual(label(hit, 'chosen'), 'R3')
        other = run({'kind': 'prefix_match', 'destination': [10, 2, 0, 1], 'entries': entries}).frames[-1]
        self.assertEqual([label(other, f'match{i}') for i in range(4)], ['O', 'X', 'X', 'O'])
        self.assertEqual(label(other, 'chosen'), 'R1')

    # 검증 목적: 호스트 비트가 남은 접두사는 표 항목이 될 수 없어 거부
    def test_rejects_prefix_with_host_bits(self):
        with self.assertRaises(ValidationError):
            run({'kind': 'prefix_match', 'destination': [10, 1, 2, 5],
                 'entries': [{'prefix': [10, 1, 2, 3], 'length': 24, 'hop': 'R1'}, {'prefix': [0, 0, 0, 0], 'length': 0, 'hop': 'R2'}]})


class TcpTests(unittest.TestCase):
    # 검증 목적: SYN seq=x, SYN+ACK seq=y ack=x+1, ACK seq=x+1 ack=y+1
    def test_handshake_numbers(self):
        animation = run({'kind': 'tcp', 'mode': 'handshake', 'client_isn': 100, 'server_isn': 300})
        last = animation.frames[-1]
        self.assertEqual([label(last, f'info{k}') for k in range(3)], ['seq 100', 'seq 300 ack 101', 'seq 101 ack 301'])
        self.assertEqual([a.label for a in last.arrows], ['SYN', 'SYN+ACK', 'ACK'])
        self.assertEqual((label(last, 'cstate'), label(last, 'sstate')), ('ESTABLISHED', 'ESTABLISHED'))

    # 검증 목적: 2번 세그먼트 유실 → 중복 ACK 101 두 번 → 타임아웃 재전송 → 누적 ACK 401로 한 번에 확인
    def test_loss_and_timeout(self):
        animation = run({'kind': 'tcp', 'mode': 'transfer', 'seq': 1, 'mss': 100, 'segments': 4, 'lost': [2]})
        acks = [label(f, 'ack') for f in animation.frames[1:-1]]
        self.assertEqual(acks, ['101', None, '101', '101', '401'])
        self.assertIn('타임아웃', animation.frames[5].caption)

    # 검증 목적: 중복 ACK가 3개 쌓이면 타임아웃을 기다리지 않고 빠른 재전송
    def test_fast_retransmit(self):
        animation = run({'kind': 'tcp', 'mode': 'transfer', 'seq': 1, 'mss': 100, 'segments': 5, 'lost': [1]})
        self.assertIn('빠른 재전송', animation.frames[5].caption)
        self.assertEqual(label(animation.frames[5], 'ack'), '401')
        self.assertEqual(label(animation.frames[6], 'ack'), '501')

    # 검증 목적: ACK 하나가 유실돼도 다음 누적 ACK가 앞 바이트까지 확인해 재전송이 없음
    def test_lost_ack_covered(self):
        animation = run({'kind': 'tcp', 'mode': 'transfer', 'seq': 1, 'mss': 100, 'segments': 3, 'lost_acks': [1]})
        self.assertEqual(len(animation.frames), 5)
        self.assertEqual(label(animation.frames[2], 'ack'), '201')
        self.assertFalse(any('재전송' in c for c in captions(animation)))

    # 검증 목적: 세 자리를 넘는 순서 번호, 범위 밖 유실 번호, 데이터·ACK 유실 중복 지정을 거부
    def test_rejects_invalid(self):
        for bad in ({'seq': 900, 'mss': 100, 'segments': 2}, {'segments': 3, 'lost': [4]},
                    {'segments': 3, 'lost': [2], 'lost_acks': [2]}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                run({'kind': 'tcp', 'mode': 'transfer', **bad})


class FlowCongestionTests(unittest.TestCase):
    # 검증 목적: 버퍼 4칸을 채우면 rwnd 0 → 송신 멈춤, 앱이 1칸 읽고 ACK하면 1개만 다시 보냄
    def test_zero_window(self):
        animation = run({'kind': 'flow_window', 'buffer': 4, 'cwnd': 8, 'mss': 100,
                         'ops': [{'op': 'send'}, {'op': 'ack'}, {'op': 'send'}, {'op': 'read', 'count': 1},
                                 {'op': 'ack'}, {'op': 'send'}]})
        frames = animation.frames
        self.assertEqual(label(frames[1], 'sendable'), '가능 0B')
        self.assertEqual(label(frames[2], 'rwnd'), 'rwnd 0B')
        self.assertIn('0', frames[3].caption)
        self.assertEqual(label(frames[5], 'rwnd'), 'rwnd 100B')
        self.assertEqual(row(frames[6], 1, 'cell'), ['1', '2', '3', '4', '5'])

    # 검증 목적: rwnd가 넉넉해도 cwnd 2가 막고, 미확인 2개가 있으면 보낼 수 있는 양이 0
    def test_cwnd_limits(self):
        animation = run({'kind': 'flow_window', 'buffer': 6, 'cwnd': 2, 'mss': 100,
                         'ops': [{'op': 'send'}, {'op': 'send'}, {'op': 'ack'}, {'op': 'send'}]})
        self.assertEqual(row(animation.frames[1], 1, 'cell'), ['1', '2'])
        self.assertEqual(row(animation.frames[2], 1, 'cell'), ['1', '2'])
        self.assertEqual(label(animation.frames[3], 'rwnd'), 'rwnd 400B')
        self.assertEqual(row(animation.frames[4], 1, 'cell'), ['1', '2', '3', '4'])

    # 검증 목적: 버퍼에 없는 양을 읽는 연산을 거부
    def test_rejects_overread(self):
        with self.assertRaises(ValueError):
            run({'kind': 'flow_window', 'buffer': 4, 'ops': [{'op': 'read', 'count': 1}]})

    # 검증 목적: 느린 시작 1·2·4·8(ssthresh에서 멈춤) → 혼잡 회피 +1 → 타임아웃 뒤 1부터, 3중복 ACK 뒤 절반부터
    def test_congestion_window(self):
        animation = run({'kind': 'congestion', 'ssthresh': 8, 'rounds': 12,
                         'losses': [{'round': 6, 'type': 'timeout'}, {'round': 11, 'type': 'dup_ack'}]})
        cwnd = [int(label(animation.frames[-1], f'c{r}')) for r in range(12)]
        self.assertEqual(cwnd, [1, 2, 4, 8, 9, 10, 1, 2, 4, 5, 6, 3])
        self.assertEqual(len(animation.frames), 12)

    # 검증 목적: 마지막 라운드의 손실(효과를 그릴 수 없음)과 같은 라운드 중복 손실을 거부
    def test_congestion_rejects_invalid(self):
        for losses in ([{'round': 5, 'type': 'timeout'}], [{'round': 2, 'type': 'timeout'}, {'round': 2, 'type': 'dup_ack'}]):
            with self.subTest(losses=losses), self.assertRaises(ValidationError):
                run({'kind': 'congestion', 'ssthresh': 8, 'rounds': 5, 'losses': losses})


class DnsTests(unittest.TestCase):
    # 검증 목적: 반복 질의는 로컬 DNS가 루트 → TLD → 권한 서버에 차례로 묻고 참조를 받음
    def test_iterative_order(self):
        animation = run({'kind': 'dns', 'mode': 'iterative', 'name': ['www', 'example', 'com']})
        steps = [next((a.source, a.target) for a in f.arrows if a.label) for f in animation.frames[1:]]
        self.assertEqual(steps, [('pc', 'local'), ('local', 'root'), ('root', 'local'), ('local', 'tld'), ('tld', 'local'),
                                 ('local', 'auth'), ('auth', 'local'), ('local', 'pc')])

    # 검증 목적: 재귀 질의는 서버가 다음 서버에 대신 묻고 답이 거꾸로 돌아옴
    def test_recursive_order(self):
        animation = run({'kind': 'dns', 'mode': 'recursive', 'name': ['example', 'com']})
        steps = [next((a.source, a.target) for a in f.arrows if a.label) for f in animation.frames[1:]]
        self.assertEqual(steps, [('pc', 'local'), ('local', 'root'), ('root', 'tld'), ('tld', 'auth'), ('auth', 'tld'),
                                 ('tld', 'root'), ('root', 'local'), ('local', 'pc')])

    # 검증 목적: TTL 300에서 t=120 적중(남은 180), t=400 만료 후 재조회, t=650 적중(남은 50)
    def test_cache_ttl(self):
        animation = run({'kind': 'dns', 'mode': 'cache', 'name': ['www', 'example', 'com'], 'ttl': 300,
                         'queries': [0, 120, 400, 650]})
        remaining = [label(f, 'remain') for f in animation.frames[1:]]
        self.assertEqual(remaining, ['남은 TTL 300', '남은 TTL 180', '남은 TTL 300', '남은 TTL 50'])
        self.assertIn('만료', animation.frames[3].caption)

    # 검증 목적: 이름 형식 오류, 질의 시각 역순, 캐시 장면의 질의 누락을 거부
    def test_rejects_invalid(self):
        for bad in ({'mode': 'iterative', 'name': ['WWW', 'com']}, {'mode': 'cache', 'name': ['a', 'com'], 'queries': [5, 3]},
                    {'mode': 'cache', 'name': ['a', 'com']}, {'mode': 'iterative', 'name': ['verylongname1', 'example', 'com']}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                run({'kind': 'dns', **bad})


## @brief 개념별 예시 장면 3종 데이터가 모두 실행되고 분량·글자 수 상한을 지키며 서로 다른지 검증
class SceneContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenes = json.loads(SCENES_PATH.read_text(encoding='utf-8'))

    # 검증 목적: 맡은 개념 6개 전부에 정확히 장면 3개가 있고 다른 개념은 없음
    def test_concepts_have_three_scenes(self):
        self.assertEqual(set(self.scenes), set(CONCEPTS))
        for concept, requests in self.scenes.items():
            self.assertEqual(len(requests), 3, concept)

    # 검증 목적: 모든 장면이 실행되고, 12장 이하이며, 상자 안 글자가 4자 이하
    def test_scenes_run_within_limits(self):
        for concept, requests in self.scenes.items():
            for request in requests:
                with self.subTest(concept=concept, request=request):
                    animation = run(request)
                    self.assertLessEqual(len(animation.frames), SCENE_FRAME_LIMIT)
                    for frame in animation.frames:
                        for box in frame.boxes:
                            if box.shape != 'label':
                                self.assertLessEqual(len(box.label), BOX_LABEL_LIMIT, box)

    # 검증 목적: 같은 개념의 3개가 요청 JSON도, 캡션 수열도 서로 달라 숫자만 바꾼 장면이 아님
    def test_scenes_are_distinct(self):
        for concept, requests in self.scenes.items():
            runs = [captions(run(r)) for r in requests]
            with self.subTest(concept=concept):
                self.assertEqual(len({json.dumps(r, sort_keys=True) for r in requests}), 3)
                self.assertEqual(len({tuple(c) for c in runs}), 3)
                self.assertEqual(len({c[0] for c in runs}), 3)

    # 검증 목적: 장면에 쓴 종류가 시뮬레이터 표에 등록돼 있고, 장면 선택 형식 예시도 그대로 실행됨
    def test_kinds_registered(self):
        for kind in {r['kind'] for requests in self.scenes.values() for r in requests}:
            self.assertIn(kind, SIMULATORS)
            run(SCENE_FORMATS[kind])

if __name__ == '__main__':
    unittest.main()
