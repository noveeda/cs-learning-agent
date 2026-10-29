## @file
# @brief 컴퓨터 구조 장면 6종(2의 보수·명령어 사이클·파이프라인·메모리 계층·직접 사상 캐시·페이지 변환)의 요청 스키마와 시뮬레이터
# - 요청은 model.py의 AnimationRequest 합집합에, 시뮬레이터와 형식 예시는 __init__.py의 SIMULATORS·SCENE_FORMATS에 등록됨
# - 비트 연산·스케줄·적중 여부를 코드가 직접 계산해 프레임을 만듦. 모델이나 데이터가 중간 상태를 적지 않게 하기 위함
# - 그림은 도형 위주: 칸(cell)에는 3글자 이하 값만, 설명은 캡션 한 줄에 둠
"""Computer architecture scene simulators."""
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .model import Animation, Arrow, Box, Frame, Strict, Value, build_animation, josa

# Frame.caption 상한. 값이 긴 장면은 짧은 문장으로 바꿈
CAPTION_MAX = 80


## @brief 캡션이 상한을 넘으면 짧은 대안 문장으로 바꿈
def fit(caption: str, short: str) -> str:
    return caption if len(caption) <= CAPTION_MAX else short


# --- 2진수와 2의 보수 ---

# 비트 행의 가장 낮은 자리(LSB) 열. 폭이 달라도 같은 자리끼리 세로로 맞춰 덧셈·부호 확장을 비교하게 함
LSB_X = 9


## @brief 2의 보수 장면: encode(10진 → 비트열), add(부호 있는 덧셈과 오버플로), extend(부호 확장)
# - bits 3~8: 비트 칸과 자릿값이 격자 한 줄(LSB_X 왼쪽 8칸)에 들어가는 폭
class TwosComplementRequest(Strict):
    kind: Literal['twos_complement']
    operation: Literal['encode', 'add', 'extend']
    bits: int = Field(ge=3, le=8)
    a: int = Field(ge=-128, le=127)
    b: int | None = Field(default=None, ge=-128, le=127)
    to_bits: int | None = Field(default=None, ge=4, le=8)

    ## @brief 피연산자가 bits 폭의 표현 범위 안인지, add의 b와 extend의 to_bits가 있는지 확인
    @model_validator(mode='after')
    def validate_operands(self):
        low, high = -2 ** (self.bits - 1), 2 ** (self.bits - 1) - 1
        for name in ('a', 'b'):
            value = getattr(self, name)
            if value is not None and not low <= value <= high:
                raise ValueError(f'{name}={value}: {self.bits}비트 2의 보수 범위({low}~{high}) 밖입니다')
        if self.operation == 'add' and self.b is None:
            raise ValueError('add에는 b가 필요합니다')
        if self.operation == 'extend' and (self.to_bits is None or self.to_bits <= self.bits):
            raise ValueError('extend에는 bits보다 큰 to_bits가 필요합니다')
        return self


## @brief value를 width비트 2의 보수 비트열(MSB부터)로 바꿈. 음수는 2^width를 더한 값의 비트와 같음
def bit_string(value: int, width: int) -> str:
    return format(value & ((1 << width) - 1), f'0{width}b')


## @brief 비트열을 부호 있는 정수로 읽음(MSB 자릿값이 -2^(width-1))
def signed(text: str) -> int:
    value = int(text, 2)
    return value - (1 << len(text)) if text[0] == '1' else value


## @brief 한 줄의 비트 칸을 LSB_X에 오른쪽 정렬해 만듦. id는 자리 번호(k=0이 LSB)로 붙여 같은 자리를 프레임 사이에 추적
def bit_boxes(prefix: str, y: int, text: str, tone='normal', tones=None) -> list[Box]:
    width = len(text)
    return [Box(id=f'{prefix}{k}', label=text[width - 1 - k], x=LSB_X - k, y=y, shape='cell',
                tone=(tones or {}).get(k, tone)) for k in range(width)]


## @brief 자릿값 이름표 줄(y=0). MSB만 음수(-2^(w-1)), 1인 자리는 active로 강조
def weight_boxes(text: str) -> list[Box]:
    width = len(text)
    return [Box(id=f'w{k}', label=str(-(1 << k) if k == width - 1 else 1 << k), x=LSB_X - k, y=0, shape='label',
                tone='active' if text[width - 1 - k] == '1' else 'normal') for k in range(width)]


## @brief 비트열의 1인 자리 자릿값을 더하는 검산 문장. 예: '-8 + 2 = -6'
def check_sum(text: str) -> str:
    width = len(text)
    terms = [-(1 << k) if k == width - 1 else 1 << k for k in reversed(range(width)) if text[width - 1 - k] == '1']
    return f'{" + ".join(map(str, terms)) or "0"} = {signed(text)}'


## @brief 비트 줄의 왼쪽 이름표(MSB 바로 왼쪽 칸)와 오른쪽(x=11) 10진 값 이름표. width는 그 줄의 비트 수
def side(row_id: str, y: int, width: int, left: str | None = None, right: str | None = None) -> list[Box]:
    boxes = [Box(id=f'{row_id}L', label=left, x=LSB_X - width, y=y, shape='label')] if left else []
    return boxes + ([Box(id=f'{row_id}R', label=right, x=11, y=y, shape='label')] if right is not None else [])


