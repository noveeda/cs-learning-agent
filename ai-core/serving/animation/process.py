## @file
# @brief 운영체제 장면 두 종류의 요청 스키마와 시뮬레이터
# - process_state: 준비·실행·대기·종료 상태판에서 사건(디스패치·시간 만료·입출력 요청·입출력 완료·종료·도착)마다 프로세스를 옮김
# - schedule_metrics: 간트 차트를 실제로 계산(scheduling.schedule)한 뒤 프로세스마다 반환·대기·응답 시간을 표에 채움
# - 막힘 보충의 개념별 예시 장면(content/os/scenes.json)이 쓰는 장면. 상태·지표는 코드가 계산하고 모델이 지어내지 않음
"""Process state transition and scheduling metric simulators."""
from typing import Annotated, Literal, Union

from pydantic import Field

from .model import Animation, Arrow, Box, CpuScheduleRequest, Frame, Proc, Strict, build_animation, josa
from .scheduling import ALGORITHMS, schedule

# 프로세스 이름: 스케줄링 장면(Proc)과 같은 P1~P5
Name = Literal['P1', 'P2', 'P3', 'P4', 'P5']


## @brief 인자 없는 사건. 실행 중 프로세스(디스패치는 준비 큐 맨 앞)에 적용
class StateOp(Strict):
    op: Literal['dispatch', 'timeout', 'io_request', 'exit']


## @brief 이름을 지정하는 사건. io_done은 대기 중인 프로세스를, arrive는 새 프로세스를 준비 큐 맨 뒤에 넣음
class NamedStateOp(Strict):
    op: Literal['io_done', 'arrive']
    name: Name


## @brief 프로세스 상태 전이 장면. 프로세스가 하나도 없고 CPU가 비어 있는 상태에서 시작해 사건을 차례로 실행
# - 처음 준비 큐도 arrive 사건으로 만듦: 새 프로세스가 준비 상태로 들어오는 것도 상태 전이이고, 채팅 도구의 기존 필드(ops·name)만 쓰기 위함
# - 사건 10개(프레임 11장)는 한 장면을 이전·다음 버튼으로 따라갈 수 있는 교육적 분량 제한
class ProcessStateRequest(Strict):
    kind: Literal['process_state']
    ops: list[Annotated[Union[StateOp, NamedStateOp], Field(discriminator='op')]] = Field(min_length=1, max_length=10)


## @brief 스케줄링 지표 장면. 입력 규칙은 cpu_schedule과 같고, 표가 격자(y=3~6)에 들도록 프로세스는 4개까지
class ScheduleMetricsRequest(CpuScheduleRequest):
    kind: Literal['schedule_metrics']
    processes: list[Proc] = Field(min_length=1, max_length=4)


# 상태판 행(y)과 이름. 실행 행은 CPU가 하나라 한 칸만 씀
ROWS = {'ready': (0, '준비 큐'), 'running': (2, '실행'), 'waiting': (4, '대기'), 'terminated': (6, '종료')}

# 사건 → (출발 상태, 도착 상태, 화살표 이름, 설명). 출발 상태가 None이면 새로 들어오는 프로세스
EVENTS = {
    'dispatch': ('ready', 'running', '디스패치', '준비 → 실행'),
    'timeout': ('running', 'ready', '시간 만료', '실행 → 준비 큐 맨 뒤'),
    'io_request': ('running', 'waiting', '입출력', '실행 → 대기'),
    'io_done': ('waiting', 'ready', '입출력 끝', '대기 → 준비 큐 맨 뒤'),
    'exit': ('running', 'terminated', '종료', '실행 → 종료'),
    'arrive': (None, 'ready', '도착', '도착 → 준비 큐 맨 뒤'),
}


## @brief 상태판 한 장을 그림. 상태 이름은 x=0, 프로세스는 x=2부터 순서대로(준비 큐는 맨 앞이 왼쪽)
# - moved: 이번 사건으로 옮겨진 프로세스(active, 새로 온 것은 new)
# - source·arrow: 전이 화살표가 시작하는 상태 이름 상자와 화살표 이름. 새로 도착한 프로세스는 출발 상태가 없어 화살표 없음
# - CPU가 비면 실행 칸에 '비어 있음'을 적어 대기 중에도 CPU를 못 쓰는 상황이 보이게 함
def state_frame(board, caption, moved=None, source=None, arrow=None) -> Frame:
    boxes = [Box(id=f'L{state}', label=title, x=0, y=y, shape='label') for state, (y, title) in ROWS.items()]
    for state, (y, _) in ROWS.items():
        for x, name in enumerate(board[state], start=2):
            tone = ('active' if source else 'new') if name == moved else 'normal'
            boxes.append(Box(id=name, label=name, x=x, y=y, shape='node', tone=tone))
    if not board['running']:
        boxes.append(Box(id='idle', label='비어 있음', x=2, y=ROWS['running'][0], shape='label'))
    arrows = [Arrow(source=f'L{source}', target=moved, label=arrow)] if source else []
    return Frame(boxes=boxes, arrows=arrows, caption=caption)


