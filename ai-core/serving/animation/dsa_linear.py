## @file
# @brief 자료구조·알고리즘(dsa) 과목의 배열·참조 계열 장면: 메모리 주소, 참조, 동적 배열, 연결 리스트 순회, 탐색, 선택 정렬
# - 요청 모델과 시뮬레이터를 한 파일에 둠. model.py는 REQUESTS를 합집합에, __init__.py는 SIMULATORS·FORMATS를 등록 표에 한 줄로 합침
# - 상태는 파이썬 리스트·dict로 실제 연산을 실행해 계산하고, 프레임은 그 상태를 그대로 그림(모델이 상태를 지어내지 않음)
# - 실행할 수 없는 입력(없는 인덱스·이름, 격자를 넘는 크기)은 한국어 ValueError로 거부
"""DSA memory, reference, dynamic array, list walk, search and selection sort scenes."""
from itertools import count
from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from .linear import tone_of
from .model import GRID_WIDTH, Animation, Arrow, Box, Frame, Strict, Value, build_animation, josa

# 변수·객체 이름: 칸 옆 이름표에 들어가는 세 글자 이내
VarName = Annotated[str, Field(min_length=1, max_length=3)]


# --- 장면 요청 ---

## @brief 배열의 i번째 원소 주소를 계산해 읽는 연산
class MemoryAccessOp(Strict):
    op: Literal['access']
    index: int = Field(ge=0, le=7)


## @brief 메모리 주소 장면. 시작 주소(base)와 원소 크기(width 바이트)로 연속 배치된 원소의 주소를 계산
# - width는 실제 자료형 크기(1·2·4·8바이트)만 허용. 주소 이름표가 네 자리 안에 들도록 base는 0~900
class MemoryRequest(Strict):
    kind: Literal['dsa_memory']
    base: int = Field(ge=0, le=900)
    width: Literal[1, 2, 4, 8]
    values: list[Value] = Field(min_length=1, max_length=8)
    ops: list[MemoryAccessOp] = Field(min_length=1, max_length=6)


## @brief 변수에 값을 직접 저장(let)하거나, 새 객체를 만들어 그 주소를 저장(new)하거나, 값을 바꾸는(set) 연산
class RefValueOp(Strict):
    op: Literal['let', 'new', 'set']
    name: VarName
    value: Value


## @brief source 변수의 내용(값 또는 주소)을 name 변수에 복사하는 연산
class RefCopyOp(Strict):
    op: Literal['copy']
    name: VarName
    source: VarName


## @brief 변수를 읽는 연산. 참조 변수면 주소를 따라가 객체 값을 읽음
class RefReadOp(Strict):
    op: Literal['read']
    name: VarName


## @brief 참조 장면. 변수 칸(왼쪽)과 객체(오른쪽)를 따로 그려 값 복사와 참조(주소) 복사를 구분
class ReferenceRequest(Strict):
    kind: Literal['dsa_reference']
    ops: list[Annotated[Union[RefValueOp, RefCopyOp, RefReadOp], Field(discriminator='op')]] = Field(min_length=1, max_length=8)


## @brief 동적 배열 장면. 빈 배열(capacity칸)에 values를 차례로 끝에 추가
# - growth: double(가득 차면 두 배), plus1(가득 차면 한 칸 더). 두 방식의 복사 횟수 차이가 분할 상환 분석의 핵심
class DynamicArrayRequest(Strict):
    kind: Literal['dynamic_array']
    capacity: int = Field(ge=1, le=4)
    growth: Literal['double', 'plus1']
    values: list[Value] = Field(min_length=1, max_length=9)


## @brief 연결 리스트 순회 장면. head에서 next를 따라 index번째 노드까지 이동
class ListWalkRequest(Strict):
    kind: Literal['list_walk']
    values: list[Value] = Field(min_length=1, max_length=8)
    index: int = Field(ge=0, le=7)


## @brief 탐색 장면. linear는 앞에서부터 한 칸씩, binary는 정렬된 배열에서 가운데와 비교해 범위를 절반씩 줄임
class SearchRequest(Strict):
    kind: Literal['dsa_search']
    algorithm: Literal['linear', 'binary']
    values: list[Value] = Field(min_length=2, max_length=12)
    target: Value

    ## @brief 이진 탐색은 오름차순 정렬(중복 없음)이 전제라 그렇지 않은 입력을 거부
    @model_validator(mode='after')
    def validate_sorted(self):
        if self.algorithm == 'binary' and any(a >= b for a, b in zip(self.values, self.values[1:])):
            raise ValueError('이진 탐색은 오름차순으로 정렬된(중복 없는) 배열이 필요합니다')
        return self