## @brief 10진 수를 비트열로: 음수는 |a| → 모든 비트 반전 → +1 순서로, 마지막은 자릿값 검산
def encode_frames(req: TwosComplementRequest) -> list[Frame]:
    a, width = req.a, req.bits
    result = bit_string(a, width)
    if a >= 0:
        row = bit_boxes('r', 1, result) + side('r', 1, width, right=str(a))
        return [Frame(boxes=row, arrows=[], caption=f'{a}{josa(a, "은", "는")} 양수: 그대로 {width}비트 2진수 {result}'),
                Frame(boxes=weight_boxes(result) + row, arrows=[], caption=f'검산: {check_sum(result)}')]
    plain = bit_string(-a, width)
    inverted = ''.join('1' if bit == '0' else '0' for bit in plain)
    first = bit_boxes('p', 1, plain) + side('p', 1, width, f'|{a}|', str(-a))
    second = first + bit_boxes('n', 2, inverted, 'new') + side('n', 2, width, 'NOT')
    third = first + bit_boxes('n', 2, inverted) + bit_boxes('r', 3, result, 'new') + side('r', 3, width, '+1', str(a))
    final = (weight_boxes(result) + first + bit_boxes('n', 2, inverted) + side('n', 2, width, 'NOT')
             + bit_boxes('r', 3, result, tones={k: 'active' for k in range(width) if result[width - 1 - k] == '1'})
             + side('r', 3, width, '+1', str(a)))
    return [Frame(boxes=first, arrows=[], caption=f'{-a}{josa(-a, "을", "를")} {width}비트 2진수로: {plain}'),
            Frame(boxes=second, arrows=[], caption=f'모든 비트 반전: {inverted}'),
            Frame(boxes=third + side('n', 2, width, 'NOT'), arrows=[], caption=f'1을 더하면 {result} = {a}'),
            Frame(boxes=final, arrows=[], caption=f'검산: {check_sum(result)}')]


## @brief LSB부터 한 자리씩 더하며 올림을 위 자리로 넘기고, 마지막에 부호 있는 오버플로를 판정
# - 오버플로 = 두 수의 부호가 같은데 결과 부호가 다름. 맨 앞 자리 올림(cout)은 버리며 오버플로와 다른 개념임
def add_frames(req: TwosComplementRequest) -> list[Frame]:
    width, a, b = req.bits, req.a, req.b
    x, y = bit_string(a, width), bit_string(b, width)
    operands = bit_boxes('a', 2, x) + side('a', 2, width, right=str(a)) + bit_boxes('b', 3, y) + side('b', 3, width, '+', str(b))
    frames = [Frame(boxes=operands, arrows=[], caption=f'{width}비트 덧셈: {a} + {b}')]
    carries, sums, carry = [], '', 0
    for k in range(width):
        bit_a, bit_b = int(x[width - 1 - k]), int(y[width - 1 - k])
        total = bit_a + bit_b + carry
        sums = str(total % 2) + sums
        carry = total // 2
        out_id = 'cout' if k == width - 1 else f'c{k + 1}'
        carries = [Box(**{**c.model_dump(), 'tone': 'normal'}) for c in carries]
        carries.append(Box(id=out_id, label=str(carry), x=LSB_X - k - 1, y=1, shape='cell', tone='new'))
        column = {k: 'active'}
        boxes = (bit_boxes('a', 2, x, tones=column) + side('a', 2, width, right=str(a)) + bit_boxes('b', 3, y, tones=column)
                 + side('b', 3, width, '+', str(b)) + carries + bit_boxes('s', 4, sums, tones={k: 'new'}))
        frames.append(Frame(boxes=boxes, arrows=[], caption=f'자리 {k}: {bit_a}+{bit_b}+올림 {total - bit_a - bit_b} = {total}'
                                                            f' → 쓰기 {total % 2}, 올림 {carry}'))
    result = signed(sums)
    overflow = (a >= 0) == (b >= 0) and (result >= 0) != (a >= 0)
    carries[-1] = Box(**{**carries[-1].model_dump(), 'tone': 'removed' if carry else 'normal'})
    boxes = operands + carries + bit_boxes('s', 4, sums, 'removed' if overflow else 'active') \
        + side('s', 4, width, '=', str(result))
    if overflow:
        low, high = -2 ** (width - 1), 2 ** (width - 1) - 1
        caption = f'{a + b}{josa(a + b, "은", "는")} 범위({low}~{high}) 밖: 결과 {result}, 부호 있는 오버플로입니다'
    else:
        caption = f'결과 {result}' + (': 맨 앞 올림 1은 버림, 오버플로 아님' if carry else ': 오버플로 아님')
    frames.append(Frame(boxes=boxes, arrows=[], caption=caption))
    return frames


## @brief 짧은 비트열을 넓은 폭으로 늘림: 아래 자리는 그대로, 새 위 자리는 모두 부호 비트로 채움
def extend_frames(req: TwosComplementRequest) -> list[Frame]:
    width, wide, a = req.bits, req.to_bits, req.a
    short, result = bit_string(a, width), bit_string(a, wide)
    original = bit_boxes('o', 1, short, tones={width - 1: 'active'}) + side('o', 1, width, f'{width}비트', str(a))
    copied = bit_boxes('e', 2, short)
    filled = [Box(id=f'e{k}', label=short[0], x=LSB_X - k, y=2, shape='cell', tone='new') for k in range(width, wide)]
    arrows = [Arrow(source=f'o{width - 1}', target=box.id) for box in filled]
    label = side('e', 2, wide, f'{wide}비트')
    return [Frame(boxes=original, arrows=[], caption=f'{a}의 {width}비트 표현 {short}, 부호 비트 {short[0]}'),
            Frame(boxes=original + bit_boxes('e', 2, short, 'new') + label, arrows=[],
                  caption=f'{wide}비트로 늘리기: 아래 {width}자리는 그대로 복사'),
            Frame(boxes=original + copied + filled + label, arrows=arrows,
                  caption=f'새 위 {wide - width}자리에 부호 비트 {short[0]}를 복사 → {result}'),
            Frame(boxes=weight_boxes(result) + original + bit_boxes('e', 2, result) + side('e', 2, wide, f'{wide}비트', str(a)),
                  arrows=[], caption=fit(f'검산: {check_sum(result)}, 값이 그대로', f'검산: 값 {a} 그대로'))]


## @brief 2의 보수 장면 실행. operation별 프레임 생성기로 보냄
def simulate_twos_complement(req: TwosComplementRequest) -> Animation:
    frames = {'encode': encode_frames, 'add': add_frames, 'extend': extend_frames}[req.operation](req)
    title = {'encode': f'2의 보수 표현: {req.a}', 'add': f'2의 보수 덧셈: {req.a} + {req.b}',
             'extend': f'부호 확장: {req.bits}→{req.to_bits}비트'}[req.operation]
    return build_animation('twos_complement', title, frames)


# --- 명령어 실행 사이클 ---

Var = Literal['A', 'B', 'C', 'D']
# 끝나지 않는 JUMP 반복을 막는 실행 횟수 상한(교육적 분량: 명령어 단위 장면도 MAX_FRAMES 안에 들어감)
MAX_STEPS = 12


