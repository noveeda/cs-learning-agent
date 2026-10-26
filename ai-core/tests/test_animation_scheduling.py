# 파일 역할: CPU 스케줄링 간트 차트 장면이 손으로 계산한 교과서 예제와 같은 순서·대기 시간을 그리는지,
# 동점·유휴 구간 규칙을 지키는지, 그릴 수 없는 입력을 거부하는지, 장면 선택 LLM 경로에 형식이 들어가는지 검증
# - 기대값은 시뮬레이터를 거치지 않고 손으로 계산해 적음: 코드와 같은 실수를 테스트가 따라 하지 않게 하기 위함
# - 동점 규칙(MVP 4 계획 Review Focus 2): 도착 시각 → 프로세스 이름 순. RR에서 같은 시각이면 새 도착이 할당량이 끝난 프로세스보다 먼저 큐에 들어감
import json
import unittest

from pydantic import ValidationError

from serving.animation import SCENE_FORMATS, check_limits, create_animation, scene_formats, scene_schema, simulate
from serving.animation.model import MAX_FRAMES, AnimationRequestModel


# 역할: 프로세스 목록 축약. (이름, 도착, 실행[, 우선순위]) 튜플을 요청 dict로
def procs(*rows):
    return [dict(zip(('name', 'arrival', 'burst', 'priority'), row)) for row in rows]


def run(algorithm, processes, quantum=None):
    request = {'kind': 'cpu_schedule', 'algorithm': algorithm, 'processes': processes}
    if quantum is not None:
        request['quantum'] = quantum
    return simulate(AnimationRequestModel.model_validate(request).root)


# 역할: 마지막 프레임의 간트 줄(y=1 cell)을 x 순서대로 읽음
def gantt(animation):
    return [b.label for b in sorted((b for b in animation.frames[-1].boxes if b.y == 1), key=lambda b: b.x)]


# 역할: 마지막 프레임의 대기 시간 줄(y=6 label, 'P1 대기 4')을 {이름: 대기 시간}으로 읽음
def waits(animation):
    rows = (b.label.split() for b in animation.frames[-1].boxes if b.y == 6)
    return {name: int(value) for name, _, value in rows}


class ScheduleTests(unittest.TestCase):
    # 검증 목적: FCFS는 도착 순서대로 끝까지 실행하고, 대기 시간 = 끝난 시각 - 도착 - 실행 시간
    def test_fcfs(self):
        animation = run('fcfs', procs(('P1', 0, 5), ('P2', 1, 3), ('P3', 2, 1)))
        self.assertEqual(gantt(animation), ['P1'] * 5 + ['P2'] * 3 + ['P3'])
        self.assertEqual(waits(animation), {'P1': 0, 'P2': 4, 'P3': 6})
        self.assertIn('3.33', animation.frames[-1].caption)

    # 검증 목적: 비선점 SJF는 P1이 끝난 시각(5)에 준비된 P2(3)·P3(1) 중 짧은 P3를 먼저 고름
    def test_sjf_non_preemptive(self):
        animation = run('sjf', procs(('P1', 0, 5), ('P2', 1, 3), ('P3', 2, 1)))
        self.assertEqual(gantt(animation), ['P1'] * 5 + ['P3'] + ['P2'] * 3)
        self.assertEqual(waits(animation), {'P1': 0, 'P2': 5, 'P3': 3})
        self.assertIn('2.67', animation.frames[-1].caption)

    # 검증 목적: RR(q=2)은 할당량만큼 실행한 뒤 남은 프로세스를 큐 뒤로 보냄
    def test_round_robin(self):
        animation = run('rr', procs(('P1', 0, 5), ('P2', 0, 3), ('P3', 0, 1)), quantum=2)
        self.assertEqual(gantt(animation), ['P1', 'P1', 'P2', 'P2', 'P3', 'P1', 'P1', 'P2', 'P1'])
        self.assertEqual(waits(animation), {'P1': 4, 'P2': 5, 'P3': 4})
        self.assertIn('4.33', animation.frames[-1].caption)

    # 검증 목적: 비선점 우선순위는 숫자가 작은 쪽(P2, 1)을 먼저 실행
    def test_priority(self):
        animation = run('priority', procs(('P1', 0, 3, 2), ('P2', 0, 2, 1)))
        self.assertEqual(gantt(animation), ['P2'] * 2 + ['P1'] * 3)
        self.assertEqual(waits(animation), {'P1': 2, 'P2': 0})

    # 검증 목적: 첫 도착 전 빈 구간은 '-'(유휴) 칸으로 그리고, 유휴 시간은 대기 시간에 넣지 않음(Review Focus 3)
    def test_idle_before_arrival(self):
        for algorithm in ('fcfs', 'sjf', 'rr'):
            animation = run(algorithm, procs(('P1', 2, 2)), quantum=2 if algorithm == 'rr' else None)
            self.assertEqual(gantt(animation), ['-', '-', 'P1', 'P1'], algorithm)
            self.assertEqual(waits(animation), {'P1': 0}, algorithm)

    # 검증 목적: 실행 중간에 CPU가 비는 구간도 '-'로 그림(P1이 2에 끝나고 P2는 4에 도착)
    def test_idle_between_processes(self):
        animation = run('sjf', procs(('P1', 0, 2), ('P2', 4, 1)))
        self.assertEqual(gantt(animation), ['P1', 'P1', '-', '-', 'P2'])
        self.assertEqual(waits(animation), {'P1': 0, 'P2': 0})


