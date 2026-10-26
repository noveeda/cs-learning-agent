# 파일 역할: 배열·연결 리스트·스택·큐 장면 요청을 실제 파이썬 리스트로 실행하며 단계별 프레임을 만드는 시뮬레이터
# - 프레임 규칙: frames[0]은 초기 상태, 연산 하나마다 프레임 하나(len(frames) == len(ops) + 1)
# - 원소마다 생성 순서로 id를 붙여, 자리가 바뀌어도 같은 상자로 이어지게 함
# - 실행할 수 없는 연산(빈 스택 pop, 범위 밖 인덱스 등)은 한국어 ValueError로 거부. 채팅 모델이 고쳐 다시 부를 수 있게 함
"""Array, linked list, stack and queue simulators."""
from itertools import count

from .model import (GRID_HEIGHT, GRID_WIDTH, Animation, ArrayRequest, Arrow, Box, Frame, LinkedListRequest,
                    QueueRequest, StackRequest, build_animation, josa)

# 스택 높이 상한: 세로 격자 8칸에 아래→위로 쌓을 수 있는 원소 수
STACK_MAX_HEIGHT = GRID_HEIGHT


# 역할: 강조 대상 id 집합을 보고 상자의 tone을 고름
def tone_of(box_id: str, active=(), new=()) -> str:
    return 'new' if box_id in new else 'active' if box_id in active else 'normal'


# 역할: 배열을 y=0 한 줄의 cell로 그림. 삭제된 원소는 같은 단계에서만 원래 자리 아래(y=1)에 removed로 남김
def array_frame(items, caption, active=(), new=(), removed=None) -> Frame:
    boxes = [Box(id=i, label=str(v), x=x, y=0, shape='cell', tone=tone_of(i, active, new))
             for x, (i, v) in enumerate(items)]
    if removed:
        (i, v), x = removed
        boxes.append(Box(id=i, label=str(v), x=x, y=1, shape='cell', tone='removed'))
    return Frame(boxes=boxes, arrows=[], caption=caption)


# 역할: 배열 장면 실행. insert·delete는 뒤 원소를 한 칸씩 옮기는 비용(O(n))이 보이도록 이동한 원소를 active로 표시
def simulate_array(req: ArrayRequest) -> Animation:
    ids = count()
    items = [(f'a{next(ids)}', v) for v in req.values]
    frames = [array_frame(items, f'초기 배열 {[v for _, v in items]}')]
    for op in req.ops:
        if op.op == 'insert':
            if not 0 <= op.index <= len(items):
                raise ValueError(f'insert [{op.index}]: 길이 {len(items)}인 배열에 넣을 수 있는 인덱스는 0~{len(items)}입니다')
            if len(items) == GRID_WIDTH:
                raise ValueError(f'insert: 배열은 {GRID_WIDTH}칸까지만 그립니다')
            moved = [i for i, _ in items[op.index:]]
            new = (f'a{next(ids)}', op.value)
            items.insert(op.index, new)
            what = f'{op.value}{josa(op.value, "을", "를")} 넣습니다'
            caption = (f'insert [{op.index}] {op.value}: 뒤 원소 {len(moved)}개를 한 칸씩 밀고 {what}' if moved
                       else f'insert [{op.index}] {op.value}: 맨 끝에 {what}')
            frames.append(array_frame(items, caption, active=moved, new=[new[0]]))
            continue
        if not items:
            raise ValueError(f'{op.op} [{op.index}]: 배열이 비어 있어 읽거나 지울 원소가 없습니다')
        if not 0 <= op.index < len(items):
            raise ValueError(f'{op.op} [{op.index}]: 길이 {len(items)}인 배열의 인덱스 범위(0~{len(items) - 1})를 벗어났습니다')
        if op.op == 'access':
            box_id, value = items[op.index]
            frames.append(array_frame(items, f'access [{op.index}]: 주소 계산으로 바로 찾은 값은 {value}입니다',
                                      active=[box_id]))
        else:
            removed = items.pop(op.index)
            moved = [i for i, _ in items[op.index:]]
            frames.append(array_frame(
                items, f'delete [{op.index}]: {removed[1]}{josa(removed[1], "을", "를")} 지우고 뒤 원소 {len(moved)}개를 당깁니다',
                active=moved, removed=(removed, op.index)))
    return build_animation('array', '배열 연산', frames)