## @brief 누산기(ACC) 방식 명령어 하나. LOAD·ADD·STORE는 변수(var), JUMP는 이동할 주소(target)를 받음
class CycleInstr(Strict):
    opcode: Literal['LOAD', 'ADD', 'STORE', 'JUMP']
    var: Var | None = None
    target: int | None = Field(default=None, ge=0, le=255)


## @brief 명령어 사이클 장면. detail='phase'는 인출·해독·실행마다, 'instruction'은 명령어마다 한 프레임
# - length: 명령어 길이(바이트). 인출 때 PC가 이만큼 늘어남
# - program 6개: 주소 칸을 한 열(y=1..6)에 세로로 그릴 수 있는 수
class InstructionCycleRequest(Strict):
    kind: Literal['instruction_cycle']
    length: Literal[1, 2, 4]
    start: int = Field(ge=0, le=200)
    detail: Literal['phase', 'instruction']
    memory: dict[Var, Value] = Field(min_length=1, max_length=4)
    program: list[CycleInstr] = Field(min_length=1, max_length=6)

    ## @brief JUMP는 프로그램 안 명령어 주소만, 나머지는 memory에 있는 변수만 쓰게 함
    @model_validator(mode='after')
    def validate_program(self):
        addresses = [self.start + i * self.length for i in range(len(self.program))]
        for address, ins in zip(addresses, self.program):
            if ins.opcode == 'JUMP':
                if ins.target not in addresses or ins.var is not None:
                    raise ValueError(f'주소 {address} JUMP: target은 명령어 주소 {addresses} 중 하나여야 합니다')
            elif ins.var not in self.memory or ins.target is not None:
                raise ValueError(f'주소 {address} {ins.opcode}: var는 memory에 있는 변수여야 합니다')
        return self


## @brief 명령어를 화면 글자로. 예: 'LOAD A', 'JUMP 12'
def instr_text(ins: CycleInstr) -> str:
    return f'JUMP {ins.target}' if ins.opcode == 'JUMP' else f'{ins.opcode} {ins.var}'


## @brief 명령어 사이클 한 프레임
# - 배치: 프로그램 메모리(x=0 주소 칸, x=2 명령어), CPU(x=4 이름표, x=5 PC·IR·ACC), 데이터 메모리(x=8 값 칸, x=9 이름)
# - tones: 이번 단계에서 강조할 상자 id → tone
def cycle_frame(req, pc, ir, acc, memory, caption, tones=None, arrows=()) -> Frame:
    tones = tones or {}
    boxes = []
    for i, ins in enumerate(req.program):
        address = req.start + i * req.length
        boxes += [Box(id=f'addr{i}', label=str(address), x=0, y=1 + i, shape='slot',
                      tone=tones.get(f'addr{i}', 'active' if address == pc and not tones else 'normal')),
                  Box(id=f'ins{i}', label=instr_text(ins), x=2, y=1 + i, shape='label')]
    boxes += [Box(id='pcL', label='PC', x=4, y=1, shape='label'),
              Box(id='pc', label=str(pc), x=5, y=1, shape='cell', tone=tones.get('pc', 'normal')),
              Box(id='irL', label='IR', x=4, y=3, shape='label'),
              Box(id='ir', label=ir, x=5, y=3, shape='label', tone=tones.get('ir', 'normal')),
              Box(id='accL', label='ACC', x=4, y=5, shape='label'),
              Box(id='acc', label=str(acc), x=5, y=5, shape='cell', tone=tones.get('acc', 'normal'))]
    for y, (name, value) in enumerate(sorted(memory.items()), start=1):
        boxes += [Box(id=f'mem{name}', label=str(value), x=8, y=y, shape='cell', tone=tones.get(f'mem{name}', 'normal')),
                  Box(id=f'var{name}', label=name, x=9, y=y, shape='label')]
    return Frame(boxes=boxes, arrows=[Arrow(source=s, target=t) for s, t in arrows], caption=caption)


## @brief 프로그램을 실제로 실행하며 단계별 프레임을 기록. PC가 프로그램 끝을 지나면 마지막 프레임으로 마침
# - 인출 단계에서 PC를 먼저 늘림(PC ← PC + 길이). JUMP는 실행 단계에서 PC를 덮어씀
def simulate_instruction_cycle(req: InstructionCycleRequest) -> Animation:
    index = {req.start + i * req.length: i for i in range(len(req.program))}
    pc, ir, acc, memory = req.start, '', 0, dict(req.memory)
    frames = [cycle_frame(req, pc, ir, acc, memory, f'시작: PC={pc}, 명령어 길이 {req.length}바이트')]
    steps = 0
    while pc in index:
        steps += 1
        if steps > MAX_STEPS:
            raise ValueError(f'끝나지 않는 반복: 실행이 {MAX_STEPS}번을 넘습니다. JUMP 대상을 바꾸세요')
        i, address = index[pc], pc
        ins = req.program[i]
        ir, pc = instr_text(ins), pc + req.length
        if req.detail == 'phase':
            frames.append(cycle_frame(req, pc, ir, acc, memory, f'① 인출: 주소 {address}의 {ir} → IR, PC {address}→{pc}',
                                      {f'addr{i}': 'active', 'ir': 'new', 'pc': 'new'}, [(f'addr{i}', 'ir')]))
            target = f'mem{ins.var}' if ins.var else 'pc'
            operand = f'변수 {ins.var}' if ins.var else f'주소 {ins.target}'
            frames.append(cycle_frame(req, pc, ir, acc, memory, f'② 해독: {ins.opcode}, 대상 {operand}',
                                      {'ir': 'active', target: 'active'}, [('ir', target)]))
        if ins.opcode == 'LOAD':
            acc = memory[ins.var]
            effect, tones, arrows = f'ACC ← {ins.var}({acc})', {'acc': 'new'}, [(f'mem{ins.var}', 'acc')]
        elif ins.opcode == 'ADD':
            acc += memory[ins.var]
            effect, tones, arrows = f'ACC ← ACC + {ins.var}({memory[ins.var]}) = {acc}', {'acc': 'new'}, [(f'mem{ins.var}', 'acc')]
        elif ins.opcode == 'STORE':
            memory[ins.var] = acc
            effect, tones, arrows = f'{ins.var} ← ACC({acc})', {f'mem{ins.var}': 'new'}, [('acc', f'mem{ins.var}')]
        else:
            pc = ins.target
            effect, tones, arrows = f'PC ← {pc}', {'pc': 'new'}, [('ir', 'pc')]
        if not -99 <= acc <= 999:
            raise ValueError(f'ACC 값 {acc}: 칸에 들어가는 범위(-99~999)를 넘습니다')
        if req.detail == 'phase':
            caption = f'③ 실행: {effect}'
        else:
            caption = f'주소 {address} {ir}: {effect}, 다음 PC {pc}'
            tones = {**tones, f'addr{i}': 'active'}
        frames.append(cycle_frame(req, pc, ir, acc, memory, caption, tones, arrows))
    frames.append(cycle_frame(req, pc, ir, acc, memory, f'PC={pc}: 다음 명령어 없음, 끝', {'pc': 'active'}))
    title = '명령어 실행 사이클' if req.detail == 'phase' else f'순차 실행과 PC(+{req.length})'
    return build_animation('instruction_cycle', title, frames)