## @brief 사건을 차례로 실행하며 상태판을 옮김. 실행할 수 없는 사건은 이유를 담아 ValueError
def simulate_process_state(req: ProcessStateRequest) -> Animation:
    board = {'ready': [], 'running': [], 'waiting': [], 'terminated': []}
    frames = [state_frame(board, '시작: 프로세스 없음, CPU 비어 있음')]
    for event in req.ops:
        start, end, arrow, change = EVENTS[event.op]
        if event.op == 'dispatch':
            if board['running']:
                running = board['running'][0]
                raise ValueError(f'dispatch: 이미 {running}{josa(int(running[1]), "이", "가")} 실행 중입니다')
            if not board['ready']:
                raise ValueError('dispatch: 준비 큐가 비어 있습니다')
            name = board['ready'].pop(0)
        elif event.op in ('timeout', 'io_request', 'exit'):
            if not board['running']:
                raise ValueError(f'{event.op}: 실행 중인 프로세스가 없습니다')
            name = board['running'].pop()
        elif event.op == 'io_done':
            name = event.name
            if name not in board['waiting']:
                raise ValueError(f'io_done: {name}{josa(int(name[1]), "은", "는")} 대기 상태가 아닙니다')
            board['waiting'].remove(name)
        else:
            name = event.name
            if any(name in names for names in board.values()):
                raise ValueError(f'arrive: {name}{josa(int(name[1]), "은", "는")} 이미 있는 프로세스입니다')
        board[end].append(name)
        frames.append(state_frame(board, f'{name} {change}', moved=name, source=start, arrow=arrow))
    return build_animation('process_state', '프로세스 상태 전이', frames)


## @brief 지표 장면 한 장. 간트(y=0 눈금, y=1 칸) 아래에 지금까지 계산한 프로세스 표와 평균 줄을 그림
# - focus: 이번에 계산하는 프로세스. 그 프로세스가 실행된 간트 칸과 도착 시각 눈금을 강조
# - 표 한 줄: x=0 이름, x=2 반환, x=5 대기, x=8 응답(라벨이 옆 칸과 겹치지 않게 세 칸 간격)
def metrics_frame(cells, total, rows, caption, focus=None, arrival=None, average=None) -> Frame:
    boxes = [Box(id=f't{t}', label=str(t), x=t, y=0, shape='label', tone='active' if t == arrival else 'normal')
             for t in range(total)]
    boxes += [Box(id=f'g{t}', label=label, x=t, y=1, shape='cell', tone='active' if label == focus else 'normal')
              for t, label in enumerate(cells)]
    for y, (name, values) in enumerate(rows, start=3):
        tone = 'new' if name == focus else 'normal'
        boxes.append(Box(id=f'r{name}', label=name, x=0, y=y, shape='label', tone=tone))
        boxes += [Box(id=f'{key}{name}', label=f'{title} {value}', x=x, y=y, shape='label', tone=tone)
                  for x, key, title, value in zip((2, 5, 8), 'TWR', ('반환', '대기', '응답'), values)]
    if average:
        boxes.append(Box(id='avg', label='평균', x=0, y=7, shape='label', tone='new'))
        boxes += [Box(id=f'avg{key}', label=f'{title} {value}', x=x, y=7, shape='label', tone='new')
                  for x, key, title, value in zip((2, 5, 8), 'TWR', ('반환', '대기', '응답'), average)]
    return Frame(boxes=boxes, arrows=[], caption=caption)


## @brief 스케줄을 계산하고 프로세스마다 반환·대기·응답 시간을 한 장씩 채운 뒤 평균을 보여 줌
# - 반환 = 완료 - 도착, 대기 = 반환 - 실행(준비 큐에서 기다린 시간의 합), 응답 = 처음 CPU를 받은 시각 - 도착
# - 비선점 알고리즘은 한 번 시작하면 끝까지 실행해 대기와 응답이 같고, RR은 다시 기다리는 시간이 있어 달라질 수 있음
def simulate_schedule_metrics(req: ScheduleMetricsRequest) -> Animation:
    segments = schedule(req)
    total = segments[-1][2]
    cells, first, done = [], {}, {}
    for proc, start, end, _, finished in segments:
        cells += [proc.name if proc else '-'] * (end - start)
        if proc:
            first.setdefault(proc.name, start)
            if finished:
                done[proc.name] = end
    title = ALGORITHMS[req.algorithm] + (f'(q={req.quantum})' if req.algorithm == 'rr' else '') + ' 지표'
    frames = [metrics_frame(cells, total, [], f'간트 차트: 끝 시각 {total}, 프로세스별 지표를 계산합니다')]
    rows, sums = [], [0, 0, 0]
    for proc in sorted(req.processes, key=lambda p: p.name):
        turnaround = done[proc.name] - proc.arrival
        waiting = turnaround - proc.burst
        response = first[proc.name] - proc.arrival
        rows.append((proc.name, (turnaround, waiting, response)))
        sums = [s + v for s, v in zip(sums, (turnaround, waiting, response))]
        caption = (f'{proc.name}: 반환 {done[proc.name]}-{proc.arrival}={turnaround}, '
                   f'대기 {turnaround}-{proc.burst}={waiting}, 응답 {first[proc.name]}-{proc.arrival}={response}')
        frames.append(metrics_frame(cells, total, rows, caption, focus=proc.name, arrival=proc.arrival))
    average = [f'{s / len(rows):.2f}' for s in sums]
    frames.append(metrics_frame(cells, total, rows, f'평균: 반환 {average[0]}, 대기 {average[1]}, 응답 {average[2]}',
                                average=average))
    return build_animation('schedule_metrics', title, frames)
