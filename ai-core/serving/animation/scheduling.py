# 파일 역할: CPU 스케줄링 장면 요청(FCFS·SJF·우선순위·RR)을 실제로 실행하며 간트 차트 프레임을 만드는 시뮬레이터
# - 화면 배치: y=0 시간 눈금, y=1 간트 칸(프로세스 이름, 유휴는 "-"), y=3·4 준비 큐, y=6 프로세스별 대기 시간
# - 시간 단위마다 프레임을 만들면 총 12칸 + 처음·끝 프레임이 MAX_FRAMES(16) 안이지만, 학생이 같은 장면을 반복해 넘기지 않게
#   한 번 꺼내 실행한 구간(RR은 할당량 한 번)마다 프레임 하나로 묶음
# - 동점 규칙(MVP 4 계획 Review Focus 2): 도착 시각 → 프로세스 이름 순
#   RR에서 할당량이 끝난 시각에 새로 도착한 프로세스가 있으면 새 도착을 먼저 큐에 넣음(교과서 관례)
"""CPU scheduling Gantt chart simulator."""
from .graph import fit
from .model import Animation, Box, CpuScheduleRequest, Frame, build_animation, josa

ALGORITHMS = {'fcfs': 'FCFS 스케줄링', 'sjf': 'SJF 스케줄링(비선점)', 'rr': '라운드 로빈 스케줄링',
              'priority': '우선순위 스케줄링(비선점)'}

# 알고리즘별로 준비 큐에서 다음 프로세스를 고르는 기준. 없으면 큐 맨 앞(FCFS·RR)
# - 기준이 같으면 도착 시각 → 이름 순으로 고름
KEYS = {'sjf': lambda p: (p.burst, p.arrival, p.name), 'priority': lambda p: (p.priority, p.arrival, p.name)}


# 역할: 스케줄을 계산해 실행 구간 목록을 반환. 구간 = (프로세스 또는 None(유휴), 시작, 끝, 꺼낸 뒤 준비 큐, 끝났는지)
# - 비선점(FCFS·SJF·우선순위)은 남은 시간을 한 번에, RR은 quantum만큼만 실행
# - 준비 큐가 비면 다음 도착까지 유휴 구간을 넣음
def schedule(req: CpuScheduleRequest) -> list[tuple]:
    pending = sorted(req.processes, key=lambda p: (p.arrival, p.name))
    left = {p.name: p.burst for p in req.processes}
    ready, segments, time = [], [], 0

    # 도착 시각이 now 이하인 프로세스를 도착·이름 순으로 큐 뒤에 넣음
    def admit(now):
        while pending and pending[0].arrival <= now:
            ready.append(pending.pop(0))

    admit(0)
    while ready or pending:
        if not ready:
            segments.append((None, time, pending[0].arrival, [], False))
            time = pending[0].arrival
            admit(time)
            continue
        key = KEYS.get(req.algorithm)
        proc = min(ready, key=key) if key else ready[0]
        ready.remove(proc)
        queue = sorted(ready, key=key) if key else list(ready)
        run = min(req.quantum, left[proc.name]) if req.algorithm == 'rr' else left[proc.name]
        start, time = time, time + run
        left[proc.name] -= run
        # 새 도착을 먼저 넣고 나서 할당량이 끝난 프로세스를 큐 뒤로 보냄(RR 동점 규칙)
        admit(time)
        if left[proc.name]:
            ready.append(proc)
        segments.append((proc, start, time, queue, not left[proc.name]))
    return segments


# 역할: 한 프레임을 그림. cells는 지금까지 채운 간트 칸, active는 이번 구간의 칸 번호, waits는 끝난 프로세스의 대기 시간
def gantt_frame(req, total, cells, queue, waits, caption, active=()) -> Frame:
    boxes = [Box(id=f't{t}', label=str(t), x=t, y=0, shape='label') for t in range(total)]
    boxes += [Box(id=f'g{t}', label=label, x=t, y=1, shape='cell', tone='active' if t in active else 'normal')
              for t, label in enumerate(cells)]
    boxes.append(Box(id='ready', label='준비 큐', x=0, y=3, shape='label'))
    boxes += [Box(id=f'q{p.name}', label=p.name, x=x, y=4, shape='slot') for x, p in enumerate(queue)]
    # 대기 시간 라벨은 두 칸 간격: 'P1 대기 4'가 옆 라벨과 겹치지 않게 하기 위함
    names = sorted(p.name for p in req.processes)
    boxes += [Box(id=f'w{name}', label=f'{name} 대기 {waits.get(name, "?")}', x=2 * i + 1, y=6, shape='label')
              for i, name in enumerate(names)]
    return Frame(boxes=boxes, arrows=[], caption=caption)


# 역할: 구간 하나의 설명. 왜 이 프로세스를 골랐는지(알고리즘별 기준)와 끝났는지를 적음
def describe(req, proc, start, end, done, wait) -> str:
    span = f'{start}~{end}'
    if proc is None:
        return f'{span}: 준비 큐가 비어 CPU가 쉽니다(유휴)'
    name = f'{proc.name}{josa(int(proc.name[1]), "을", "를")}'
    reason = {'fcfs': f'먼저 온 순서로 준비 큐 맨 앞 {name} 실행',
              'sjf': f'준비된 것 중 실행 시간이 가장 짧은 {proc.name}({proc.burst}) 실행',
              'priority': f'준비된 것 중 우선순위 숫자가 가장 작은 {proc.name}({proc.priority}) 실행',
              'rr': f'큐 맨 앞 {name} {end - start}만큼 실행'}[req.algorithm]
    if done:
        return f'{span}: {reason}, 끝남(대기 {wait})'
    return f'{span}: {reason}, 남은 일은 큐 뒤로'


# 역할: CPU 스케줄링 장면 실행. 첫 프레임은 입력 소개, 구간마다 한 프레임, 마지막 프레임은 평균 대기 시간
# - 대기 시간 = 끝난 시각 - 도착 시각 - 실행 시간(준비 큐에서 기다린 시간의 합)
def simulate_cpu_schedule(req: CpuScheduleRequest) -> Animation:
    segments = schedule(req)
    total = segments[-1][2]
    title = ALGORITHMS[req.algorithm] + (f'(q={req.quantum})' if req.algorithm == 'rr' else '')
    fields = '도착, 실행, 우선순위' if req.algorithm == 'priority' else '도착, 실행'
    shown = ' '.join(f'{p.name}({p.arrival},{p.burst}' + (f',{p.priority})' if req.algorithm == 'priority' else ')')
                     for p in sorted(req.processes, key=lambda p: (p.arrival, p.name)))
    intro = fit(f'{title} 시작. 프로세스({fields}): {shown}', f'{title} 시작: 프로세스 {len(req.processes)}개')
    frames = [gantt_frame(req, total, [], [], {}, intro)]
    cells, waits = [], {}
    for proc, start, end, queue, done in segments:
        cells += [proc.name if proc else '-'] * (end - start)
        if done:
            waits[proc.name] = end - proc.arrival - proc.burst
        caption = describe(req, proc, start, end, done, waits.get(proc.name) if proc else None)
        frames.append(gantt_frame(req, total, cells, queue, waits, caption, range(start, end)))
    ordered = [waits[name] for name in sorted(waits)]
    average = sum(ordered) / len(ordered)
    summary = f'끝: 평균 대기 시간 = ({"+".join(map(str, ordered))})/{len(ordered)} = {average:.2f}'
    frames.append(gantt_frame(req, total, cells, [], waits, summary))
    return build_animation('cpu_schedule', title, frames)
