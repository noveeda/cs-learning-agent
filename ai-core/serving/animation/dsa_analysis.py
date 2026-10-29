## @file
# @brief 자료구조·알고리즘(dsa) 과목의 분석 계열 장면: 로그(절반·두 배), 증가 속도 표, 반복 횟수, 재귀 트리, 재귀 호출, 피보나치 DP
# - 요청 모델과 시뮬레이터를 한 파일에 둠. model.py는 REQUESTS를 합집합에, __init__.py는 SIMULATORS·FORMATS를 등록 표에 한 줄로 합침
# - 횟수·합계·복잡도 문구는 모두 코드가 실제로 세거나 계산한 값으로 만듦(모델이 숫자를 지어내지 않음)
# - 입력 범위는 격자(12×8)·상자 개수(40)·MAX_FRAMES(16) 안에 장면 전체를 생략 없이 그릴 수 있는 크기로 제한
"""DSA logarithm, growth, loop count, recursion tree, recursion and Fibonacci DP scenes."""
from typing import Literal

from pydantic import Field, model_validator

from .call_stack import stack_frame
from .dsa_structure import tidy_layout
from .model import GRID_HEIGHT, Animation, Arrow, Box, Frame, Strict, build_animation


## @brief 2의 거듭제곱인지 확인
def is_power_of_two(n: int) -> bool:
    return n > 0 and n & (n - 1) == 0


# --- 장면 요청 ---

## @brief 로그 장면. halve는 n을 1이 될 때까지 절반으로, double은 1을 n이 될 때까지 두 배로(로그와 지수는 역관계)
# - n은 2의 거듭제곱 2~32: 칸을 8개씩 4줄(32칸)까지 그리고 단계 수가 정수 log₂n이 되게 함
class LogStepsRequest(Strict):
    kind: Literal['log_steps']
    mode: Literal['halve', 'double']
    n: int = Field(ge=2, le=32)

    ## @brief log₂n이 정수가 아닌 n을 거부
    @model_validator(mode='after')
    def validate_power(self):
        if not is_power_of_two(self.n):
            raise ValueError('n은 2의 거듭제곱(2, 4, 8, 16, 32)이어야 합니다')
        return self


# 증가 속도 표에 쓸 수 있는 함수식 → 계산. log는 밑이 2
GROWTH_FUNCTIONS = {
    '1': lambda n: 1,
    'log n': lambda n: n.bit_length() - 1,
    'n': lambda n: n,
    '3n': lambda n: 3 * n,
    'n log n': lambda n: n * (n.bit_length() - 1),
    'n²': lambda n: n * n,
    'n²+n': lambda n: n * n + n,
}


## @brief 증가 속도 표 장면. 함수식(행)마다 입력 크기(열)별 연산량을 계산해 한 열씩 채움
# - 칸 값은 세 자리(999) 이하, log가 들어가면 n은 2의 거듭제곱(값이 정수여야 칸에 정확히 적을 수 있음)
class GrowthRequest(Strict):
    kind: Literal['growth']
    functions: list[Literal['1', 'log n', 'n', '3n', 'n log n', 'n²', 'n²+n']] = Field(min_length=2, max_length=5)
    sizes: list[int] = Field(min_length=2, max_length=5)

    ## @brief 함수식 중복, 입력 크기 범위(1~30)·오름차순, log의 정수 조건, 칸 값 상한을 확인
    @model_validator(mode='after')
    def validate_table(self):
        if len(set(self.functions)) != len(self.functions):
            raise ValueError('함수식이 중복되었습니다')
        if any(not 1 <= n <= 30 for n in self.sizes) or any(a >= b for a, b in zip(self.sizes, self.sizes[1:])):
            raise ValueError('sizes는 1~30의 오름차순 정수여야 합니다')
        if any('log' in f for f in self.functions) and not all(is_power_of_two(n) for n in self.sizes):
            raise ValueError('log가 들어간 표의 sizes는 2의 거듭제곱이어야 합니다')
        for f in self.functions:
            for n in self.sizes:
                if GROWTH_FUNCTIONS[f](n) > 999:
                    raise ValueError(f'{f}의 n={n} 값이 999를 넘어 칸에 적을 수 없습니다')
        return self