## @brief 선택 정렬 장면. 같은 값은 입력 순서대로 a·b 꼬리표를 붙여 안정성(같은 값의 순서 유지 여부)을 보여 줌
class SelectionSortRequest(Strict):
    kind: Literal['selection_sort']
    values: list[Value] = Field(min_length=2, max_length=8)


REQUESTS = (MemoryRequest, ReferenceRequest, DynamicArrayRequest, ListWalkRequest, SearchRequest, SelectionSortRequest)


# --- 시뮬레이터 ---

## @brief 숫자 뒤 '(으)로'를 고름. 끝자리 읽기가 ㄹ 받침(1 일·7 칠·8 팔)이거나 받침이 없으면(2·4·5·9) '로'
def euro(number: int) -> str:
    return '로' if str(abs(number))[-1] in '1245789' else '으로'

## @brief 메모리 주소 장면 실행. 주소 이름표(y=0)·값 칸(y=1)을 그리고, 접근마다 주소 계산식을 캡션에 넣음
def simulate_memory(req: MemoryRequest) -> Animation:
    addresses = [req.base + req.width * i for i in range(len(req.values))]

    def frame(caption, index=None):
        boxes = []
        for i, (address, value) in enumerate(zip(addresses, req.values)):
            tone = 'active' if i == index else 'normal'
            boxes.append(Box(id=f'addr{i}', label=str(address), x=i, y=0, shape='label', tone=tone))
            boxes.append(Box(id=f'm{i}', label=str(value), x=i, y=1, shape='cell', tone=tone))
        return Frame(boxes=boxes, arrows=[], caption=caption)

    frames = [frame(f'시작 주소 {req.base}, 원소 하나 {req.width}바이트씩 연속 배치')]
    for op in req.ops:
        if op.index >= len(req.values):
            raise ValueError(f'access [{op.index}]: 원소 {len(req.values)}개 배열의 인덱스 범위(0~{len(req.values) - 1})를 벗어났습니다')
        address = addresses[op.index]
        frames.append(frame(f'[{op.index}] 주소 = {req.base} + {req.width}×{op.index} = {address} → 값 {req.values[op.index]}',
                            op.index))
    return build_animation('dsa_memory', '메모리 주소 계산', frames)


# 참조 장면의 객체 주소: 첫 객체 200번지부터 4바이트씩. 변수 칸(주소 100번대 스택)과 다른 영역임을 숫자로 구분
OBJECT_BASE, OBJECT_STEP = 200, 4
# 변수·객체 개수 상한: 세로 격자 8칸 중 한 줄에 하나씩
REF_MAX_ROWS = 6