class TieTests(unittest.TestCase):
    # 검증 목적: 도착 시각이 같으면 입력 순서와 관계없이 이름 순(P1 먼저)
    def test_same_arrival_uses_name_order(self):
        animation = run('fcfs', procs(('P2', 0, 1), ('P1', 0, 2)))
        self.assertEqual(gantt(animation), ['P1', 'P1', 'P2'])

    # 검증 목적: SJF에서 실행 시간이 같으면 도착이 이른 쪽이 먼저(이름이 더 작은 P2보다 먼저 온 P3), 도착도 같으면 이름 순
    def test_sjf_tie_arrival_then_name(self):
        animation = run('sjf', procs(('P1', 0, 4), ('P3', 1, 2), ('P2', 2, 2)))
        self.assertEqual(gantt(animation), ['P1'] * 4 + ['P3'] * 2 + ['P2'] * 2)
        animation = run('sjf', procs(('P1', 0, 4), ('P3', 1, 2), ('P2', 1, 2)))
        self.assertEqual(gantt(animation), ['P1'] * 4 + ['P2'] * 2 + ['P3'] * 2)

    # 검증 목적: 우선순위가 같으면 도착 시각 → 이름 순
    def test_priority_tie(self):
        animation = run('priority', procs(('P1', 0, 1, 1), ('P3', 0, 1, 2), ('P2', 0, 1, 2)))
        self.assertEqual(gantt(animation), ['P1', 'P2', 'P3'])

    # 검증 목적: RR에서 P2가 도착한 시각(2)에 P1의 할당량도 끝나면 새 도착 P2가 먼저 큐에 들어감(교과서 관례)
    def test_round_robin_new_arrival_before_expired(self):
        animation = run('rr', procs(('P1', 0, 3), ('P2', 2, 1)), quantum=2)
        self.assertEqual(gantt(animation), ['P1', 'P1', 'P2', 'P1'])
        self.assertEqual(waits(animation), {'P1': 1, 'P2': 0})


class FrameTests(unittest.TestCase):
    # 검증 목적: 가장 잘게 나뉘는 입력(RR q=1, 총 12칸)도 MAX_FRAMES 안이고, 격자·설명 길이 제한을 지킴
    def test_longest_scene_fits(self):
        animation = run('rr', procs(('P1', 0, 3), ('P2', 0, 3), ('P3', 0, 2), ('P4', 0, 2), ('P5', 0, 2)), quantum=1)
        self.assertLessEqual(len(animation.frames), MAX_FRAMES)
        self.assertEqual(len(gantt(animation)), 12)
        self.assertTrue(all(len(f.caption) <= 80 for f in animation.frames))

    # 검증 목적: 준비 큐 줄(y=4)이 실행 중이 아닌 대기 프로세스를 큐 순서대로 보여 줌
    # - RR 예제에서 P3를 꺼낸 시각 4의 큐는 [P1, P2]
    def test_ready_queue_row(self):
        animation = run('rr', procs(('P1', 0, 5), ('P2', 0, 3), ('P3', 0, 1)), quantum=2)
        frame = next(f for f in animation.frames if any(b.y == 1 and b.x == 4 for b in f.boxes))
        queue = [b.label for b in sorted((b for b in frame.boxes if b.y == 4), key=lambda b: b.x)]
        self.assertEqual(queue, ['P1', 'P2'])
        self.assertIn('준비 큐', [b.label for b in frame.boxes if b.y == 3])

    # 검증 목적: 실행 구간 하나를 묶은 프레임에서, 그 구간 끝까지 도착해 기다리는 프로세스가 준비 큐 줄(y=4)에 보임
    # - 운영 E2E(2026-10-26) 결함: FCFS "0~5" 프레임에서 그 사이 도착한 P2·P3가 큐에 없었음(꺼낸 순간의 큐만 그림)
    # - SJF·우선순위는 다음에 고를 순서(실행 시간·우선순위 → 도착 → 이름)로, FCFS·RR은 도착 순서로 보임
    def test_ready_queue_includes_arrivals_during_segment(self):
        cases = [
            # FCFS: 0~5에 P1 실행 중 P2(1)·P3(2) 도착
            (('fcfs', procs(('P1', 0, 5), ('P2', 1, 3), ('P3', 2, 1)), None), '0~5', ['P2', 'P3']),
            # SJF: 같은 입력, 기다리는 순서는 실행 시간 순(P3 1, P2 3)
            (('sjf', procs(('P1', 0, 5), ('P2', 1, 3), ('P3', 2, 1)), None), '0~5', ['P3', 'P2']),
            # RR q=2: P1 0~2 뒤 큐 [P2, P1], P2 2~4 실행 중 P3(3) 도착 → 기다리는 큐 [P1, P3]
            (('rr', procs(('P1', 0, 3), ('P2', 1, 2), ('P3', 3, 1)), 2), '2~4', ['P1', 'P3']),
        ]
        for (algorithm, processes, quantum), span, expected in cases:
            animation = run(algorithm, processes, quantum)
            frame = next(f for f in animation.frames if f.caption.startswith(span + ':'))
            queue = [b.label for b in sorted((b for b in frame.boxes if b.y == 4), key=lambda b: b.x)]
            self.assertEqual(queue, expected, algorithm)

    # 검증 목적: 마지막 프로세스가 끝나는 시각이 화면에 보임
    # - 눈금(y=0)은 칸의 시작 시각(0~총길이-1)만 적어, 끝 시각(예: 9)이 어디에도 나오지 않던 결함(운영 E2E 2026-10-26)
    # - 12칸을 다 쓰는 장면도 끝 시각 12가 보이고 설명 길이 제한(80자)을 지켜야 함
    def test_end_time_shown(self):
        animation = run('fcfs', procs(('P1', 0, 5), ('P2', 1, 3), ('P3', 2, 1)))
        self.assertIn('시각 9', animation.frames[-1].caption)
        longest = run('rr', procs(('P1', 0, 3), ('P2', 0, 3), ('P3', 0, 2), ('P4', 0, 2), ('P5', 0, 2)), quantum=1)
        self.assertIn('시각 12', longest.frames[-1].caption)
        self.assertLessEqual(len(longest.frames[-1].caption), 80)

    # 검증 목적: 채팅 도구 경로(create_animation)로 JSON을 넣어도 장면이 만들어짐
    def test_create_animation(self):
        result = create_animation(json.dumps(SCENE_FORMATS['cpu_schedule']))
        self.assertEqual(result['animation']['kind'], 'cpu_schedule')
        self.assertEqual(result['request']['algorithm'], SCENE_FORMATS['cpu_schedule']['algorithm'])


