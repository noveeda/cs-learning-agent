## @file
# @brief 컴퓨터 구조 장면(2의 보수·명령어 사이클·파이프라인·메모리 계층·캐시·페이지 변환)이 손으로 계산한 값과 같은 상태를 그리는지,
# 그릴 수 없는 입력을 거부하는지, 개념별 예시 장면 3개(content/arch/scenes.json)가 모두 실행되고 서로 다른지 검증
# - 기대값은 시뮬레이터를 거치지 않고 손으로 계산해 적음: 코드와 같은 실수를 테스트가 따라 하지 않게 하기 위함
import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from serving.animation import SCENE_FORMATS, SIMULATORS, simulate
from serving.animation.model import AnimationRequestModel

SCENES = Path(__file__).resolve().parents[1] / 'content' / 'arch' / 'scenes.json'
ARCH_KINDS = {'twos_complement', 'instruction_cycle', 'pipeline', 'memory_hierarchy', 'cache', 'paging'}
CONCEPTS = {'binary-twos-complement', 'instruction-cycle', 'pipelining-hazards', 'memory-hierarchy', 'cache-locality',
            'virtual-memory-pages'}


def run(data: dict):
    return simulate(AnimationRequestModel.model_validate(data).root)


## @brief 프레임에서 id로 상자 하나를 찾음. 없으면 None
def box(frame, box_id):
    return next((b for b in frame.boxes if b.id == box_id), None)


## @brief 프레임의 y행에 있는 cell 상자 라벨을 x 순서로 이어 붙임(비트 행 읽기)
def row(frame, y):
    return ''.join(b.label for b in sorted((b for b in frame.boxes if b.y == y and b.shape == 'cell'), key=lambda b: b.x))


class TwosComplementTests(unittest.TestCase):
    # 검증 목적: -6을 4비트로 만들 때 6(0110) → 반전(1001) → +1(1010) 순서를 그리는지
    def test_encode_negative_inverts_then_adds_one(self):
        animation = run({'kind': 'twos_complement', 'operation': 'encode', 'bits': 4, 'a': -6})
        last = animation.frames[-1]
        self.assertEqual([row(last, 1), row(last, 2), row(last, 3)], ['0110', '1001', '1010'])
        self.assertIn('-8', [b.label for b in last.boxes if b.y == 0])

    # 검증 목적: 양수는 반전 없이 그대로 2진수로 그림
    def test_encode_positive_has_no_inversion(self):
        animation = run({'kind': 'twos_complement', 'operation': 'encode', 'bits': 4, 'a': 5})
        self.assertEqual(row(animation.frames[-1], 1), '0101')
        self.assertEqual(row(animation.frames[-1], 2), '')

    # 검증 목적: 표현 범위의 가장 작은 수(-8)도 반전·+1 뒤 1000으로 돌아옴
    def test_encode_minimum_value(self):
        animation = run({'kind': 'twos_complement', 'operation': 'encode', 'bits': 4, 'a': -8})
        self.assertEqual(row(animation.frames[-1], 3), '1000')

    # 검증 목적: 5 + 4 = 9는 4비트 범위(-8..7)를 넘어 1001(-7)이 되는 부호 있는 오버플로
    def test_add_detects_signed_overflow(self):
        animation = run({'kind': 'twos_complement', 'operation': 'add', 'bits': 4, 'a': 5, 'b': 4})
        last = animation.frames[-1]
        self.assertEqual([row(last, 2), row(last, 3), row(last, 4)], ['0101', '0100', '1001'])
        self.assertEqual(len(animation.frames), 4 + 2)
        self.assertIn('오버플로', last.caption)

    # 검증 목적: -3 + 5 = 2는 맨 위 자리 올림(버림)이 있어도 오버플로가 아님
    def test_add_carry_out_is_not_overflow(self):
        animation = run({'kind': 'twos_complement', 'operation': 'add', 'bits': 4, 'a': -3, 'b': 5})
        last = animation.frames[-1]
        self.assertEqual(row(last, 4), '0010')
        self.assertEqual(box(last, 'cout').label, '1')
        self.assertNotIn('오버플로입니다', last.caption)

    # 검증 목적: -3(1101)을 8비트로 늘리면 부호 비트 1을 왼쪽에 채워 11111101
    def test_extend_copies_sign_bit(self):
        animation = run({'kind': 'twos_complement', 'operation': 'extend', 'bits': 4, 'a': -3, 'to_bits': 8})
        last = animation.frames[-1]
        self.assertEqual(row(last, 1), '1101')
        self.assertEqual(row(last, 2), '11111101')

    # 검증 목적: 비트 수 범위 밖 값, add의 b 누락, 줄어드는 extend를 거부
    def test_rejects_invalid_inputs(self):
        for data in ({'operation': 'encode', 'bits': 4, 'a': 8},
                     {'operation': 'add', 'bits': 4, 'a': 1},
                     {'operation': 'add', 'bits': 4, 'a': 1, 'b': -9},
                     {'operation': 'extend', 'bits': 4, 'a': 1, 'to_bits': 4},
                     {'operation': 'extend', 'bits': 4, 'a': 1}):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                run({'kind': 'twos_complement', **data})