## @brief 참조 장면 실행
# - 변수: 이름표(x=0)와 칸(x=1)을 생성 순서대로 한 줄씩. 값 변수 칸에는 값, 참조 변수 칸에는 객체 주소를 적고 객체로 화살표
# - 객체: 오른쪽 노드(x=6)와 주소 이름표(x=7)를 생성 순서대로 한 줄씩
# - 아무 변수도 가리키지 않게 된 객체는 그 단계에서만 removed로 남기고 다음 단계부터 지움(도달할 수 없는 객체)
def simulate_reference(req: ReferenceRequest) -> Animation:
    names: list[str] = []
    slots: dict[str, tuple[str, int]] = {}
    objects: dict[int, int] = {}
    rows: dict[int, int] = {}
    ids = count()

    def frame(caption, active=(), new=(), dropped=()):
        boxes, arrows = [], []
        for row, name in enumerate(names):
            kind, content = slots[name]
            label = str(OBJECT_BASE + OBJECT_STEP * content) if kind == 'ref' else str(content)
            boxes.append(Box(id=f'n_{name}', label=name, x=0, y=row, shape='label'))
            boxes.append(Box(id=f'v_{name}', label=label, x=1, y=row, shape='cell', tone=tone_of(name, active, new)))
            if kind == 'ref':
                arrows.append(Arrow(source=f'v_{name}', target=f'o{content}'))
        for obj, value in objects.items():
            tone = 'removed' if obj in dropped else tone_of(obj, active, new)
            boxes.append(Box(id=f'o{obj}', label=str(value), x=6, y=rows[obj], shape='node', tone=tone))
            boxes.append(Box(id=f'a{obj}', label=f'{OBJECT_BASE + OBJECT_STEP * obj}번지', x=7, y=rows[obj], shape='label'))
        return Frame(boxes=boxes, arrows=arrows, caption=caption)

    def need(name, op):
        if name not in slots:
            raise ValueError(f'{op} {name}: 아직 만들지 않은 변수입니다')
        return slots[name]

    def assign(name, slot):
        if name not in slots:
            if len(names) == REF_MAX_ROWS:
                raise ValueError(f'변수는 {REF_MAX_ROWS}개까지만 그립니다')
            names.append(name)
        slots[name] = slot

    def unreachable():
        alive = {content for kind, content in slots.values() if kind == 'ref'}
        return [obj for obj in objects if obj not in alive]

    frames = [frame('빈 메모리: 변수 칸(왼쪽)과 객체(오른쪽)')]
    for op in req.ops:
        active, new = [], []
        if op.op == 'let':
            assign(op.name, ('value', op.value))
            caption, new = f'{op.name} = {op.value}: 변수 칸에 값 {op.value}{josa(op.value, "을", "를")} 바로 저장합니다', [op.name]
        elif op.op == 'new':
            if len(rows) == REF_MAX_ROWS:
                raise ValueError(f'객체는 {REF_MAX_ROWS}개까지만 그립니다')
            obj = next(ids)
            objects[obj], rows[obj] = op.value, len(rows)
            assign(op.name, ('ref', obj))
            address = OBJECT_BASE + OBJECT_STEP * obj
            caption, new = f'{op.name} = new({op.value}): {address}번지에 객체를 만들고 {op.name}에는 주소 {address}만 저장', [op.name, obj]
        elif op.op == 'copy':
            kind, content = need(op.source, 'copy')
            assign(op.name, (kind, content))
            if kind == 'ref':
                address = OBJECT_BASE + OBJECT_STEP * content
                caption = f'{op.name} = {op.source}: 주소 {address}{josa(address, "을", "를")} 복사해 {op.name}도 같은 객체를 가리킵니다'
                active = [op.source, content]
            else:
                caption = f'{op.name} = {op.source}: 값 {content}{josa(content, "을", "를")} 복사해 {op.name} 칸에 따로 저장합니다'
                active = [op.source]
            new = [op.name]
        elif op.op == 'set':
            kind, content = need(op.name, 'set')
            if kind == 'ref':
                objects[content] = op.value
                others = [n for n in names if n != op.name and slots[n] == ('ref', content)]
                seen = f', {"·".join(others)}에서 읽어도 {op.value}' if others else ''
                caption, active, new = f'{op.name}가 가리키는 객체를 {op.value}{euro(op.value)} 바꿉니다{seen}', [op.name], [content]
            else:
                slots[op.name] = ('value', op.value)
                caption, new = f'{op.name} = {op.value}: {op.name} 칸만 바뀌고 다른 변수는 그대로입니다', [op.name]
        else:
            kind, content = need(op.name, 'read')
            if kind == 'ref':
                address = OBJECT_BASE + OBJECT_STEP * content
                caption = f'{op.name} 읽기: 주소 {address}{josa(address, "을", "를")} 따라가 값 {objects[content]}{josa(objects[content], "을", "를")} 읽습니다'
                active = [op.name, content]
            else:
                caption, active = f'{op.name} 읽기: 칸에 든 값 {content}{josa(content, "을", "를")} 바로 읽습니다', [op.name]
        dropped = unreachable()
        if dropped:
            caption += ', 아무도 안 가리키는 객체는 버려짐'
        frames.append(frame(caption, active, new, dropped))
        for obj in dropped:
            del objects[obj]
    title = '값 복사와 참조 복사' if any(op.op == 'copy' for op in req.ops) else '값을 담은 변수와 주소를 담은 변수'
    return build_animation('dsa_reference', title, frames)


# 동적 배열의 두 줄: 할당할 때마다 위·아래 줄을 번갈아 써서 새 공간에 복사하는 모습이 이동으로 보이게 함
DYNAMIC_ROWS = (1, 3)


