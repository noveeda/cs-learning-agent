## @file
# @brief 프로세스 상태 전이(process_state)와 스케줄링 지표(schedule_metrics) 장면이 손으로 계산한 상태·지표를 그리는지,
# 실행할 수 없는 사건·그릴 수 없는 입력을 거부하는지 검증
# - 기대값은 시뮬레이터를 거치지 않고 손으로 계산해 적음: 코드와 같은 실수를 테스트가 따라 하지 않게 하기 위함
import unittest

from pydantic import ValidationError

from serving.animation import SCENE_FORMATS, SIMULATORS, simulate
from serving.animation.model import AnimationRequestModel


def run(request):
    return simulate(AnimationRequestModel.model_validate(request).root)


## @brief 처음 준비 큐(ready)를 도착 사건으로 만든 뒤 사건(events)을 이어 붙여 실행
def states(ready, *events):
    return run({'kind': 'process_state', 'ops': [{'op': 'arrive', 'name': name} for name in ready] + list(events)})


## @brief 프레임에서 상태별 프로세스를 읽음. 행(y)이 상태, 같은 행은 x 순서(준비 큐는 맨 앞이 왼쪽)
def board(frame):
    rows = {'ready': 0, 'running': 2, 'waiting': 4, 'terminated': 6}
    nodes = [b for b in frame.boxes if b.shape == 'node']
    return {state: [b.label for b in sorted((b for b in nodes if b.y == y), key=lambda b: b.x)]
            for state, y in rows.items()}


## @brief 지표 장면의 표 한 줄(프로세스 이름으로 시작하는 행)을 {이름: (반환, 대기, 응답)}으로 읽음
def metrics(frame):
    labels = {}
    for b in frame.boxes:
        if b.shape == 'label' and b.y >= 3:
            labels.setdefault(b.y, []).append((b.x, b.label))
    table = {}
    for row in labels.values():
        cells = [label for _, label in sorted(row)]
        if cells[0].startswith('P') and len(cells) == 4:
            table[cells[0]] = tuple(cell.split()[1] for cell in cells[1:])
    return table


class ProcessStateTests(unittest.TestCase):
    # 검증 목적: 준비 → 실행 → 대기 → 준비 → 실행 → 종료의 전이를 사건마다 한 프레임으로, 준비 큐는 맨 뒤에 넣음
    def test_io_cycle(self):
        animation = states(['P1', 'P2'], {'op': 'dispatch'}, {'op': 'io_request'}, {'op': 'dispatch'},
                           {'op': 'io_done', 'name': 'P1'}, {'op': 'exit'}, {'op': 'dispatch'}, {'op': 'exit'})
        self.assertEqual(animation.kind, 'process_state')
        # 빈 상태 1장 + 도착 2장 + 사건 7장
        self.assertEqual(len(animation.frames), 10)
        self.assertEqual(board(animation.frames[0]), {'ready': [], 'running': [], 'waiting': [], 'terminated': []})
        expected = [
            (['P1', 'P2'], [], [], []),
            (['P2'], ['P1'], [], []),
            (['P2'], [], ['P1'], []),
            ([], ['P2'], ['P1'], []),
            (['P1'], ['P2'], [], []),
            (['P1'], [], [], ['P2']),
            ([], ['P1'], [], ['P2']),
            ([], [], [], ['P2', 'P1']),
        ]
        for frame, (ready, running, waiting, done) in zip(animation.frames[2:], expected, strict=True):
            self.assertEqual(board(frame), {'ready': ready, 'running': running, 'waiting': waiting, 'terminated': done})

    # 검증 목적: 시간 만료는 실행 중 프로세스를 준비 큐 맨 뒤로 보내고, 새로 도착한 프로세스도 맨 뒤에 섬
    def test_timeout_and_arrival_go_to_back_of_ready_queue(self):
        animation = states(['P1'], {'op': 'arrive', 'name': 'P2'}, {'op': 'dispatch'}, {'op': 'arrive', 'name': 'P3'},
                           {'op': 'timeout'})
        self.assertEqual(board(animation.frames[2])['ready'], ['P1', 'P2'])
        self.assertEqual(board(animation.frames[4])['ready'], ['P2', 'P3'])
        self.assertEqual(board(animation.frames[5]), {'ready': ['P2', 'P3', 'P1'], 'running': [], 'waiting': [],
                                                      'terminated': []})

    # 검증 목적: 같은 프로세스는 프레임이 바뀌어도 같은 id라 재생기가 상태 사이 이동으로 그리고, 전이는 화살표로 보임
    def test_process_keeps_box_id_and_transition_arrow(self):
        animation = states(['P1'], {'op': 'dispatch'}, {'op': 'io_request'})
        ids = [{b.id for b in frame.boxes if b.shape == 'node'} for frame in animation.frames]
        self.assertEqual(ids, [set(), {'P1'}, {'P1'}, {'P1'}])
        # 도착은 출발 상태가 없어 화살표 없이 new로 강조
        self.assertEqual(animation.frames[1].arrows, [])
        self.assertEqual(next(b for b in animation.frames[1].boxes if b.id == 'P1').tone, 'new')
        arrow = animation.frames[3].arrows[0]
        self.assertEqual((arrow.source, arrow.target, arrow.label), ('Lrunning', 'P1', '입출력'))
        moved = next(b for b in animation.frames[3].boxes if b.id == 'P1')
        self.assertEqual(moved.tone, 'active')

    # 검증 목적: 실행할 수 없는 사건은 상태를 지어내지 않고 이유를 담은 한국어 오류로 거부
    def test_rejects_impossible_events(self):
        cases = [
            (['P1', 'P2'], [{'op': 'dispatch'}, {'op': 'dispatch'}], '이미'),
            ([], [{'op': 'dispatch'}], '비어'),
            (['P1'], [{'op': 'timeout'}], '실행 중'),
            (['P1'], [{'op': 'io_request'}], '실행 중'),
            (['P1'], [{'op': 'exit'}], '실행 중'),
            (['P1'], [{'op': 'io_done', 'name': 'P1'}], '대기'),
            (['P1'], [{'op': 'arrive', 'name': 'P1'}], '이미'),
        ]
        for ready, events, message in cases:
            with self.subTest(events=events), self.assertRaisesRegex(ValueError, message):
                states(ready, *events)

    # 검증 목적: 빈 사건·사건 수 초과·없는 이름·모르는 사건·이름 빠진 사건처럼 그릴 수 없는 입력은 스키마에서 거부
    def test_rejects_invalid_requests(self):
        bad = [
            {'ops': []},
            {'ops': [{'op': 'arrive', 'name': 'P1'}] + [{'op': 'dispatch'}] * 10},
            {'ops': [{'op': 'arrive', 'name': 'P9'}]},
            {'ops': [{'op': 'sleep'}]},
            {'ops': [{'op': 'io_done'}]},
            {'ops': [{'op': 'dispatch', 'name': 'P1'}]},
        ]
        for request in bad:
            with self.subTest(request=request), self.assertRaises(ValidationError):
                AnimationRequestModel.model_validate({'kind': 'process_state', **request})