# --- 파이프라이닝과 해저드 ---

Reg = Literal['r1', 'r2', 'r3', 'r4', 'r5', 'r6', 'r7', 'r8', 'r9']
# 그릴 수 있는 사이클 수: x=0·1은 명령어 이름표, x=2..11이 사이클 1..10
MAX_CYCLES = 10


## @brief 파이프라인 명령어 하나. add·sub는 원본 레지스터 1~2개, lw는 주소를 담은 레지스터 1개를 읽음
class PipeInstr(Strict):
    opcode: Literal['add', 'sub', 'lw']
    dest: Reg
    srcs: list[Reg] = Field(min_length=1, max_length=2)


## @brief 5단계(IF·ID·EX·MEM·WB) 파이프라인 장면. forwarding=False면 결과를 레지스터 파일(WB)로만 받음
class PipelineRequest(Strict):
    kind: Literal['pipeline']
    forwarding: bool
    instructions: list[PipeInstr] = Field(min_length=2, max_length=5)

    ## @brief lw의 원본 개수와 전체 사이클 수(격자 10칸)를 확인
    @model_validator(mode='after')
    def validate_pipeline(self):
        for ins in self.instructions:
            if ins.opcode == 'lw' and len(ins.srcs) != 1:
                raise ValueError('lw는 주소 레지스터 하나만 읽습니다(srcs 1개)')
        total = pipeline_schedule(self)[-1]['e'] + 2
        if total > MAX_CYCLES:
            raise ValueError(f'총 {total}사이클: 격자 {MAX_CYCLES}칸을 넘습니다. 명령어나 의존성을 줄이세요')
        return self


## @brief 명령어마다 IF(f)·ID(d)·EX(e) 시작 사이클과 의존 정보(deps)를 계산. MEM은 e+1, WB는 e+2
# - 순서대로만 진행: 앞 명령어가 ID를 떠나야(f_i = d_{i-1}) 뒤 명령어가 IF에 들어가고, 앞이 EX로 가야 ID로 감
# - RAW 의존(가장 최근에 dest를 쓴 앞 명령어 j): forwarding이면 ALU 결과는 EX 다음 사이클, lw는 MEM 다음 사이클부터 EX에 씀
#   forwarding이 없으면 WB 사이클에 레지스터에 쓰고 같은 사이클 ID에서 읽음(앞 반 쓰기·뒤 반 읽기) → EX는 WB 다음 사이클부터
def pipeline_schedule(req: PipelineRequest) -> list[dict]:
    rows = []
    for i, ins in enumerate(req.instructions):
        f = 1 if i == 0 else rows[-1]['d']
        d = 2 if i == 0 else max(f + 1, rows[-1]['e'])
        e, deps = d + 1, []
        for src in ins.srcs:
            j = next((j for j in range(i - 1, -1, -1) if req.instructions[j].dest == src), None)
            if j is None:
                continue
            producer = rows[j]
            if req.forwarding:
                ready = producer['e'] + (2 if req.instructions[j].opcode == 'lw' else 1)
            else:
                ready = producer['e'] + 3
            deps.append((j, src, ready))
            e = max(e, ready)
        rows.append({'f': f, 'd': d, 'e': e, 'deps': deps})
    return rows


## @brief 명령어 i의 사이클 → 단계 이름. 기다리는 사이클은 '-'
def stage_map(row: dict) -> dict[int, str]:
    stages = {row['f']: 'IF', row['d']: 'ID', row['e']: 'EX', row['e'] + 1: 'MEM', row['e'] + 2: 'WB'}
    for cycle in range(row['f'] + 1, row['e']):
        stages.setdefault(cycle, '-')
    return stages


## @brief 명령어 이름 글자. 예: 'add r1'
def pipe_name(ins: PipeInstr) -> str:
    return f'{ins.opcode} {ins.dest}'