## @brief 동적 배열 장면 실행
# - 끝 추가마다 프레임 하나. 가득 찼으면 그 앞에 재할당 프레임을 하나 더 둠(새 배열로 원소가 옮겨 가고, 이전 배열은 removed로 남음)
# - 맨 위 줄 이름표에 현재 용량과 누적 복사 횟수를 적어 분할 상환 비용을 숫자로 비교하게 함
def simulate_dynamic_array(req: DynamicArrayRequest) -> Animation:
    items: list[tuple[str, int]] = []
    capacity, copies, generation = req.capacity, 0, 0

    def frame(caption, new=(), active=(), ghost=None):
        row = DYNAMIC_ROWS[generation % 2]
        boxes = [Box(id='cap', label=f'용량 {capacity}', x=0, y=0, shape='label'),
                 Box(id='copies', label=f'복사 {copies}회', x=3, y=0, shape='label')]
        for x in range(capacity):
            if x < len(items):
                element, value = items[x]
                boxes.append(Box(id=element, label=str(value), x=x, y=row, shape='cell', tone=tone_of(element, active, new)))
            else:
                boxes.append(Box(id=f'g{generation}s{x}', label='', x=x, y=row, shape='slot'))
        if ghost:
            old_row = DYNAMIC_ROWS[(generation - 1) % 2]
            boxes += [Box(id=f'g{generation - 1}c{x}', label=str(value), x=x, y=old_row, shape='cell', tone='removed')
                      for x, (_, value) in enumerate(ghost)]
        return Frame(boxes=boxes, arrows=[], caption=caption)

    frames = [frame(f'용량 {capacity}인 빈 동적 배열')]
    for number, value in enumerate(req.values):
        if len(items) == capacity:
            old = capacity
            capacity = capacity * 2 if req.growth == 'double' else capacity + 1
            if capacity > GRID_WIDTH:
                raise ValueError(f'append {value}: 용량 {capacity}칸이 필요해 격자 {GRID_WIDTH}칸을 넘습니다')
            copies += len(items)
            generation += 1
            frames.append(frame(f'가득 참: 용량 {old}→{capacity}인 새 배열을 잡고 원소 {len(items)}개를 복사',
                                active=[e for e, _ in items], ghost=list(items)))
        element = f'e{number}'
        items.append((element, value))
        frames.append(frame(f'append {value}: 빈칸 [{len(items) - 1}]에 넣습니다(추가 {number + 1}번, 누적 복사 {copies}번)',
                            new=[element]))
    name = '두 배씩' if req.growth == 'double' else '한 칸씩'
    return build_animation('dynamic_array', f'동적 배열: 용량을 {name} 늘리기', frames)


## @brief 연결 리스트 순회 장면 실행. cur 이름표(y=2)가 노드 아래를 한 칸씩 옮겨 가며 next를 따라간 횟수를 보여 줌
def simulate_list_walk(req: ListWalkRequest) -> Animation:
    if req.index >= len(req.values):
        raise ValueError(f'index {req.index}: 노드 {len(req.values)}개 리스트의 범위(0~{len(req.values) - 1})를 벗어났습니다')

    def frame(caption, at=None):
        boxes = [Box(id='head', label='head', x=0, y=0, shape='label')]
        boxes += [Box(id=f'n{x}', label=str(v), x=x, y=1, shape='node', tone='active' if x == at else 'normal')
                  for x, v in enumerate(req.values)]
        order = ['head'] + [f'n{x}' for x in range(len(req.values))]
        arrows = [Arrow(source=a, target=b) for a, b in zip(order, order[1:])]
        if at is not None:
            boxes.append(Box(id='cur', label='cur', x=at, y=2, shape='label'))
            arrows.append(Arrow(source='cur', target=f'n{at}'))
        return Frame(boxes=boxes, arrows=arrows, caption=caption)

    frames = [frame(f'[{req.index}]번째 값을 찾으려면 head부터 따라가야 합니다')]
    frames.append(frame('cur = head: 0번째 노드에서 시작', 0))
    for x in range(1, req.index + 1):
        frames.append(frame(f'cur = cur.next: {x}번째 노드로 이동(next {x}번)', x))
    value = req.values[req.index]
    frames[-1] = frame(f'[{req.index}] 도착: next를 {req.index}번 따라가 값 {value}{josa(value, "을", "를")} 찾음', req.index)
    return build_animation('list_walk', f'연결 리스트 [{req.index}] 접근', frames)