## @brief 반복 횟수 장면. nested는 i·j 모두 0..n-1(n×n번), triangle은 j가 i..n-1(n(n+1)/2번)
class LoopCountRequest(Strict):
    kind: Literal['loop_count']
    pattern: Literal['nested', 'triangle']
    n: int = Field(ge=2, le=6)


## @brief 재귀 트리 장면. T(n) = branch·T(n/2) + work를 크기 1까지 펼쳐 단계별 일의 합을 더함
# - n은 2·4·8: 가지 2개일 때 리프 8개가 가로 격자에 들어가는 크기
class RecursionTreeRequest(Strict):
    kind: Literal['recursion_tree']
    branch: Literal[1, 2]
    work: Literal['n', '1']
    n: Literal[2, 4, 8]


## @brief 재귀 호출 장면. sum은 sum(n) = n + sum(n-1), sum(0) = 0. no_base는 기저 사례 없이 count(n-1)을 계속 부름
# - sum은 n=5에서 13단계(호출·반환 각 n+1번 + 초기)로 MAX_FRAMES 안
class RecursionRequest(Strict):
    kind: Literal['dsa_recursion']
    function: Literal['sum', 'no_base']
    n: int = Field(ge=0, le=5)


## @brief 피보나치 동적 계획법 장면. naive(단순 재귀 호출 트리), memo(메모이제이션), table(아래에서 위로 표 채우기)
# - 교육적 분량 제한: naive는 n≤5(호출 15번), memo는 n≤6(호출 11번), table은 n≤10(칸 11개)
class FibDpRequest(Strict):
    kind: Literal['fib_dp']
    mode: Literal['naive', 'memo', 'table']
    n: int = Field(ge=1, le=10)

    ## @brief 방식별 n 상한 확인: 모든 호출·칸을 생략 없이 그리기 위한 분량 제한
    @model_validator(mode='after')
    def validate_size(self):
        limit = {'naive': 5, 'memo': 6, 'table': 10}[self.mode]
        if self.n > limit:
            raise ValueError(f'{self.mode}는 n={limit}까지만 지원합니다. 모든 호출을 한 장면에 그리기 위한 분량 제한입니다')
        return self


REQUESTS = (LogStepsRequest, GrowthRequest, LoopCountRequest, RecursionTreeRequest, RecursionRequest, FibDpRequest)


# --- 시뮬레이터 ---

# 로그 장면 칸 배치: 한 줄 8칸, 횟수 이름표는 오른쪽(x=10)
LOG_ROW = 8