## @brief 명령어 사이클 장면 요청 축약
def program(*rows, length=4, start=0, detail='phase', memory=None):
    instructions = [{'opcode': op, **({'target': arg} if op == 'JUMP' else {'var': arg})} for op, arg in rows]
    return {'kind': 'instruction_cycle', 'length': length, 'start': start, 'detail': detail,
            'memory': memory or {'A': 3, 'B': 4, 'C': 0}, 'program': instructions}


class InstructionCycleTests(unittest.TestCase):
    # 검증 목적: 인출·해독·실행 3단계마다 한 프레임, 인출에서 PC가 길이만큼 증가, 끝나면 C = 3 + 4
    def test_phases_update_pc_ir_and_memory(self):
        animation = run(program(('LOAD', 'A'), ('ADD', 'B'), ('STORE', 'C')))
        self.assertEqual(len(animation.frames), 1 + 3 * 3 + 1)
        fetch = animation.frames[1]
        self.assertEqual((box(fetch, 'pc').label, box(fetch, 'ir').label), ('4', 'LOAD A'))
        self.assertEqual([box(f, 'pc').label for f in animation.frames], ['0', '4', '4', '4', '8', '8', '8', '12', '12', '12', '12'])
        self.assertEqual(box(animation.frames[-1], 'memC').label, '7')
        self.assertEqual(box(animation.frames[-1], 'acc').label, '7')

    # 검증 목적: 명령어 단위 모드는 명령어마다 한 프레임, 길이 2바이트면 PC가 100, 102, 104… 로 진행
    def test_instruction_detail_advances_pc_by_length(self):
        animation = run(program(('LOAD', 'A'), ('ADD', 'B'), ('ADD', 'B'), ('STORE', 'C'), length=2, start=100,
                                detail='instruction'))
        self.assertEqual([box(f, 'pc').label for f in animation.frames], ['100', '102', '104', '106', '108', '108'])
        self.assertEqual(box(animation.frames[-1], 'memC').label, '11')

    # 검증 목적: JUMP는 실행 단계에서 PC를 덮어써 건너뛴 명령어(ADD B)를 실행하지 않음
    def test_jump_skips_instruction(self):
        animation = run(program(('LOAD', 'A'), ('JUMP', 12), ('ADD', 'B'), ('STORE', 'C')))
        self.assertEqual(box(animation.frames[-1], 'memC').label, '3')
        self.assertEqual(len(animation.frames), 1 + 3 * 3 + 1)
        self.assertEqual(box(animation.frames[6], 'pc').label, '12')

    # 검증 목적: 프로그램 밖 점프 주소, 메모리에 없는 변수, 끝나지 않는 반복을 거부
    def test_rejects_invalid_programs(self):
        with self.assertRaises(ValidationError):
            run(program(('LOAD', 'A'), ('JUMP', 6)))
        with self.assertRaises(ValidationError):
            run(program(('LOAD', 'D')))
        with self.assertRaises(ValueError):
            run(program(('LOAD', 'A'), ('JUMP', 0)))


## @brief 파이프라인 명령어 축약: (opcode, dest, srcs...)
def pipe(*rows, forwarding):
    return {'kind': 'pipeline', 'forwarding': forwarding,
            'instructions': [{'opcode': op, 'dest': dest, 'srcs': list(srcs)} for op, dest, *srcs in rows]}


## @brief 마지막 프레임에서 i번째 명령어의 사이클별 단계 라벨(1사이클부터, 빈 사이클은 '', 끝난 뒤 빈 사이클은 뺌)
def stages(animation, i):
    frame = animation.frames[-1]
    cycles = max(int(b.id.split('c')[1]) for b in frame.boxes if b.id.startswith('i') and 'c' in b.id)
    labels = [b.label if (b := box(frame, f'i{i}c{c}')) else '' for c in range(1, cycles + 1)]
    while labels and not labels[-1]:
        labels.pop()
    return labels