## @brief 탐색 장면의 한 프레임. 값 칸(y=1), 범위 밖 칸은 점선 slot으로 흐리게, 포인터 이름표는 위(mid)·아래(i·lo, hi)
def search_frame(values, caption, live=None, pointers=(), active=None, found=None) -> Frame:
    boxes, arrows = [], []
    for x, value in enumerate(values):
        inside = live is None or live[0] <= x <= live[1]
        tone = 'new' if x == found else 'active' if x == active else 'normal'
        boxes.append(Box(id=f's{x}', label=str(value), x=x, y=1, shape='cell' if inside else 'slot', tone=tone))
    for name, x, y in pointers:
        boxes.append(Box(id=name, label=name, x=x, y=y, shape='label'))
        arrows.append(Arrow(source=name, target=f's{x}'))
    return Frame(boxes=boxes, arrows=arrows, caption=caption)


## @brief 선형 탐색: 앞에서부터 비교. 첫 칸에서 찾으면 최선, 끝까지 비교하면 최악이라고 캡션에 적음
def linear_search(req: SearchRequest) -> list[Frame]:
    values, target = req.values, req.target
    frames = [search_frame(values, f'{target}{josa(target, "을", "를")} 앞에서부터 한 칸씩 비교해 찾습니다')]
    for x, value in enumerate(values):
        compared = x + 1
        if value == target:
            case = '최선' if x == 0 else '최악' if x == len(values) - 1 else '중간'
            frames.append(search_frame(values, f'[{x}] = {target} 찾음: 비교 {compared}번({case})', pointers=[('i', x, 2)], found=x))
            return frames
        frames.append(search_frame(values, f'[{x}] {value} ≠ {target}: 다음 칸으로', pointers=[('i', x, 2)], active=x))
    frames.append(search_frame(values, f'끝까지 없음: 비교 {len(values)}번(최악)', live=(len(values), -1)))
    return frames


## @brief 이진 탐색: mid = (lo+hi)//2와 비교해 남길 절반을 고름. 비교마다 프레임 하나, 못 찾으면 lo > hi 단계를 더함
def binary_search(req: SearchRequest) -> list[Frame]:
    values, target = req.values, req.target
    lo, hi, compared = 0, len(values) - 1, 0
    frames = [search_frame(values, f'정렬된 배열에서 {target} 찾기: 범위 [{lo}..{hi}]', live=(lo, hi),
                           pointers=[('lo', lo, 2), ('hi', hi, 3)])]
    while lo <= hi:
        mid = (lo + hi) // 2
        compared += 1
        pointers = [('mid', mid, 0), ('lo', lo, 2), ('hi', hi, 3)]
        if values[mid] == target:
            frames.append(search_frame(values, f'mid = ({lo}+{hi})//2 = {mid}: [{mid}] = {target} 찾음, 비교 {compared}번',
                                       live=(lo, hi), pointers=pointers, found=mid))
            return frames
        if values[mid] < target:
            rest = f'{values[mid]} < {target}이라 오른쪽 절반만 남김'
            next_lo, next_hi = mid + 1, hi
        else:
            rest = f'{values[mid]} > {target}이라 왼쪽 절반만 남김'
            next_lo, next_hi = lo, mid - 1
        frames.append(search_frame(values, f'mid = ({lo}+{hi})//2 = {mid}: {rest}', live=(lo, hi), pointers=pointers, active=mid))
        lo, hi = next_lo, next_hi
    frames.append(search_frame(values, f'lo > hi: 범위가 비어 {target}{josa(target, "이", "가")} 없음, 비교 {compared}번',
                               live=(lo, hi)))
    return frames


## @brief 탐색 장면 실행
def simulate_search(req: SearchRequest) -> Animation:
    if req.algorithm == 'linear':
        return build_animation('dsa_search', f'선형 탐색: {req.target} 찾기', linear_search(req))
    return build_animation('dsa_search', f'이진 탐색: {req.target} 찾기', binary_search(req))


## @brief 같은 값이 여러 번 나오면 입력 순서대로 a·b·c 꼬리표를 붙인 라벨 목록을 만듦
def tagged_labels(values) -> list[str]:
    seen: dict[int, int] = {}
    labels = []
    for value in values:
        if values.count(value) > 1:
            labels.append(f'{value}{"abcdefgh"[seen.get(value, 0)]}')
            seen[value] = seen.get(value, 0) + 1
        else:
            labels.append(str(value))
    return labels