## @brief 사이클별 프레임과 전달 화살표를 만듦
# - 화살표: forwarding이면 소비 명령어 EX 바로 전 사이클의 생산 명령어 칸(EX 또는 MEM) → 소비 명령어 EX 칸('fwd')
#   forwarding이 없으면 생산 명령어 WB 칸 → 소비 명령어가 같은 사이클에 레지스터를 읽는 칸
#   레지스터 파일에서 이미 읽을 수 있는 먼 의존은 화살표 없음. 두 원본이 같은 생산 명령어면 화살표 하나만 그림
def simulate_pipeline(req: PipelineRequest) -> Animation:
    rows = pipeline_schedule(req)
    total = rows[-1]['e'] + 2
    maps = [stage_map(row) for row in rows]
    links = []  # (나타나는 사이클, 출발 id, 도착 id, 라벨, 생산 명령어, 소비 명령어, 레지스터)
    for i, row in enumerate(rows):
        read = row['e'] - 1
        for j, src, _ in row['deps']:
            wb = rows[j]['e'] + 2
            if read > wb or (read == wb and req.forwarding):
                continue
            if req.forwarding:
                link = (row['e'], f'i{j}c{read}', f'i{i}c{row["e"]}', 'fwd', j, i, src)
            else:
                link = (read, f'i{j}c{read}', f'i{i}c{read}', None, j, i, src)
            if all(link[1:3] != other[1:3] for other in links):
                links.append(link)
    header = [Box(id=f'cy{c}', label=str(c), x=1 + c, y=0, shape='label') for c in range(1, total + 1)]
    names = []
    for i, ins in enumerate(req.instructions):
        names += [Box(id=f'n{i}', label=pipe_name(ins), x=0, y=1 + i, shape='label'),
                  Box(id=f's{i}', label=','.join(ins.srcs), x=1, y=1 + i, shape='label')]

    ## @brief cycle까지 진행한 표. 현재 사이클 열은 active, 기다리는 칸은 removed
    def table(cycle):
        boxes = []
        for i, stages in enumerate(maps):
            for c, stage in sorted(stages.items()):
                if c <= cycle:
                    tone = 'removed' if stage == '-' else 'active' if c == cycle else 'normal'
                    boxes.append(Box(id=f'i{i}c{c}', label=stage, x=1 + c, y=1 + i, shape='cell', tone=tone))
        arrows = [Arrow(source=s, target=t, label=label) for c, s, t, label, *_ in links if c <= cycle]
        return header + names + boxes, arrows

    mode = 'forwarding 켬' if req.forwarding else 'forwarding 끔'
    frames = [Frame(boxes=header + names, arrows=[], caption=f'명령어 {len(rows)}개, 5단계 파이프라인, {mode}')]
    for cycle in range(1, total + 1):
        boxes, arrows = table(cycle)
        caption = describe_cycle(req, rows, maps, links, cycle)
        frames.append(Frame(boxes=boxes, arrows=arrows, caption=caption))
    stalls = total - (len(rows) + 4)
    boxes, arrows = table(total)
    boxes = [Box(**{**b.model_dump(), 'tone': 'normal'}) if b.tone == 'active' else b for b in boxes]
    frames.append(Frame(boxes=boxes, arrows=arrows,
                        caption=f'총 {total}사이클(정지 {stalls}) · 겹치지 않으면 5×{len(rows)} = {5 * len(rows)}사이클'))
    return build_animation('pipeline', '파이프라인과 데이터 해저드', frames)


## @brief 한 사이클의 설명: 전달(forwarding)·레지스터 읽기 → 정지 → 동시에 처리되는 명령어 수 순서로 고름
def describe_cycle(req, rows, maps, links, cycle) -> str:
    for c, _, _, label, j, i, src in links:
        if c == cycle:
            producer, consumer = pipe_name(req.instructions[j]), pipe_name(req.instructions[i])
            obj = f'{src}{josa(int(src[1]), "을", "를")}'
            if label:
                return f'{cycle}: {producer}의 결과 {obj} {consumer} EX로 바로 전달(forwarding)'
            return (f'{cycle}: {producer}{josa(int(producer[-1]), "이", "가")} WB에서 {obj} 쓰고 '
                    f'{consumer}{josa(int(consumer[-1]), "이", "가")} 같은 사이클에 읽음')
    for i, row in enumerate(rows):
        if maps[i].get(cycle) == '-' and row['deps'] and cycle > row['d']:
            j, src, _ = max(row['deps'], key=lambda dep: dep[2])
            name = pipe_name(req.instructions[i])
            return f'{cycle}: {name}{josa(int(name[-1]), "이", "가")} {pipe_name(req.instructions[j])}의 {src} 값을 기다려 정지'
    busy = [i for i, stages in enumerate(maps) if stages.get(cycle, '-') != '-']
    if len(busy) == 1:
        return f'{cycle}: {pipe_name(req.instructions[busy[0]])} {maps[busy[0]][cycle]}'
    return f'{cycle}: 명령어 {len(busy)}개가 서로 다른 단계를 동시에 처리'


# --- 메모리 계층 ---

Item = Literal['a', 'b', 'c', 'd', 'e', 'f']
# 위(빠르고 작음)에서 아래(느리고 큼) 순서. 요청의 levels는 이 순서를 지켜야 함
LEVEL_ORDER = ('L1', 'L2', 'DRAM', 'SSD')


## @brief 계층 하나. latency는 이 계층을 확인하는 데 드는 시간(사이클), slots는 담을 수 있는 데이터 수
# - latency 999 이하: 4계층 합도 4자리라 접근 비용 이름표가 옆 칸 이름표와 붙지 않음
class Level(Strict):
    name: Literal['L1', 'L2', 'DRAM', 'SSD']
    slots: int = Field(ge=1, le=6)
    latency: int = Field(ge=1, le=999)


## @brief 메모리 계층 장면. 맨 아래 계층에 모든 데이터가 있고, 위 계층은 비어서 시작
# - 접근 비용 = 위에서부터 찾은 계층까지의 latency 합. 단일 캐시면 적중 시간 + (미스일 때) 미스 페널티와 같음
# - 찾은 데이터는 위 계층 모두에 복사하고, 가득 차면 가장 오래 안 쓴 것(LRU)을 내보냄
# - accesses 10개: 접근 줄(x=2..11)에 들어가는 수
class MemoryHierarchyRequest(Strict):
    kind: Literal['memory_hierarchy']
    levels: list[Level] = Field(min_length=2, max_length=4)
    accesses: list[Item] = Field(min_length=1, max_length=10)

    ## @brief 계층 이름 중복·순서, 맨 아래 계층 용량을 확인
    @model_validator(mode='after')
    def validate_levels(self):
        order = [LEVEL_ORDER.index(level.name) for level in self.levels]
        if order != sorted(set(order)):
            raise ValueError('levels는 L1 → L2 → DRAM → SSD 순서로 중복 없이 적어야 합니다')
        if self.levels[-1].slots < len(set(self.accesses)):
            raise ValueError(f'맨 아래 {self.levels[-1].name}에 데이터 {len(set(self.accesses))}개가 모두 들어가야 합니다')
        return self


## @brief 평균을 소수 둘째 자리까지 짧게. 예: 58.5, 20.75, 101
def short_number(value: float) -> str:
    return f'{round(value, 2):g}'