class PipelineTests(unittest.TestCase):
    # 검증 목적: 의존성 없는 4개는 정지 없이 한 사이클씩 겹쳐 4 + 5 - 1 = 8사이클
    def test_independent_instructions_overlap(self):
        animation = run(pipe(('add', 'r1', 'r2', 'r3'), ('sub', 'r4', 'r5', 'r6'), ('add', 'r7', 'r8', 'r9'),
                             ('lw', 'r2', 'r3'), forwarding=False))
        self.assertEqual(stages(animation, 3), ['', '', '', 'IF', 'ID', 'EX', 'MEM', 'WB'])
        self.assertEqual(len(animation.frames), 1 + 8 + 1)

    # 검증 목적: forwarding이 있으면 ALU 결과 RAW는 정지 없이 EX → EX로 전달(화살표)
    def test_forwarding_removes_alu_stall(self):
        animation = run(pipe(('add', 'r1', 'r2', 'r3'), ('sub', 'r4', 'r1', 'r5'), forwarding=True))
        self.assertEqual(stages(animation, 1), ['', 'IF', 'ID', 'EX', 'MEM', 'WB'])
        arrows = [(a.source, a.target) for a in animation.frames[4].arrows]
        self.assertIn(('i0c3', 'i1c4'), arrows)

    # 검증 목적: forwarding이 없으면 레지스터 파일 쓰기(WB)까지 2사이클 정지, 뒤 명령어도 함께 밀림
    def test_no_forwarding_stalls_two_cycles(self):
        animation = run(pipe(('add', 'r1', 'r2', 'r3'), ('sub', 'r4', 'r1', 'r5'), ('add', 'r6', 'r7', 'r8'),
                             forwarding=False))
        self.assertEqual(stages(animation, 1), ['', 'IF', 'ID', '-', '-', 'EX', 'MEM', 'WB'])
        self.assertEqual(stages(animation, 2), ['', '', 'IF', '-', '-', 'ID', 'EX', 'MEM', 'WB'])

    # 검증 목적: load 직후 사용(load-use)은 forwarding이 있어도 1사이클 정지, MEM → EX로 전달
    def test_load_use_needs_one_stall_with_forwarding(self):
        animation = run(pipe(('lw', 'r1', 'r2'), ('add', 'r3', 'r1', 'r4'), forwarding=True))
        self.assertEqual(stages(animation, 1), ['', 'IF', 'ID', '-', 'EX', 'MEM', 'WB'])
        arrows = [(a.source, a.target) for a in animation.frames[5].arrows]
        self.assertIn(('i0c4', 'i1c5'), arrows)

    # 검증 목적: 두 원본이 같은 생산 명령어(r1, r1)여도 전달 화살표는 하나(재생기의 화살표 key 중복 방지)
    def test_same_producer_twice_draws_one_arrow(self):
        animation = run(pipe(('add', 'r1', 'r2', 'r3'), ('add', 'r4', 'r1', 'r1'), forwarding=True))
        self.assertEqual([(a.source, a.target) for a in animation.frames[-1].arrows], [('i0c3', 'i1c4')])

    # 검증 목적: lw의 원본 레지스터 개수 오류, 10사이클(격자 폭)을 넘는 장면을 거부
    def test_rejects_invalid_pipelines(self):
        with self.assertRaises(ValidationError):
            run(pipe(('lw', 'r1', 'r2', 'r3'), ('add', 'r3', 'r1', 'r4'), forwarding=True))
        chain = [('add', 'r1', 'r1', 'r2')] * 4
        with self.assertRaises(ValidationError):
            run(pipe(*chain, forwarding=False))


## @brief 메모리 계층 장면 요청 축약: levels는 (이름, 칸 수, 접근 시간)
def hierarchy(levels, accesses):
    return {'kind': 'memory_hierarchy', 'accesses': list(accesses),
            'levels': [{'name': n, 'slots': s, 'latency': t} for n, s, t in levels]}


## @brief 마지막 프레임의 접근별 비용(y=7 label)
def costs(animation):
    return [b.label for b in sorted((b for b in animation.frames[-1].boxes if b.id.startswith('cost')), key=lambda b: b.x)]