## @brief 꼬리표 붙은 라벨(2a 등) 뒤 조사를 고름. 숫자로 끝나면 숫자 읽기, a~h(에이·비…에이치)로 끝나면 받침 없음
def label_josa(label: str, with_final: str, without_final: str) -> str:
    return josa(int(label), with_final, without_final) if label[-1].isdigit() else without_final


## @brief 선택 정렬 장면 실행. 회전마다 남은 부분의 최솟값(같으면 앞쪽)을 앞자리와 교환
# - 원소마다 처음 자리로 id를 붙여 교환이 이동으로 보이게 함. 이번 회전에 자리가 정해진 원소는 new, 교환 상대는 active
# - 같은 값이 있으면 마지막 단계에 원래 순서가 유지됐는지(안정·불안정)를 계산해 알려 줌
def simulate_selection_sort(req: SelectionSortRequest) -> Animation:
    items = [(f'e{i}', value, label) for i, (value, label) in enumerate(zip(req.values, tagged_labels(req.values)))]

    def frame(caption, tones=None):
        tones = tones or {}
        return Frame(boxes=[Box(id=i, label=label, x=x, y=0, shape='cell', tone=tones.get(i, 'normal'))
                            for x, (i, _, label) in enumerate(items)], arrows=[], caption=caption)

    frames = [frame('남은 부분에서 최솟값을 골라 앞자리와 바꿉니다')]
    for i in range(len(items) - 1):
        m = min(range(i, len(items)), key=lambda k: (items[k][1], k))
        chosen = items[m]
        if m == i:
            caption = f'{i + 1}회전: 최솟값 {chosen[2]}{label_josa(chosen[2], "이", "가")} 이미 [{i}]에 있음'
            tones = {chosen[0]: 'new'}
        else:
            caption = (f'{i + 1}회전: 최솟값 {chosen[2]}{label_josa(chosen[2], "을", "를")} '
                       f'[{i}]의 {items[i][2]}{label_josa(items[i][2], "과", "와")} 교환')
            tones = {chosen[0]: 'new', items[i][0]: 'active'}
            items[i], items[m] = items[m], items[i]
        frames.append(frame(caption, tones))
    duplicates = [label for label in tagged_labels(req.values) if not label.lstrip('-').isdigit()]
    if duplicates:
        order = [label for _, _, label in items if label in duplicates]
        stable = order == sorted(order, key=lambda label: (int(label[:-1]), label[-1]))
        verdict = '같은 값의 입력 순서가 유지됨' if stable else '같은 값의 입력 순서가 뒤바뀜(불안정 정렬)'
        frames.append(frame(f'결과 {", ".join(label for _, _, label in items)} → {verdict}',
                            {i: 'active' for i, _, label in items if label in duplicates}))
    return build_animation('selection_sort', '선택 정렬', frames)


SIMULATORS = {
    'dsa_memory': simulate_memory,
    'dsa_reference': simulate_reference,
    'dynamic_array': simulate_dynamic_array,
    'list_walk': simulate_list_walk,
    'dsa_search': simulate_search,
    'selection_sort': simulate_selection_sort,
}

# 장면 선택 요청에 넣는 형식 예시(짧고 평범한 값; SCENE_FORMATS와 같은 역할)
FORMATS = {
    'dsa_memory': {'kind': 'dsa_memory', 'base': 100, 'width': 4, 'values': [3, 5, 7], 'ops': [{'op': 'access', 'index': 1}]},
    'dsa_reference': {'kind': 'dsa_reference', 'ops': [{'op': 'new', 'name': 'p', 'value': 1},
                                                       {'op': 'copy', 'name': 'q', 'source': 'p'}, {'op': 'read', 'name': 'q'}]},
    'dynamic_array': {'kind': 'dynamic_array', 'capacity': 2, 'growth': 'double', 'values': [1, 2, 3]},
    'list_walk': {'kind': 'list_walk', 'values': [1, 2, 3], 'index': 1},
    'dsa_search': {'kind': 'dsa_search', 'algorithm': 'linear', 'values': [1, 2, 3], 'target': 2},
    'selection_sort': {'kind': 'selection_sort', 'values': [3, 1, 2]},
}