# 역할: 연결 리스트를 head 이름표(0,0)와 y=1 한 줄의 node로 그리고, head·각 노드의 next를 화살표로 이음
# - 삭제된 노드는 같은 단계에서만 원래 자리 아래(y=2)에 화살표 없이 남겨 연결이 끊긴 모습을 보여 줌
def list_frame(items, caption, active=(), new=(), removed=None) -> Frame:
    boxes = [Box(id='head', label='head', x=0, y=0, shape='label')]
    boxes += [Box(id=i, label=str(v), x=x, y=1, shape='node', tone=tone_of(i, active, new))
              for x, (i, v) in enumerate(items)]
    order = ['head'] + [i for i, _ in items]
    arrows = [Arrow(source=a, target=b) for a, b in zip(order, order[1:])]
    if removed:
        (i, v), x = removed
        boxes.append(Box(id=i, label=str(v), x=x, y=2, shape='node', tone='removed'))
    return Frame(boxes=boxes, arrows=arrows, caption=caption)


# 역할: 값으로 첫 노드 위치를 찾음. 없으면 연산 이름을 담은 한국어 오류
def find_value(items, value: int, op: str) -> int:
    for index, (_, v) in enumerate(items):
        if v == value:
            return index
    raise ValueError(f'{op}: {value}{josa(value, "이", "가")} 리스트에 없습니다')


# 역할: 단일 연결 리스트 장면 실행. 포인터를 바꾼 노드(앞 노드)를 active로 표시
def simulate_linked_list(req: LinkedListRequest) -> Animation:
    ids = count()
    items = [(f'n{next(ids)}', v) for v in req.values]
    start = ' → '.join(str(v) for _, v in items)
    frames = [list_frame(items, f'초기 연결 리스트: head → {start} → null' if items else '빈 연결 리스트(head = null)')]
    for op in req.ops:
        if op.op == 'insert_head':
            new = (f'n{next(ids)}', op.value)
            items.insert(0, new)
            frames.append(list_frame(items, f'insert_head {op.value}: 새 노드가 기존 첫 노드를 가리키고 head가 새 노드를 가리킵니다',
                                     new=[new[0]]))
        elif op.op == 'insert_after':
            index = find_value(items, op.target, f'insert_after {op.target}')
            new = (f'n{next(ids)}', op.value)
            items.insert(index + 1, new)
            frames.append(list_frame(
                items, f'insert_after {op.target} {op.value}: 새 노드가 {op.target}의 다음을 이어받고 {op.target}{josa(op.target, "이", "가")} 새 노드를 가리킵니다',
                active=[items[index][0]], new=[new[0]]))
        else:
            index = find_value(items, op.value, f'delete {op.value}')
            removed = items.pop(index)
            if index == 0:
                caption, active = f'delete {op.value}: head가 {op.value}의 다음 노드를 가리키게 바꿉니다', []
            else:
                before = items[index - 1]
                caption, active = f'delete {op.value}: 앞 노드 {before[1]}{josa(before[1], "이", "가")} {op.value}의 다음 노드를 가리키게 바꿉니다', [before[0]]
            frames.append(list_frame(items, caption, active=active, removed=(removed, index)))
    return build_animation('linked_list', '단일 연결 리스트 연산', frames)


# 역할: 스택 장면 실행
# - 먼저 모든 단계의 상태를 기록하고, 가장 높았던 높이를 안 뒤에 그림. 바닥을 고정해 프레임마다 화면이 출렁이지 않게 하기 위함
# - pop한 상자는 그 단계에서만 원래 자리에 removed로 남김
def simulate_stack(req: StackRequest) -> Animation:
    ids = count()
    items: list[tuple[str, int]] = []
    states = [([], '빈 스택에서 시작합니다', None, None)]
    for op in req.ops:
        if op.op == 'push':
            if len(items) == STACK_MAX_HEIGHT:
                raise ValueError(f'push {op.value}: 스택은 {STACK_MAX_HEIGHT}칸까지만 그립니다')
            items.append((f's{next(ids)}', op.value))
            states.append((list(items), f'push {op.value}: {op.value}{josa(op.value, "을", "를")} top 위에 올립니다',
                           items[-1][0], None))
        else:
            if not items:
                raise ValueError('pop: 스택이 비어 있어 꺼낼 원소가 없습니다')
            popped = items.pop()
            states.append((list(items), f'pop: top의 {popped[1]}{josa(popped[1], "을", "를")} 꺼냅니다', None, popped))
    height = max(1, *(len(s[0]) + (1 if s[3] else 0) for s in states))
    frames = []
    for stack, caption, new, popped in states:
        boxes = [Box(id=i, label=str(v), x=0, y=height - 1 - level, shape='cell', tone=tone_of(i, new=[new]))
                 for level, (i, v) in enumerate(stack)]
        arrows = []
        if stack:
            boxes.append(Box(id='top', label='top', x=1, y=height - len(stack), shape='label'))
            arrows.append(Arrow(source='top', target=stack[-1][0]))
        if popped:
            boxes.append(Box(id=popped[0], label=str(popped[1]), x=0, y=height - 1 - len(stack), shape='cell', tone='removed'))
        frames.append(Frame(boxes=boxes, arrows=arrows, caption=caption))
    return build_animation('stack', '스택 push·pop', frames)