## @brief 접근마다 위 계층부터 찾아 비용을 누적하고, 마지막 프레임에 평균 접근 시간을 계산
# - 배치: 계층 행(3개 이하는 y=0,2,4 / 4개는 y=0..3; x=0 이름, x=1 latency, x=2.. 칸), y=6 접근한 데이터, y=7 비용
def simulate_memory_hierarchy(req: MemoryHierarchyRequest) -> Animation:
    levels = req.levels
    step = 2 if len(levels) <= 3 else 1
    bottom = sorted(set(req.accesses))
    slots = [[None] * level.slots for level in levels[:-1]] + [bottom + [None] * (levels[-1].slots - len(bottom))]
    recency = [[] for _ in levels[:-1]]  # 위 계층별 최근 사용 순서(뒤가 최근)
    done = []  # (데이터, 비용)

    ## @brief 현재 상태를 그림. tones는 (계층, 칸) → tone, names는 계층 번호 → 이름표 tone
    def frame(caption, tones=None, names=None, arrows=()):
        boxes = []
        for i, level in enumerate(levels):
            y = i * step
            boxes += [Box(id=f'lv{i}', label=level.name, x=0, y=y, shape='label', tone=(names or {}).get(i, 'normal')),
                      Box(id=f'lt{i}', label=str(level.latency), x=1, y=y, shape='label')]
            boxes += [Box(id=f'm{i}s{k}', label=item or '', x=2 + k, y=y, shape='cell', tone=(tones or {}).get((i, k), 'normal'))
                      for k, item in enumerate(slots[i])]
        for k, (item, cost) in enumerate(done):
            current = k == len(done) - 1 and tones is not None
            boxes += [Box(id=f'acc{k}', label=item, x=2 + k, y=6, shape='cell', tone='active' if current else 'normal'),
                      Box(id=f'cost{k}', label=str(cost), x=2 + k, y=7, shape='label')]
        return Frame(boxes=boxes, arrows=[Arrow(source=s, target=t) for s, t in arrows], caption=caption)

    shown = ' · '.join(f'{level.name} {level.latency}' for level in levels)
    frames = [frame(fit(f'접근 시간: {shown} (위일수록 빠르고 작음)', f'접근 시간: {shown}'))]
    for item in req.accesses:
        found = next(i for i in range(len(levels)) if item in slots[i])
        cost = sum(level.latency for level in levels[:found + 1])
        tones = {(found, slots[found].index(item)): 'active'}
        arrows, evicted, previous = [], [], f'm{found}s{slots[found].index(item)}'
        for i in range(found - 1, -1, -1):
            if None in slots[i]:
                k = slots[i].index(None)
            else:
                old = recency[i].pop(0)
                k = slots[i].index(old)
                evicted.append(f'{levels[i].name}의 {old}')
            slots[i][k] = item
            tones[(i, k)] = 'new'
            arrows.append((previous, f'm{i}s{k}'))
            previous = f'm{i}s{k}'
        for i in range(min(found + 1, len(levels) - 1)):
            if item in recency[i]:
                recency[i].remove(item)
            recency[i].append(item)
        done.append((item, cost))
        names = {i: 'removed' for i in range(found)} | {found: 'active'}
        if found == 0:
            caption = f'{item}: {levels[0].name} 적중 → {cost}'
        else:
            missed = '·'.join(level.name for level in levels[:found])
            terms = '+'.join(str(level.latency) for level in levels[:found + 1])
            caption = f'{item}: {missed} 미스 → {levels[found].name}에서 찾음, {terms} = {cost}'
            if evicted:
                caption = fit(f'{caption}, {", ".join(evicted)} 밀려남', caption)
        frames.append(frame(caption, tones, names, arrows))
    total = sum(cost for _, cost in done)
    average = short_number(total / len(done))
    caption = f'평균 접근 시간 = 총 {total} / {len(done)} = {average}'
    if len(levels) == 2:
        misses = sum(cost > levels[0].latency for _, cost in done)
        caption += f' = {levels[0].latency} + 미스율 {misses}/{len(done)} × {levels[1].latency}'
    frames.append(frame(caption))
    return build_animation('memory_hierarchy', '메모리 계층과 접근 시간', frames)


# --- 캐시와 지역성(직접 사상) ---

## @brief 직접 사상 캐시 장면. 블록 = 주소 ÷ 블록 크기, 라인 = 블록 mod 라인 수
# - lines 2·4: 라인 행(y=0..3)과 주소 분해 줄(y=4·5)·기록 줄(y=6·7)이 한 화면에 들어가는 수
class CacheRequest(Strict):
    kind: Literal['cache']
    block_size: Literal[1, 2, 4, 8, 16]
    lines: Literal[2, 4]
    addresses: list[Annotated[int, Field(ge=0, le=255)]] = Field(min_length=1, max_length=10)