class ScheduleMetricsTests(unittest.TestCase):
    # 검증 목적: 비선점 FCFS는 반환 = 완료 - 도착, 대기 = 반환 - 실행, 응답 = 첫 실행 - 도착이고 대기와 응답이 같음
    def test_fcfs_metrics(self):
        animation = run({'kind': 'schedule_metrics', 'algorithm': 'fcfs', 'processes': [
            {'name': 'P1', 'arrival': 0, 'burst': 3}, {'name': 'P2', 'arrival': 1, 'burst': 2},
            {'name': 'P3', 'arrival': 2, 'burst': 1}]})
        self.assertEqual(animation.kind, 'schedule_metrics')
        # 간트 1장 + 프로세스마다 1장 + 평균 1장
        self.assertEqual(len(animation.frames), 5)
        self.assertEqual(metrics(animation.frames[-1]), {'P1': ('3', '0', '0'), 'P2': ('4', '2', '2'),
                                                         'P3': ('4', '3', '3')})
        self.assertIn('대기 1.67', animation.frames[-1].caption)
        # 첫 프레임은 지표 없이 간트 차트만: 표가 한 줄씩 채워지는 순서를 보여 주기 위함
        self.assertEqual(metrics(animation.frames[0]), {})
        self.assertEqual(list(metrics(animation.frames[2])), ['P1', 'P2'])

    # 검증 목적: RR은 첫 할당량에 바로 실행돼도 나중에 다시 기다리므로 응답 시간이 대기 시간보다 짧을 수 있음
    def test_rr_response_differs_from_waiting(self):
        animation = run({'kind': 'schedule_metrics', 'algorithm': 'rr', 'quantum': 2, 'processes': [
            {'name': 'P1', 'arrival': 0, 'burst': 3}, {'name': 'P2', 'arrival': 0, 'burst': 2},
            {'name': 'P3', 'arrival': 0, 'burst': 1}]})
        self.assertEqual(metrics(animation.frames[-1]), {'P1': ('6', '3', '0'), 'P2': ('4', '2', '2'),
                                                         'P3': ('5', '4', '4')})
        # 프로세스 차례의 프레임은 그 프로세스가 실행된 간트 칸만 강조
        p1 = [b.x for b in animation.frames[1].boxes if b.y == 1 and b.tone == 'active']
        self.assertEqual(p1, [0, 1, 5])

    # 검증 목적: 표가 격자 안에 들도록 프로세스는 4개까지, rr의 quantum 누락 같은 기존 스케줄 규칙도 그대로 거부
    def test_rejects_invalid_requests(self):
        five = [{'name': f'P{i}', 'arrival': 0, 'burst': 1} for i in range(1, 6)]
        bad = [
            {'algorithm': 'fcfs', 'processes': five},
            {'algorithm': 'rr', 'processes': five[:2]},
            {'algorithm': 'fcfs', 'processes': [{'name': 'P1', 'arrival': 0, 'burst': 6},
                                                {'name': 'P2', 'arrival': 0, 'burst': 6},
                                                {'name': 'P3', 'arrival': 0, 'burst': 1}]},
        ]
        for request in bad:
            with self.subTest(request=request), self.assertRaises(ValidationError):
                AnimationRequestModel.model_validate({'kind': 'schedule_metrics', **request})


class RegistrationTests(unittest.TestCase):
    # 검증 목적: 새 장면 종류가 시뮬레이터·형식 예시에 등록되고 형식 예시가 그대로 실행됨
    def test_registered_with_runnable_formats(self):
        for kind in ('process_state', 'schedule_metrics'):
            with self.subTest(kind=kind):
                self.assertIn(kind, SIMULATORS)
                self.assertEqual(run(SCENE_FORMATS[kind]).kind, kind)


if __name__ == '__main__':
    unittest.main()