## @brief 로그 장면 실행. 칸 하나가 크기 1을 뜻하고, 한 단계마다 남은 칸을 절반으로 줄이거나(halve) 두 배로 늘림(double)
def simulate_log_steps(req: LogStepsRequest) -> Animation:
    steps = req.n.bit_length() - 1

    def frame(caption, tones, k):
        boxes = [Box(id=f'c{i}', label='', x=i % LOG_ROW, y=i // LOG_ROW, shape='cell', tone=tone) for i, tone in tones]
        boxes.append(Box(id='count', label=f'{k}회', x=10, y=0, shape='label'))
        return Frame(boxes=boxes, arrows=[], caption=caption)

    frames = []
    if req.mode == 'halve':
        frames.append(frame(f'n = {req.n}: 1이 될 때까지 절반으로 나눕니다', [(i, 'normal') for i in range(req.n)], 0))
        for k in range(1, steps + 1):
            before, after = req.n >> (k - 1), req.n >> k
            caption = f'{k}회: {before} → {after}' + (f', log₂{req.n} = {k}' if after == 1 else '')
            tones = [(i, 'active') for i in range(after)] + [(i, 'removed') for i in range(after, before)]
            frames.append(frame(caption, tones, k))
        return build_animation('log_steps', f'log₂{req.n}: 절반으로 나눈 횟수', frames)
    frames.append(frame('1에서 시작해 두 배씩 늘립니다', [(0, 'normal')], 0))
    for k in range(1, steps + 1):
        before, after = 1 << (k - 1), 1 << k
        caption = f'{k}회: {before} × 2 = {after}' + (f', 2^{k} = {req.n} ⇔ log₂{req.n} = {k}' if after == req.n else '')
        frames.append(frame(caption, [(i, 'normal') for i in range(before)] + [(i, 'new') for i in range(before, after)], k))
    return build_animation('log_steps', f'2^k = {req.n}: 두 배로 늘린 횟수', frames)


## @brief 증가 속도 표 장면 실행. 이름표 열(x=1)·크기 머리줄(y=0)을 두고 입력 크기마다 한 열씩 채움
# - 마지막 단계는 가장 큰 n에서의 크기 순서를 계산해 보여 주고, 가장 큰 행을 active로 강조
def simulate_growth(req: GrowthRequest) -> Animation:
    table = {f: [GROWTH_FUNCTIONS[f](n) for n in req.sizes] for f in req.functions}

    def frame(caption, columns, new_column=None, top=None):
        boxes = [Box(id='hn', label='n', x=1, y=0, shape='label')]
        for row, f in enumerate(req.functions, start=1):
            boxes.append(Box(id=f'f{row}', label=f, x=1, y=row, shape='label', tone='active' if f == top else 'normal'))
        for j in range(columns):
            boxes.append(Box(id=f'h{j}', label=str(req.sizes[j]), x=3 + j, y=0, shape='label'))
            for row, f in enumerate(req.functions, start=1):
                tone = 'new' if j == new_column else 'active' if f == top else 'normal'
                boxes.append(Box(id=f'v{row}_{j}', label=str(table[f][j]), x=3 + j, y=row, shape='cell', tone=tone))
        return Frame(boxes=boxes, arrows=[], caption=caption)

    frames = [frame('입력 크기 n이 커질 때 연산량 비교', 0)]
    for j, n in enumerate(req.sizes):
        frames.append(frame(f'n = {n}: ' + ', '.join(f'{f} = {table[f][j]}' for f in req.functions), j + 1, j))
    ranked = sorted(req.functions, key=lambda f: table[f][-1])
    order = ranked[0]
    for a, b in zip(ranked, ranked[1:]):
        order += (' = ' if table[a][-1] == table[b][-1] else ' < ') + b
    frames.append(frame(f'n = {req.sizes[-1]}에서 순서: {order}', len(req.sizes), top=ranked[-1]))
    return build_animation('growth', '증가 속도 비교', frames)


## @brief 반복 횟수 장면 실행. 안쪽 반복 한 번을 칸 하나로 보고, 바깥 반복 i 하나마다 그 줄을 채움
# - 초기 단계에 앞으로 채울 자리를 점선 slot으로 먼저 보여 줘 전체 횟수(넓이)를 짐작하게 함
def simulate_loop_count(req: LoopCountRequest) -> Animation:
    n = req.n
    spots = [(i, j) for i in range(n) for j in range(i if req.pattern == 'triangle' else 0, n)]

    def frame(caption, done_rows, total, row=None):
        boxes = [Box(id=f'c{i}_{j}', label='', x=j, y=i, shape='cell' if i < done_rows else 'slot',
                     tone='new' if i == row else 'normal') for i, j in spots]
        boxes.append(Box(id='count', label=f'{total}번', x=9, y=0, shape='label'))
        if row is not None:
            boxes.append(Box(id='i', label=f'i={row}', x=n, y=row, shape='label'))
        return Frame(boxes=boxes, arrows=[], caption=caption)

    inner = 'j = 0..n-1' if req.pattern == 'nested' else 'j = i..n-1'
    frames = [frame(f'n = {n}, 바깥 i = 0..n-1, 안쪽 {inner}: 칸 하나 = 연산 한 번', 0, 0)]
    total = 0
    for i in range(n):
        runs = n if req.pattern == 'nested' else n - i
        total += runs
        caption = f'i = {i}: 안쪽이 {runs}번 돌아 누적 {total}번'
        if i == n - 1:
            caption += (f' → 총 {n}×{n} = {total}번, O(n²)' if req.pattern == 'nested'
                        else f' → 총 n(n+1)/2 = {total}번, 상수·낮은 차수를 버리면 O(n²)')
        frames.append(frame(caption, i + 1, total, i))
    name = '중첩 반복' if req.pattern == 'nested' else '삼각형 중첩 반복'
    return build_animation('loop_count', f'{name}의 연산 횟수', frames)


# 재귀 트리 단계 합 이름표의 x: 리프 8개(x 0..7) 오른쪽에 한 칸 띄움
LEVEL_LABEL_X = 9


## @brief 재귀 트리 장면 실행. 깊이마다 한 단계씩 펼치고, 오른쪽 이름표에 "문제 수×일=단계 합"을 적은 뒤 마지막에 전체 합을 더함
def simulate_recursion_tree(req: RecursionTreeRequest) -> Animation:
    n, b = req.n, req.branch
    levels = n.bit_length()

    def work(size):
        return size if req.work == 'n' else 1

    def x_of(depth, j):
        # 가지 2개: 리프 n개를 0..n-1에 두고 각 노드를 자기 리프 구간의 가운데(반올림)에. 가지 1개: 가운데 한 줄
        return (2 * j + 1) * n // (2 ** (depth + 1)) if b == 2 else n // 2

    def frame(caption, shown, total=None):
        boxes, arrows = [], []
        for depth in range(shown):
            size, nodes = n >> depth, b ** depth
            for j in range(nodes):
                tone = 'new' if depth == shown - 1 and total is None else 'normal'
                boxes.append(Box(id=f'r{depth}_{j}', label=str(size), x=x_of(depth, j), y=depth, shape='node', tone=tone))
                if depth:
                    arrows.append(Arrow(source=f'r{depth - 1}_{j // b}', target=f'r{depth}_{j}', head=False))
            label = f'{nodes}×{work(size)}={nodes * work(size)}'
            boxes.append(Box(id=f'l{depth}', label=label, x=LEVEL_LABEL_X, y=depth, shape='label'))
        if total is not None:
            boxes.append(Box(id='total', label=f'합 {total}', x=LEVEL_LABEL_X, y=shown, shape='label', tone='new'))
        return Frame(boxes=boxes, arrows=arrows, caption=caption)

    frames, total = [], 0
    for depth in range(levels):
        size, nodes = n >> depth, b ** depth
        total += nodes * work(size)
        head = f'T({n}): 크기 {n} 문제 하나' if depth == 0 else f'깊이 {depth}: 크기 {size} 문제 {nodes}개'
        frames.append(frame(f'{head}, 이 단계 일 {nodes * work(size)}', depth + 1))
    if b == 2 and req.work == 'n':
        summary = f'단계 {levels}개 × 단계마다 {n} = {total} → O(n log n)'
    elif b == 1 and req.work == '1':
        summary = f'단계 {levels}개 × 단계마다 1 = {total} → O(log n)'
    elif b == 2:
        summary = f'1+2+…+{n} = {total} = 2n-1 → O(n)'
    else:
        summary = f'{n}+{n // 2}+…+1 = {total} < 2n → O(n)'
    frames.append(frame(summary, levels, total))
    recurrence = f'{"2T" if b == 2 else "T"}(n/2) + {req.work}'
    return build_animation('recursion_tree', f'재귀 트리: T(n) = {recurrence}', frames)


# 기저 사례 없는 재귀를 그릴 때의 스택 한도: 세로 격자 8칸 중 넘친 호출을 그릴 맨 윗줄을 뺀 칸 수
DRAWN_STACK_LIMIT = GRID_HEIGHT - 1


## @brief 재귀 호출 장면 실행. 기존 호출 스택 장면의 그리기(stack_frame)를 그대로 써 같은 모양으로 보이게 함
# - sum: 실제 재귀로 계산하며 호출·반환을 모두 기록
# - no_base: 반환 없이 호출만 쌓다가 그림의 스택 한도를 넘는 호출을 removed로 그리고 오버플로로 끝냄
#   실제 언어의 스택 한도는 훨씬 크므로 캡션에 "그림에서는 7칸"이라고 밝힘
def simulate_recursion(req: RecursionRequest) -> Animation:
    calls: list[tuple[str, int]] = []
    if req.function == 'no_base':
        frames = [stack_frame(calls, 'count', f'빈 호출 스택: count({req.n}) 시작 전, 기저 사례가 없음')]
        k = req.n
        while len(calls) < DRAWN_STACK_LIMIT:
            calls.append((f'call{len(calls)}', k))
            frames.append(stack_frame(calls, 'count', f'count({k}) → count({k - 1}) 호출, 스택 {len(calls)}칸, 멈출 조건 없음'))
            k -= 1
        frames.append(stack_frame(calls, 'count', f'count({k}) 호출에서 스택 한도(그림에서는 {DRAWN_STACK_LIMIT}칸) 초과 → 스택 오버플로',
                                  removed=(f'call{len(calls)}', k)))
        return build_animation('dsa_recursion', '기저 사례 없는 재귀', frames)

    frames = [stack_frame(calls, 'sum', f'빈 호출 스택: sum({req.n}) 시작 전')]

    def visit(k):
        call = (f'call{k}', k)
        calls.append(call)
        frames.append(stack_frame(calls, 'sum', f'sum({k}) 호출 → 스택 {len(calls)}칸'))
        if k == 0:
            value, calculation = 0, 'sum(0) = 0(기저 사례)'
        else:
            child = visit(k - 1)
            value, calculation = k + child, f'sum({k}): {k} + {child} = {k + child}'
        calls.pop()
        frames.append(stack_frame(calls, 'sum', f'{calculation} 반환 → 스택 {len(calls)}칸', removed=call, result=value))
        return value
    visit(req.n)
    return build_animation('dsa_recursion', f'재귀로 합 구하기: sum({req.n})', frames)


# 메모 표 줄(y=6)과 그 아래 인덱스 이름표 줄(y=7): 호출 트리는 그 위(y 0..5)에 그림
MEMO_ROW, MEMO_INDEX_ROW = GRID_HEIGHT - 2, GRID_HEIGHT - 1


## @brief 피보나치 호출을 실제 재귀로 실행하며 호출 트리(id·인자·부모)와 메모 저장 순서를 기록
# - memo=True면 k≥2 값을 계산한 뒤 memo에 저장하고, 다시 부르면 자식 호출 없이 memo 값을 씀(hit)
def fib_calls(n: int, memo: bool):
    calls, stores, table = [], [], {}

    def call(k, parent):
        call_id = f'c{len(calls)}'
        hit = memo and k in table
        calls.append({'id': call_id, 'k': k, 'parent': parent, 'hit': hit, 'stores_before': list(stores)})
        stores.clear()
        if hit:
            return table[k]
        if k <= 1:
            return k
        value = call(k - 1, call_id) + call(k - 2, call_id)
        if memo:
            table[k] = value
            stores.append((k, value))
        return value
    result = call(n, None)
    return calls, list(stores), result


## @brief 피보나치 DP 장면 실행
# - naive·memo: 호출 하나마다 프레임 하나(트리 배치는 전체 호출 트리로 한 번만 계산해 노드가 움직이지 않게 함)
# - table: dp[0], dp[1]에서 시작해 dp[i] = dp[i-1] + dp[i-2]를 왼쪽부터 한 칸씩 채움
def simulate_fib_dp(req: FibDpRequest) -> Animation:
    if req.mode == 'table':
        return fib_table(req.n)
    calls, last_stores, result = fib_calls(req.n, req.mode == 'memo')
    children: dict[str, list[str]] = {}
    for c in calls:
        if c['parent']:
            children.setdefault(c['parent'], []).append(c['id'])
    places = tidy_layout(calls[0]['id'], children)
    memo: dict[int, int] = {}

    def frame(caption, upto, new_call=None, new_memo=(), highlight=()):
        boxes, arrows = [], []
        for c in calls[:upto]:
            tone = 'new' if c['id'] == new_call else 'active' if c['hit'] or c['id'] in highlight else 'normal'
            boxes.append(Box(id=c['id'], label=f'f{c["k"]}', x=places[c['id']][0], y=places[c['id']][1], shape='node', tone=tone))
            if c['parent']:
                arrows.append(Arrow(source=c['parent'], target=c['id']))
        if req.mode == 'memo':
            boxes.append(Box(id='memo', label='memo', x=0, y=MEMO_ROW, shape='label'))
            for k in range(2, req.n + 1):
                stored = k in memo
                boxes.append(Box(id=f'm{k}', label=str(memo[k]) if stored else '', x=k - 1, y=MEMO_ROW,
                                 shape='cell' if stored else 'slot', tone='new' if k in new_memo else 'normal'))
                boxes.append(Box(id=f'mi{k}', label=f'f{k}', x=k - 1, y=MEMO_INDEX_ROW, shape='label'))
        return Frame(boxes=boxes, arrows=arrows, caption=caption)

    def saved(stores):
        for k, value in stores:
            memo[k] = value
        return ''.join(f'f({k}) = {value} 저장 → ' for k, value in stores)

    frames, seen = [], {}
    for number, c in enumerate(calls, start=1):
        prefix = saved(c['stores_before'])
        k = c['k']
        seen[k] = seen.get(k, 0) + 1
        if c['hit']:
            text = f'f({k}) 호출: memo에 있어 바로 {memo[k]} 사용'
        elif req.mode == 'naive' and seen[k] > 1:
            text = f'f({k}) 호출: 같은 계산 {seen[k]}번째'
        elif k <= 1:
            text = f'f({k}) 호출: 기저 사례 {k}'
        else:
            text = f'f({k}) 호출'
        frames.append(frame(prefix + text, number, c['id'], [k for k, _ in c['stores_before']]))
    if req.mode == 'memo':
        prefix = saved(last_stores)
        frames.append(frame(f'{prefix}호출 {len(calls)}번(같은 값은 memo에서 바로 가져옴)', len(calls),
                            new_memo=[k for k, _ in last_stores]))
        title = f'메모이제이션 fib({req.n})'
    else:
        first, repeats = set(), []
        for c in calls:
            if c['k'] in first:
                repeats.append(c['id'])
            first.add(c['k'])
        frames.append(frame(f'f({req.n}) = {result}: 호출 {len(calls)}번, 그중 {len(repeats)}번은 이미 계산한 값을 다시 계산',
                            len(calls), highlight=repeats))
        title = f'단순 재귀 fib({req.n})의 호출'
    return build_animation('fib_dp', title, frames)


## @brief 아래에서 위로 표 채우기. dp 칸(y=1)과 인덱스 이름표(y=2), 이번 칸은 new·참조한 두 칸은 active
def fib_table(n: int) -> Animation:
    dp = [0, 1]
    for i in range(2, n + 1):
        dp.append(dp[i - 1] + dp[i - 2])

    def frame(caption, filled, new=(), active=()):
        boxes = []
        for i in range(n + 1):
            done = i < filled
            tone = 'new' if i in new else 'active' if i in active else 'normal'
            boxes.append(Box(id=f'd{i}', label=str(dp[i]) if done else '', x=i, y=1, shape='cell' if done else 'slot', tone=tone))
            boxes.append(Box(id=f'i{i}', label=f'[{i}]', x=i, y=2, shape='label'))
        return Frame(boxes=boxes, arrows=[], caption=caption)

    frames = [frame('dp[0] = 0, dp[1] = 1부터 왼쪽→오른쪽으로 채웁니다', 2, new=[0, 1])]
    for i in range(2, n + 1):
        caption = f'dp[{i}] = dp[{i - 1}] + dp[{i - 2}] = {dp[i - 1]} + {dp[i - 2]} = {dp[i]}'
        if i == n:
            caption += ', 칸마다 덧셈 한 번 → O(n)'
        frames.append(frame(caption, i + 1, new=[i], active=[i - 1, i - 2]))
    return build_animation('fib_dp', f'표 채우기 fib({n})', frames)


SIMULATORS = {
    'log_steps': simulate_log_steps,
    'growth': simulate_growth,
    'loop_count': simulate_loop_count,
    'recursion_tree': simulate_recursion_tree,
    'dsa_recursion': simulate_recursion,
    'fib_dp': simulate_fib_dp,
}

# 장면 선택 요청에 넣는 형식 예시(짧고 평범한 값; SCENE_FORMATS와 같은 역할)
FORMATS = {
    'log_steps': {'kind': 'log_steps', 'mode': 'halve', 'n': 8},
    'growth': {'kind': 'growth', 'functions': ['n', 'n²'], 'sizes': [2, 4]},
    'loop_count': {'kind': 'loop_count', 'pattern': 'nested', 'n': 3},
    'recursion_tree': {'kind': 'recursion_tree', 'branch': 2, 'work': 'n', 'n': 4},
    'dsa_recursion': {'kind': 'dsa_recursion', 'function': 'sum', 'n': 3},
    'fib_dp': {'kind': 'fib_dp', 'mode': 'table', 'n': 5},
}