## @brief 주소마다 블록·라인을 계산해 적중·미스(빈 라인, 다른 블록이 있던 충돌)를 기록하고 마지막에 적중률을 보임
# - 배치: 라인 행(x=0 번호, x=1 저장된 블록, x=2 바이트 범위), y=4·5 주소 → 블록 → 라인, y=6 접근 주소, y=7 적중·미스
def simulate_cache(req: CacheRequest) -> Animation:
    stored: list[int | None] = [None] * req.lines
    history = []  # (주소, 적중 여부)

    ## @brief 현재 캐시와 기록을 그림. current는 (주소, 블록, 라인, 라인 tone) 또는 None
    def frame(caption, current=None):
        boxes = []
        for k, block in enumerate(stored):
            tone = current[3] if current and current[2] == k else 'normal'
            boxes += [Box(id=f'idx{k}', label=str(k), x=0, y=k, shape='slot', tone='active' if tone != 'normal' else 'normal'),
                      Box(id=f'line{k}', label='' if block is None else str(block), x=1, y=k, shape='cell', tone=tone)]
            if block is not None:
                low = block * req.block_size
                span = str(low) if req.block_size == 1 else f'{low}-{low + req.block_size - 1}'
                boxes.append(Box(id=f'range{k}', label=span, x=2, y=k, shape='label'))
        arrows = []
        if current:
            address, block, line, _ = current
            boxes += [Box(id='addrL', label='주소', x=1, y=4, shape='label'),
                      Box(id='blkL', label='블록', x=4, y=4, shape='label'),
                      Box(id='lnL', label='라인', x=7, y=4, shape='label'),
                      Box(id='addr', label=str(address), x=1, y=5, shape='cell', tone='active'),
                      Box(id='blk', label=str(block), x=4, y=5, shape='cell', tone='active'),
                      Box(id='ln', label=str(line), x=7, y=5, shape='cell', tone='active')]
            arrows = [Arrow(source='addr', target='blk', label=f'÷{req.block_size}'),
                      Arrow(source='blk', target='ln', label=f'mod {req.lines}')]
        for k, (address, hit) in enumerate(history):
            latest = current is not None and k == len(history) - 1
            boxes += [Box(id=f'a{k}', label=str(address), x=1 + k, y=6, shape='cell', tone='active' if latest else 'normal'),
                      Box(id=f'hm{k}', label='적중' if hit else '미스', x=1 + k, y=7, shape='label')]
        return Frame(boxes=boxes, arrows=arrows, caption=caption)

    frames = [frame(f'직접 사상 캐시: 라인 {req.lines}개, 블록 {req.block_size}바이트')]
    for address in req.addresses:
        block = address // req.block_size
        line = block % req.lines
        head = f'주소 {address} → 블록 {block} → 라인 {line}'
        old = stored[line]
        if old == block:
            caption, tone = f'{head}: 적중', 'active'
        elif old is None:
            caption, tone = f'{head}: 비어 있어 미스, 블록 {block} 저장', 'new'
        else:
            caption, tone = f'{head}: 블록 {old} 자리라 충돌 미스, 교체', 'new'
        stored[line] = block
        history.append((address, old == block))
        frames.append(frame(caption, (address, block, line, tone)))
    hits = sum(hit for _, hit in history)
    frames.append(frame(f'적중 {hits}/{len(history)} = {short_number(100 * hits / len(history))}%'))
    return build_animation('cache', '직접 사상 캐시의 적중과 미스', frames)


# --- 가상 주소와 페이지 ---

# 물리 프레임 번호 범위 0..7(3비트). 물리 주소 비트 = 3 + 오프셋 비트
FRAME_BITS = 3
Frame8 = Annotated[int, Field(ge=0, le=7)]


## @brief 페이지 변환 장면. 가상 주소를 페이지 번호|오프셋으로 나누고 TLB → 페이지 테이블 → (없으면) 페이지 폴트 순서로 변환
# - page_table[p]: 페이지 p가 있는 물리 프레임, None이면 메모리에 없음(SSD에만 있음)
# - free_frames: 페이지 폴트 때 앞에서부터 쓰는 빈 프레임. tlb_size=0이면 TLB 없이 표만 봄
# - 페이지 8개·주소 8비트·접근 3개: 표·비트 줄이 격자 한 화면에, 접근당 3~4단계가 12단계 안에 들어가는 분량
class PagingRequest(Strict):
    kind: Literal['paging']
    address_bits: int = Field(ge=4, le=8)
    offset_bits: int = Field(ge=1, le=6)
    page_table: list[Frame8 | None] = Field(min_length=2, max_length=8)
    free_frames: list[Frame8] = Field(default_factory=list, max_length=4)
    tlb_size: int = Field(default=0, ge=0, le=4)
    addresses: list[Annotated[int, Field(ge=0, le=255)]] = Field(min_length=1, max_length=3)

    ## @brief 표 길이 = 2^(주소 비트 - 오프셋 비트), 주소 범위, 프레임 중복을 확인
    @model_validator(mode='after')
    def validate_paging(self):
        pages = 2 ** (self.address_bits - self.offset_bits)
        if len(self.page_table) != pages:
            raise ValueError(f'page_table은 페이지 수 {pages}개(2^(주소 비트 - 오프셋 비트))여야 합니다')
        for address in self.addresses:
            if address >= 2 ** self.address_bits:
                raise ValueError(f'주소 {address}: {self.address_bits}비트 범위를 넘습니다')
        used = [f for f in self.page_table if f is not None] + self.free_frames
        if len(set(used)) != len(used):
            raise ValueError('같은 물리 프레임을 두 번 쓸 수 없습니다(page_table·free_frames)')
        return self