# 역할: 큐를 y=1 한 줄의 slot으로 그리고, front 이름표는 위(y=0), rear 이름표는 아래(y=2)에서 화살표로 가리킴
# - 배열 기반 큐는 원소가 칸을 옮기지 않으므로 slot 상자 자체가 원소 자리이고 id가 고정됨
# - 큐가 비면 front·rear 이름표를 그리지 않음
def queue_frame(slots, front, size, caption, new=None, removed=None) -> Frame:
    capacity = len(slots)
    boxes = [Box(id=f'slot{x}', label='' if v is None else str(v), x=x, y=1, shape='slot',
                 tone='new' if x == new else 'removed' if x == removed else 'normal')
             for x, v in enumerate(slots)]
    arrows = []
    if size:
        rear = (front + size - 1) % capacity
        boxes += [Box(id='front', label='front', x=front, y=0, shape='label'),
                  Box(id='rear', label='rear', x=rear, y=2, shape='label')]
        arrows += [Arrow(source='front', target=f'slot{front}'), Arrow(source='rear', target=f'slot{rear}')]
    return Frame(boxes=boxes, arrows=arrows, caption=caption)


# 역할: 배열 기반 큐 장면 실행. front 위치와 원소 수로 상태를 관리하고, rear는 마지막 원소 칸을 가리킴
# - 선형 큐는 rear가 배열 끝에 닿으면 앞쪽 빈칸이 있어도 넣지 못함(원형 큐가 해결하는 문제)
def simulate_queue(req: QueueRequest) -> Animation:
    slots: list[int | None] = [None] * req.capacity
    front = size = 0
    name = '원형 큐' if req.circular else '선형 큐'
    frames = [queue_frame(slots, front, size, f'빈 {name}(용량 {req.capacity})에서 시작합니다')]
    for op in req.ops:
        if op.op == 'enqueue':
            if size == req.capacity:
                raise ValueError(f'enqueue {op.value}: 큐가 가득 차 넣을 수 없습니다(용량 {req.capacity})')
            if not req.circular and front + size == req.capacity:
                raise ValueError(f'enqueue {op.value}: 선형 큐는 rear가 배열 끝에 닿아 앞쪽 빈칸을 다시 쓰지 못합니다')
            # 0번 칸으로 넘어가는 그 enqueue만 "돌아온다". 이미 한 바퀴 돈 뒤(front + size > capacity)는 평범한 다음 칸
            wrapped = front + size == req.capacity
            slot = (front + size) % req.capacity
            slots[slot] = op.value
            size += 1
            where = f'rear가 끝을 지나 {slot}번 칸으로 돌아와' if wrapped else f'rear 다음 칸 [{slot}]에'
            frames.append(queue_frame(slots, front, size, f'enqueue {op.value}: {where} {op.value}{josa(op.value, "을", "를")} 넣습니다', new=slot))
        else:
            if not size:
                raise ValueError('dequeue: 큐가 비어 있어 꺼낼 원소가 없습니다')
            value, slot = slots[front], front
            front, size = (front + 1) % req.capacity if req.circular else front + 1, size - 1
            frames.append(queue_frame(slots, front, size, f'dequeue: front의 {value}{josa(value, "을", "를")} 꺼냅니다', removed=slot))
            slots[slot] = None
    return build_animation('queue', f'{name} enqueue·dequeue', frames)