class MemoryHierarchyTests(unittest.TestCase):
    # 검증 목적: 위 계층부터 차례로 찾아 비용이 누적되고(1+10+100), 찾은 데이터는 위 계층으로 복사됨
    def test_lookup_walks_down_and_copies_up(self):
        animation = run(hierarchy([('L1', 1, 1), ('L2', 2, 10), ('DRAM', 4, 100)], 'abaa'))
        self.assertEqual(costs(animation), ['111', '111', '11', '1'])
        self.assertIn('58.5', animation.frames[-1].caption)

    # 검증 목적: LRU 교체에서 칸 2개에 a,b,c를 반복하면 매번 밀려나 모두 미스
    def test_small_cache_thrashes(self):
        animation = run(hierarchy([('L1', 2, 1), ('DRAM', 3, 100)], 'abcabc'))
        self.assertEqual(costs(animation), ['101'] * 6)

    # 검증 목적: 단일 캐시의 평균 접근 시간 = 적중 시간 + 미스율 × 미스 페널티(2 + 3/8 × 50 = 20.75)
    def test_amat_matches_formula(self):
        animation = run(hierarchy([('L1', 4, 2), ('DRAM', 3, 50)], 'abaabaca'))
        self.assertEqual(costs(animation).count('52'), 3)
        self.assertIn('20.75', animation.frames[-1].caption)

    # 검증 목적: 계층 순서가 뒤집힌 요청, 이름 중복, 맨 아래 계층에 다 들어가지 않는 데이터, 4자리를 넘는 비용을 거부
    def test_rejects_invalid_levels(self):
        for levels in ([('DRAM', 4, 100), ('L1', 1, 1)], [('L1', 1, 1), ('L1', 2, 10)], [('L1', 1, 1), ('DRAM', 1, 100)],
                       [('L1', 1, 1), ('DRAM', 2, 1000)]):
            with self.subTest(levels=levels), self.assertRaises(ValidationError):
                run(hierarchy(levels, 'ab'))


## @brief 직접 사상 캐시 장면 요청 축약
def cache(block_size, lines, addresses):
    return {'kind': 'cache', 'block_size': block_size, 'lines': lines, 'addresses': list(addresses)}


## @brief 마지막 프레임의 접근별 적중·미스(y=7 label)
def outcomes(animation):
    return [b.label for b in sorted((b for b in animation.frames[-1].boxes if b.id.startswith('hm')), key=lambda b: b.x)]


class CacheTests(unittest.TestCase):
    # 검증 목적: 4바이트 원소를 연속으로 읽으면 16바이트 블록마다 첫 접근만 미스(공간 지역성)
    def test_sequential_access_hits_within_block(self):
        animation = run(cache(16, 4, [0, 4, 8, 12, 16, 20, 24, 28]))
        self.assertEqual(outcomes(animation), ['미스', '적중', '적중', '적중', '미스', '적중', '적중', '적중'])
        self.assertIn('6/8', animation.frames[-1].caption)

    # 검증 목적: 같은 주소를 다시 읽으면 적중(시간 지역성)
    def test_repeated_access_hits(self):
        animation = run(cache(8, 4, [8, 20, 8, 20, 8]))
        self.assertEqual(outcomes(animation), ['미스', '미스', '적중', '적중', '적중'])

    # 검증 목적: 블록 0과 4가 같은 라인(0)에 사상되어 서로 밀어내는 충돌 미스
    def test_conflicting_blocks_evict_each_other(self):
        animation = run(cache(8, 4, [0, 32, 0, 32]))
        self.assertEqual(outcomes(animation), ['미스'] * 4)
        self.assertEqual(box(animation.frames[-1], 'line0').label, '4')
        self.assertIn('충돌', animation.frames[2].caption)

    # 검증 목적: 2의 거듭제곱이 아닌 블록 크기, 범위 밖 주소를 거부
    def test_rejects_invalid_cache(self):
        for data in (cache(3, 4, [0]), cache(8, 4, [256]), cache(8, 3, [0])):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                run(data)


## @brief 페이지 변환 장면 요청 축약
def paging(address_bits, offset_bits, table, addresses, tlb_size=0, free_frames=()):
    return {'kind': 'paging', 'address_bits': address_bits, 'offset_bits': offset_bits, 'page_table': table,
            'addresses': list(addresses), 'tlb_size': tlb_size, 'free_frames': list(free_frames)}


