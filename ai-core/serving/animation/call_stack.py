## @file
# @brief factorial·fib를 실제 재귀로 계산해 호출 스택의 쌓임·LIFO 반환을 기존 Box·Frame으로 표현
# - 호출마다 고유 id 부여: 같은 인자의 fib가 다시 호출돼도 이전 호출과 다른 실행임을 구분
# - 반환 프레임에서는 빠진 호출을 removed로 남기고 계산값을 따로 표시해 스택 해제와 값 전달을 함께 설명
from itertools import count

from .model import GRID_HEIGHT, Animation, Arrow, Box, CallStackRequest, Frame, build_animation


## @brief 실행 중인 호출 목록과 선택적 반환 정보를 12×8 격자의 프레임으로 변환
# - 스택 바닥을 y=7에 고정해 호출이 늘면 위로 쌓이고, top은 현재 실행을 재개할 호출을 가리킴
def stack_frame(calls, function, caption, removed=None, result=None) -> Frame:
    boxes = [Box(id='top', label='top', x=0, y=GRID_HEIGHT - len(calls) if calls else GRID_HEIGHT - 1, shape='label')]
    visible = [*calls, removed] if removed else calls
    for depth, (call_id, n) in enumerate(visible):
        row = GRID_HEIGHT - 1 - depth
        tone = 'removed' if removed and call_id == removed[0] else 'active' if depth == len(calls) - 1 else 'normal'
        boxes.append(Box(id=call_id, label=str(n), x=1, y=row, shape='cell', tone=tone))
        boxes.append(Box(id=f'{call_id}name', label=f'{function}({n})', x=3, y=row, shape='label'))
    arrows = [Arrow(source='top', target=calls[-1][0])] if calls else []
    if result is not None:
        boxes += [Box(id='result', label=str(result), x=6, y=0, shape='cell', tone='new'),
                  Box(id='return', label='return', x=6, y=1, shape='label')]
    return Frame(boxes=boxes, arrows=arrows, caption=caption)


## @brief 검증된 factorial·fib 입력을 계산하며 초기·호출·반환 단계를 빠짐없이 기록
# - 입력 상한은 요청 스키마에서 제한: 실행 중간에 단계를 생략하면 계산 흐름을 잘못 가르치므로 전체를 그림
def simulate_call_stack(req: CallStackRequest) -> Animation:
    ids, calls = count(), []
    frames = [stack_frame(calls, req.function, f'빈 호출 스택: {req.function}({req.n}) 시작 전')]

    ## @brief 호출을 push한 뒤 자식 결과로 값을 계산하고 pop; 반환값으로 상위 호출의 계산 재개
    def visit(n):
        call = (f'call{next(ids)}', n)
        calls.append(call)
        name = f'{req.function}({n})'
        frames.append(stack_frame(calls, req.function, f'{name} 호출 → 스택 {len(calls)}칸'))
        if n <= 1:
            value = 1 if req.function == 'factorial' else n
            calculation = f'{name}={value}'
        elif req.function == 'factorial':
            child = visit(n - 1)
            value = n * child
            calculation = f'{name}: {n} × {child} = {value}'
        else:
            left, right = visit(n - 1), visit(n - 2)
            value = left + right
            calculation = f'{name}: {left} + {right} = {value}'
        calls.pop()
        frames.append(stack_frame(calls, req.function, f'{calculation} 반환 → 스택 {len(calls)}칸',
                                  removed=call, result=value))
        return value

    visit(req.n)
    return build_animation('call_stack', f'재귀 호출 스택: {req.function}({req.n})', frames)