class ValidationTests(unittest.TestCase):
    # 검증 목적: 12칸 격자에 못 그리는 입력, 필요한 값이 빠진 입력, 이름 중복은 그림을 지어내지 않고 거부
    def test_rejects_invalid_requests(self):
        cases = {
            '12': ('fcfs', procs(('P1', 0, 6), ('P2', 0, 6), ('P3', 0, 1)), None),
            'quantum': ('rr', procs(('P1', 0, 2)), None),
            'priority': ('priority', procs(('P1', 0, 2), ('P2', 0, 1)), None),
            '중복': ('fcfs', procs(('P1', 0, 1), ('P1', 1, 1)), None),
        }
        for message, (algorithm, processes, quantum) in cases.items():
            with self.assertRaises(ValidationError, msg=message) as caught:
                run(algorithm, processes, quantum)
            self.assertIn(message, str(caught.exception))

    # 검증 목적: 유휴 시간까지 더한 총 길이로 칸 수를 셈(실행 합 9 + 첫 도착 전 유휴 4 = 13)
    def test_total_counts_idle(self):
        with self.assertRaises(ValidationError):
            run('fcfs', procs(('P1', 4, 6), ('P2', 4, 3)))

    # 검증 목적: 범위를 벗어난 값(이름 P6, 실행 0, 할당량 5)은 스키마가 거부
    def test_field_ranges(self):
        for processes, quantum in ((procs(('P6', 0, 1)), 2), (procs(('P1', 0, 0)), 2), (procs(('P1', 0, 1)), 5)):
            with self.assertRaises(ValidationError):
                run('rr', processes, quantum)


class SceneChoiceTests(unittest.TestCase):
    # 검증 목적: 막힘 보충 장면 선택(scene_schema·scene_formats·check_limits)에 cpu_schedule이 들어가고,
    # 개념별 제한(예: round-robin → rr)이 schema enum·형식 예시·응답 검증에 모두 적용되는지 확인
    # - OS 개념 ID와 제한표 줄은 개념 지도와 함께 Task 3에서 들어오므로 여기서는 kinds·limits를 직접 넘김
    def test_scene_choice_path(self):
        schema = scene_schema(('cpu_schedule',), {'algorithm': ('rr',)})
        (option,) = schema['properties']['scene']['anyOf']
        self.assertEqual(option['properties']['algorithm']['enum'], ['rr'])
        for keyword in ('$ref', 'discriminator', 'oneOf'):
            self.assertNotIn(keyword, json.dumps(schema))
        for limits in ({'algorithm': ('rr',)}, {'algorithm': ('priority',)}, {'algorithm': ('fcfs', 'sjf')}):
            example = scene_formats(('cpu_schedule',), limits)['cpu_schedule']
            self.assertEqual(example['algorithm'], limits['algorithm'][0])
            request = AnimationRequestModel.model_validate(example).root
            check_limits(request, limits)
            simulate(request)
        fcfs = AnimationRequestModel.model_validate(dict(SCENE_FORMATS['cpu_schedule'], algorithm='fcfs')).root
        with self.assertRaises(ValueError):
            check_limits(fcfs, {'algorithm': ('rr',)})


if __name__ == '__main__':
    unittest.main()