class PagingTests(unittest.TestCase):
    # 검증 목적: 77 = 010|01101 → 페이지 2, 오프셋 13 → 프레임 5 → 5 × 32 + 13 = 173
    def test_translation_splits_page_and_offset(self):
        animation = run(paging(8, 5, [3, 7, 5, 1, None, None, 0, 2], [77]))
        last = animation.frames[-1]
        self.assertEqual(row(animation.frames[0], 0), '01001101')
        self.assertEqual(row(last, 7), '10101101')
        self.assertEqual(box(last, 'pav').label, '=173')

    # 검증 목적: 같은 페이지를 다시 쓰면 TLB 적중, 처음 페이지는 TLB 미스 후 표에서 찾아 TLB에 기록
    def test_tlb_hit_after_first_miss(self):
        animation = run(paging(7, 4, [2, None, 6, 4, None, 1, None, None], [37, 42, 90], tlb_size=2))
        captions = [f.caption for f in animation.frames]
        self.assertEqual(sum('TLB 적중' in c for c in captions), 1)
        self.assertEqual(sum('TLB 미스' in c for c in captions), 2)
        self.assertEqual(box(animation.frames[-1], 'pav').label, '=26')
        self.assertEqual({box(animation.frames[-1], 'tlb0').label, box(animation.frames[-1], 'tlb1').label}, {'2:6', '5:1'})

    # 검증 목적: 메모리에 없는 페이지는 페이지 폴트 → 빈 프레임에 적재·표 갱신 → 다시 쓰면 바로 변환
    def test_page_fault_loads_into_free_frame(self):
        animation = run(paging(6, 3, [1, None, 4, None, 0, None, None, 2], [27, 29], free_frames=[6]))
        self.assertEqual(sum('페이지 폴트' in f.caption for f in animation.frames), 1)
        self.assertEqual(box(animation.frames[-1], 'pt3').label, '3:6')
        self.assertEqual(box(animation.frames[-1], 'pav').label, '=53')

    # 검증 목적: 표 길이 불일치, 주소 범위 초과, 빈 프레임 없는 폴트, 중복 프레임을 거부
    def test_rejects_invalid_paging(self):
        with self.assertRaises(ValidationError):
            run(paging(8, 5, [0, 1], [3]))
        with self.assertRaises(ValidationError):
            run(paging(6, 3, [0, 1, 2, 3, 4, 5, 6, 7], [64]))
        with self.assertRaises(ValidationError):
            run(paging(6, 3, [0, 0, 2, 3, 4, 5, 6, 7], [1]))
        with self.assertRaises(ValueError):
            run(paging(6, 3, [None, 1, 2, 3, 4, 5, 6, 7], [1]))


## @brief 개념별 예시 장면 데이터(scenes.json)가 실행되고 개념마다 서로 다른 3개인지 검증
class ArchScenesTests(unittest.TestCase):
    ## @brief 모든 장면을 한 번만 실행해 둠
    @classmethod
    def setUpClass(cls):
        cls.scenes = json.loads(SCENES.read_text(encoding='utf-8'))
        cls.animations = {concept: [run(request) for request in requests] for concept, requests in cls.scenes.items()}

    # 검증 목적: 맡은 6개 개념 모두, 개념마다 정확히 3개
    def test_every_concept_has_three_scenes(self):
        self.assertEqual(set(self.scenes), CONCEPTS)
        for concept, requests in self.scenes.items():
            self.assertEqual(len(requests), 3, concept)

    # 검증 목적: 3개가 요청 JSON으로도, 그려지는 프레임 수열로도 서로 다름(숫자만 같은 장면 반복 방지)
    def test_scenes_are_distinct(self):
        for concept, requests in self.scenes.items():
            dumps = {json.dumps(r, sort_keys=True) for r in requests}
            self.assertEqual(len(dumps), 3, concept)
            sequences = {json.dumps([f.model_dump() for f in a.frames], sort_keys=True) for a in self.animations[concept]}
            self.assertEqual(len(sequences), 3, concept)

    # 검증 목적: 한 장면은 12단계 이하, 칸 안 라벨은 3글자(줄이지 않고 읽히는 폭), 이름표는 8글자 이하
    def test_scene_size_and_label_limits(self):
        for concept, animations in self.animations.items():
            for animation in animations:
                self.assertLessEqual(len(animation.frames), 12, (concept, animation.title))
                for frame in animation.frames:
                    for b in frame.boxes:
                        limit = 8 if b.shape == 'label' else 3
                        self.assertLessEqual(len(b.label), limit, (concept, b))

    # 검증 목적: 새 장면 종류가 등록·형식 예시와 함께 실행 경로에 들어갔는지
    def test_new_kinds_are_registered(self):
        self.assertLessEqual(ARCH_KINDS, set(SIMULATORS))
        for kind in ARCH_KINDS:
            self.assertEqual(run(SCENE_FORMATS[kind]).kind, kind)


if __name__ == '__main__':
    unittest.main()