## @brief 접근마다 분해 → 조회(TLB·표, 폴트면 적재 단계 추가) → 물리 주소 단계를 그림
# - 배치: y=0 가상 주소 비트, y=1 p·d, y=3 TLB, y=5 페이지 테이블(x=10 SSD), y=6 f·d, y=7 물리 주소 비트
def simulate_paging(req: PagingRequest) -> Animation:
    table = list(req.page_table)
    free = list(req.free_frames)
    tlb: list[tuple[int, int]] = []  # (페이지, 프레임), 앞이 가장 먼저 들어온 것
    page_bits, offset_bits = req.address_bits - req.offset_bits, req.offset_bits
    size = 2 ** offset_bits

    ## @brief 현재 상태 한 프레임. va·pa는 (주소, 페이지·프레임, 오프셋) 또는 None, tones는 id → tone
    def frame(caption, va=None, pa=None, tones=None, arrows=()):
        tones = tones or {}
        boxes = []
        if va:
            address, page, offset = va
            text = format(address, f'0{req.address_bits}b')
            boxes += [Box(id=f'va{k}', label=bit, x=1 + k, y=0, shape='cell', tone='active' if k < page_bits else 'normal')
                      for k, bit in enumerate(text)]
            boxes += [Box(id='vav', label=f'={address}', x=11, y=0, shape='label'),
                      Box(id='vp', label=f'p={page}', x=1 + (page_bits - 1) // 2, y=1, shape='label'),
                      Box(id='vd', label=f'd={offset}', x=1 + page_bits + (offset_bits - 1) // 2, y=1, shape='label')]
        if req.tlb_size:
            boxes.append(Box(id='tlbL', label='TLB', x=0, y=3, shape='label'))
            boxes += [Box(id=f'tlb{k}', label=f'{p}:{f}' if k < len(tlb) else '', x=1 + k, y=3, shape='cell',
                          tone=tones.get(f'tlb{k}', 'normal'))
                      for k, (p, f) in enumerate(tlb + [(None, None)] * (req.tlb_size - len(tlb)))]
        boxes.append(Box(id='ptL', label='표', x=0, y=5, shape='label'))
        # 표 칸은 TLB와 같은 '페이지:프레임' 표기(없으면 '-'). 번호 줄을 따로 그리면 상자 수 상한(40)을 넘기 때문
        boxes += [Box(id=f'pt{p}', label=f'{p}:{"-" if f is None else f}', x=1 + p, y=5, shape='cell',
                      tone=tones.get(f'pt{p}', 'normal')) for p, f in enumerate(table)]
        boxes.append(Box(id='ssd', label='SSD', x=10, y=5, shape='slot', tone=tones.get('ssd', 'normal')))
        if pa:
            address, f, offset = pa
            text = format(address, f'0{FRAME_BITS + offset_bits}b')
            boxes += [Box(id=f'pa{k}', label=bit, x=1 + k, y=7, shape='cell', tone='active' if k < FRAME_BITS else 'normal')
                      for k, bit in enumerate(text)]
            boxes += [Box(id='pav', label=f'={address}', x=11, y=7, shape='label'),
                      Box(id='pf', label=f'f={f}', x=1 + (FRAME_BITS - 1) // 2, y=6, shape='label'),
                      Box(id='pd', label=f'd={offset}', x=1 + FRAME_BITS + (offset_bits - 1) // 2, y=6, shape='label')]
        return Frame(boxes=boxes, arrows=[Arrow(source=s, target=t) for s, t in arrows], caption=caption)

    frames = []
    for address in req.addresses:
        page, offset = address // size, address % size
        va = (address, page, offset)
        text = format(address, f'0{req.address_bits}b')
        frames.append(frame(f'가상 주소 {address} = {text[:page_bits]}|{text[page_bits:]} → 페이지 {page}, 오프셋 {offset}', va))
        cached = next((k for k, (p, _) in enumerate(tlb) if p == page), None)
        prefix = 'TLB 미스 → ' if req.tlb_size else ''
        if cached is not None:
            f = tlb[cached][1]
            frames.append(frame(f'TLB 적중: 페이지 {page} → 프레임 {f}', va, tones={f'tlb{cached}': 'active'}))
        else:
            if table[page] is None:
                frames.append(frame(f'{prefix}페이지 {page}{josa(page, "이", "가")} 메모리에 없음: 페이지 폴트', va,
                                    tones={f'pt{page}': 'removed'}))
                if not free:
                    raise ValueError(f'페이지 {page}: 빈 프레임이 없어 페이지 폴트를 처리할 수 없습니다. free_frames를 주세요')
                table[page] = free.pop(0)
                caption = f'SSD에서 페이지 {page}{josa(page, "을", "를")} 프레임 {table[page]}에 적재, 표 갱신'
                frames.append(frame(caption, va, tones={f'pt{page}': 'new', 'ssd': 'active'}, arrows=[('ssd', f'pt{page}')]))
            else:
                where = f'{prefix}페이지 테이블' if prefix else '페이지 테이블'
                tones = {f'pt{page}': 'active'}
                if req.tlb_size:
                    tones.update(remember(tlb, req.tlb_size, page, table[page]))
                    caption = f'{where}: 페이지 {page} → 프레임 {table[page]}, TLB에 기록'
                else:
                    caption = f'{where}: 페이지 {page} → 프레임 {table[page]}'
                frames.append(frame(caption, va, tones=tones))
            f = table[page]
            if req.tlb_size and all(p != page for p, _ in tlb):
                remember(tlb, req.tlb_size, page, f)
        physical = f * size + offset
        frames.append(frame(f'물리 주소 = {f}×{size} + {offset} = {physical}', va, (physical, f, offset)))
    return build_animation('paging', '가상 주소 → 물리 주소 변환', frames)


## @brief TLB에 (페이지, 프레임)을 넣고 새 칸의 tone을 돌려줌. 가득 차면 가장 먼저 들어온 항목을 내보냄(FIFO)
def remember(tlb: list, capacity: int, page: int, frame_number: int) -> dict:
    if len(tlb) == capacity:
        tlb.pop(0)
    tlb.append((page, frame_number))
    return {f'tlb{len(tlb) - 1}': 'new'}


# --- 등록용 묶음(공유 파일에는 아래 이름만 한 줄씩 추가) ---

ARCH_REQUESTS = (TwosComplementRequest, InstructionCycleRequest, PipelineRequest, MemoryHierarchyRequest, CacheRequest,
                 PagingRequest)

ARCH_SIMULATORS = {
    'twos_complement': simulate_twos_complement,
    'instruction_cycle': simulate_instruction_cycle,
    'pipeline': simulate_pipeline,
    'memory_hierarchy': simulate_memory_hierarchy,
    'cache': simulate_cache,
    'paging': simulate_paging,
}

# 장면 선택에 넣는 종류별 형식 예시(SCENE_FORMATS와 같은 용도). 짧고 평범한 값만 둠
ARCH_FORMATS = {
    'twos_complement': {'kind': 'twos_complement', 'operation': 'encode', 'bits': 4, 'a': -3},
    'instruction_cycle': {'kind': 'instruction_cycle', 'length': 4, 'start': 0, 'detail': 'phase', 'memory': {'A': 1},
                          'program': [{'opcode': 'LOAD', 'var': 'A'}]},
    'pipeline': {'kind': 'pipeline', 'forwarding': True, 'instructions': [{'opcode': 'add', 'dest': 'r1', 'srcs': ['r2']},
                                                                          {'opcode': 'sub', 'dest': 'r3', 'srcs': ['r1']}]},
    'memory_hierarchy': {'kind': 'memory_hierarchy', 'accesses': ['a', 'a'],
                         'levels': [{'name': 'L1', 'slots': 1, 'latency': 1}, {'name': 'DRAM', 'slots': 1, 'latency': 100}]},
    'cache': {'kind': 'cache', 'block_size': 4, 'lines': 2, 'addresses': [0, 4]},
    'paging': {'kind': 'paging', 'address_bits': 6, 'offset_bits': 4, 'page_table': [1, 0, None, 2], 'addresses': [20]},
}
