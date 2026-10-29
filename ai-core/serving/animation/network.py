## @file
# @brief 네트워크 과목 장면 요청 모델과 시뮬레이터: 계층 캡슐화, 서브넷 계산, 링크 상태 라우팅, 최장 접두사 일치,
# TCP 연결·신뢰 전송, 흐름 제어 윈도우, 혼잡 윈도우, DNS 조회
# - 다른 장면과 같이 모델은 장면 종류와 입력만 고르고, 주소·순서 번호·윈도우·TTL 같은 단계별 값은 여기서 계산함
# - 공유 파일(model.py·__init__.py)에는 아래 NetworkRequest·NETWORK_SIMULATORS·NETWORK_FORMATS·NETWORK_SCENES만 등록
#   과목별 워커가 같은 파일에 장면을 등록하므로 충돌 줄을 줄이기 위함
# - model.py가 AnimationRequest 합집합을 만들 때 이 모듈을 읽으므로, 여기서는 model.py의 앞부분(Strict·Box 등)만 씀
"""Network subject scene simulators."""
import re
from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from .model import Animation, Arrow, Box, Frame, Strict, build_animation, josa


## @brief 옥텟 4개를 점 표기 문자열로
def ip(octets) -> str:
    return '.'.join(map(str, octets))


## @brief 옥텟 4개를 32비트 정수로
def to_int(octets) -> int:
    return int.from_bytes(bytes(octets), 'big')


## @brief 32비트 정수를 옥텟 4개로
def to_octets(value: int) -> list[int]:
    return list(value.to_bytes(4, 'big'))


## @brief 접두사 길이의 서브넷 마스크(32비트 정수)
def mask_of(prefix: int) -> int:
    return (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF


# --- 계층 모델 ---

# 헤더 크기(바이트): 옵션 없는 TCP·IPv4 헤더 20, 이더넷 헤더 14 + FCS 4
TCP_HEADER, IP_HEADER, ETH_HEADER, ETH_TRAILER = 20, 20, 14, 4
# 이더넷 MTU 1500에서 TCP·IP 헤더를 뺀 최대 데이터(MSS): 한 프레임에 담을 수 있는 응용 데이터 상한
MAX_SEGMENT_DATA = 1500 - TCP_HEADER - IP_HEADER


## @brief 계층 모델 장면
# - send: 송신 호스트에서 계층마다 헤더가 붙는 캡슐화와 단위별 크기
# - router: 호스트 → 라우터 → 호스트로 갈 때 라우터는 인터넷 계층까지만 열어 보는 모습
# - osi: OSI 7계층이 TCP/IP 4계층에 대응하는 관계
# - data_bytes는 send에서만 씀. 1460은 이더넷 한 프레임에 들어가는 상한
class EncapsulationRequest(Strict):
    kind: Literal['encapsulation']
    mode: Literal['send', 'router', 'osi']
    data_bytes: int = Field(default=100, ge=1, le=MAX_SEGMENT_DATA)


# 송신 캡슐화의 행(y)별 계층 이름·붙는 칸·단위 이름. 데이터 칸을 x=5에 맞춰 헤더가 왼쪽으로 쌓이는 계단을 그림
# - 칸 안 글자는 영문 약어: 한글 세 글자는 칸 폭을 넘쳐 옆 칸과 겹치기 때문. 한글 단위 이름은 오른쪽 이름표에 적음
SEND_LAYERS = [
    (1, '응용', [(5, 'DATA')], '데이터'),
    (2, '전송', [(4, 'TCP'), (5, 'DATA')], '세그먼트'),
    (3, '인터넷', [(3, 'IP'), (4, 'TCP'), (5, 'DATA')], '패킷'),
    (4, '링크', [(2, 'Eth'), (3, 'IP'), (4, 'TCP'), (5, 'DATA'), (6, 'FCS')], '프레임'),
]


## @brief 송신 캡슐화. 계층 하나에 프레임 하나, 이미 지나온 계층 줄은 남겨 헤더가 늘어나는 계단을 보여 줌
def simulate_send(req: EncapsulationRequest) -> list[Frame]:
    data = req.data_bytes
    sizes = [data, data + TCP_HEADER, data + TCP_HEADER + IP_HEADER,
             data + TCP_HEADER + IP_HEADER + ETH_HEADER + ETH_TRAILER]
    captions = [f'응용 계층: 보낼 데이터 {sizes[0]}B',
                f'전송 계층: TCP 헤더 {TCP_HEADER}B → 세그먼트 {sizes[1]}B',
                f'인터넷 계층: IP 헤더 {IP_HEADER}B → 패킷 {sizes[2]}B',
                f'링크 계층: 이더넷 헤더 {ETH_HEADER}B·FCS {ETH_TRAILER}B → 프레임 {sizes[3]}B']
    frames, boxes = [], []
    for (y, name, parts, unit), size, caption in zip(SEND_LAYERS, sizes, captions):
        boxes = [Box(**{**b.model_dump(), 'tone': 'normal'}) for b in boxes]
        boxes.append(Box(id=f'layer{y}', label=name, x=0, y=y, shape='label'))
        previous = {label for _, label in SEND_LAYERS[y - 2][2]} if y > 1 else set()
        boxes += [Box(id=f'{y}-{label}', label=label, x=x, y=y, shape='cell',
                      tone='active' if label in previous else 'new') for x, label in parts]
        boxes += [Box(id=f'size{y}', label=f'{size}B', x=8, y=y, shape='label'),
                  Box(id=f'unit{y}', label=unit, x=10, y=y, shape='label')]
        frames.append(Frame(boxes=boxes, arrows=[], caption=caption))
    boxes = [Box(**{**b.model_dump(), 'tone': 'normal'}) for b in boxes]
    boxes += [Box(id='layer5', label='물리', x=0, y=5, shape='label'),
              Box(id='bits', label='0101', x=4, y=5, shape='cell', tone='new')]
    frames.append(Frame(boxes=boxes, arrows=[], caption=(
        f'물리 계층: 프레임 {sizes[3]}B를 비트 신호로 보냄(붙은 헤더 합 {sizes[3] - data}B)')))
    return frames


# 호스트 A(x=1)·라우터(x=5)·호스트 B(x=9)의 계층 칸. 라우터는 인터넷·링크 계층만 있음
ROUTER_DEVICES = [('A', 1, '호스트A', (1, 2, 3, 4)), ('R', 5, '라우터', (3, 4)), ('B', 9, '호스트B', (1, 2, 3, 4))]
LAYER_SHORT = {1: '응용', 2: '전송', 3: 'IP', 4: '링크'}
# 패킷의 이동 경로: (패킷 칸 x, y, 단위 약어, 강조할 계층 칸 id, 설명)
# - 패킷은 계층 칸 오른쪽(x+1)에, 선로 위에서는 y=5에 둠
# - 단위 약어: DATA 데이터, SEG 세그먼트, PKT 패킷, FRM 프레임, BITS 비트(한글은 칸 폭을 넘쳐 영문 약어로 씀)
ROUTER_PATH = [
    (2, 1, 'DATA', 'A-1', 'A 응용 계층이 보낼 데이터를 만듦'),
    (2, 2, 'SEG', 'A-2', 'A 전송 계층: TCP 헤더를 붙여 세그먼트'),
    (2, 3, 'PKT', 'A-3', 'A 인터넷 계층: 목적지 B의 IP 주소를 담은 IP 헤더'),
    (2, 4, 'FRM', 'A-4', 'A 링크 계층: 다음 장비(라우터) 주소로 프레임 헤더'),
    (3, 5, 'BITS', None, '물리 선로로 비트 전송 → 라우터'),
    (6, 4, 'FRM', 'R-4', '라우터 링크 계층: 프레임 헤더를 벗김'),
    (6, 3, 'PKT', 'R-3', '라우터 인터넷 계층: 목적지 IP로 다음 홉 결정(TCP는 열지 않음)'),
    (6, 4, 'FRM', 'R-4', '라우터 링크 계층: 다음 링크용 새 프레임 헤더를 씌움'),
    (7, 5, 'BITS', None, '물리 선로로 비트 전송 → 호스트B'),
    (10, 4, 'FRM', 'B-4', 'B 링크 계층: 프레임 헤더를 벗김'),
    (10, 3, 'PKT', 'B-3', 'B 인터넷 계층: 목적지 IP가 자기 주소임을 확인하고 IP 헤더를 벗김'),
    (10, 1, 'DATA', 'B-1', 'B 전송 계층이 TCP 헤더를 벗기고 응용 계층에 데이터 전달'),
]


## @brief 라우터를 지나는 전달. 패킷 칸이 계층을 오르내리며, 라우터에서는 인터넷 계층까지만 올라감
def simulate_router() -> list[Frame]:
    frames = []
    for px, py, unit, layer, caption in ROUTER_PATH:
        boxes = []
        for key, x, name, layers in ROUTER_DEVICES:
            boxes.append(Box(id=f'dev{key}', label=name, x=x, y=0, shape='label'))
            boxes += [Box(id=f'{key}-{y}', label=LAYER_SHORT[y], x=x, y=y, shape='cell',
                          tone='active' if f'{key}-{y}' == layer else 'normal') for y in layers]
        boxes.append(Box(id='pkt', label=unit, x=px, y=py, shape='cell', tone='new'))
        frames.append(Frame(boxes=boxes, arrows=[], caption=caption))
    return frames


# OSI 계층 번호·이름 → 대응하는 TCP/IP 계층 번호(4 응용, 3 전송, 2 인터넷, 1 링크)
OSI_LAYERS = [(7, '응용', 4), (6, '표현', 4), (5, '세션', 4), (4, '전송', 3), (3, '네트워크', 2), (2, '데이터링크', 1),
              (1, '물리', 1)]
# TCP/IP 계층 칸의 행: 대응하는 OSI 계층 줄들의 가운데
TCPIP_LAYERS = {4: (1, '응용'), 3: (3, '전송'), 2: (4, '인터넷'), 1: (5, '링크')}
OSI_STEPS = [(4, 'OSI 응용·표현·세션(7·6·5) → TCP/IP 응용 계층(HTTP·DNS)'),
             (3, 'OSI 전송(4) → TCP/IP 전송 계층(TCP·UDP)'),
             (2, 'OSI 네트워크(3) → TCP/IP 인터넷 계층(IP)'),
             (1, 'OSI 데이터링크·물리(2·1) → TCP/IP 링크 계층(이더넷)')]


## @brief OSI 7계층과 TCP/IP 4계층의 대응. TCP/IP 계층 하나에 프레임 하나씩 대응 화살표를 늘림
def simulate_osi() -> list[Frame]:
    def frame(done, current, caption):
        boxes = [Box(id='osiTitle', label='OSI 7', x=3, y=7, shape='label'),
                 Box(id='tcpTitle', label='TCP/IP 4', x=7, y=7, shape='label')]
        for number, name, target in OSI_LAYERS:
            tone = 'active' if target == current else 'normal'
            boxes += [Box(id=f'osiName{number}', label=name, x=1, y=7 - number, shape='label'),
                      Box(id=f'osi{number}', label=str(number), x=3, y=7 - number, shape='cell', tone=tone)]
        arrows = []
        for target in done:
            y, name = TCPIP_LAYERS[target]
            boxes += [Box(id=f'tcp{target}', label=str(target), x=7, y=y, shape='cell',
                          tone='new' if target == current else 'normal'),
                      Box(id=f'tcpName{target}', label=name, x=9, y=y, shape='label')]
            arrows += [Arrow(source=f'osi{number}', target=f'tcp{target}') for number, _, t in OSI_LAYERS if t == target]
        return Frame(boxes=boxes, arrows=arrows, caption=caption)

    frames = [frame([], None, 'OSI 7계층: 위(7 응용)부터 아래(1 물리)까지 통신 기능을 나눔')]
    done = []
    for target, caption in OSI_STEPS:
        done.append(target)
        frames.append(frame(done, target, caption))
    frames.append(frame(done, None, 'TCP/IP 4계층: 위 세 계층을 응용 하나로, 아래 두 계층을 링크 하나로 묶음'))
    return frames


TITLES = {'send': '송신 캡슐화', 'router': '라우터를 지나는 계층', 'osi': 'OSI 7계층과 TCP/IP 4계층'}


## @brief 계층 모델 장면 실행. mode별 프레임 목록을 만들어 Animation으로 묶음
def simulate_encapsulation(req: EncapsulationRequest) -> Animation:
    frames = {'send': lambda: simulate_send(req), 'router': simulate_router, 'osi': simulate_osi}[req.mode]()
    return build_animation('encapsulation', TITLES[req.mode], frames)


# --- IPv4 서브넷 ---

## @brief 서브넷 장면
# - mask: 주소와 접두사로 네트워크 주소·브로드캐스트 주소·호스트 수를 비트로 계산
# - split: 넷째 옥텟 안에서 같은 크기로 나눔(new_prefix 필요, 빌리는 비트 1~3개 = 서브넷 2~8개)
# - same: 두 주소(address·other)에 같은 마스크를 AND해 같은 서브넷인지 판단(other 필요)
# - 접두사 8~30: /31·/32는 호스트 수 공식(2^h − 2)이 맞지 않아 뺌
class SubnetRequest(Strict):
    kind: Literal['subnet']
    mode: Literal['mask', 'split', 'same']
    address: list[int] = Field(min_length=4, max_length=4)
    prefix: int = Field(ge=8, le=30)
    new_prefix: int | None = Field(default=None, ge=9, le=30)
    other: list[int] | None = Field(default=None, min_length=4, max_length=4)

    ## @brief 옥텟 범위, split의 기준 주소·빌리는 비트 수, same의 짝 주소를 확인
    @model_validator(mode='after')
    def validate_subnet(self):
        for octets in (self.address, self.other or []):
            if any(not 0 <= o <= 255 for o in octets):
                raise ValueError('옥텟은 0~255여야 합니다')
        if self.mode == 'split':
            if self.new_prefix is None or not self.prefix < self.new_prefix <= self.prefix + 3:
                raise ValueError('split에는 prefix보다 1~3 큰 new_prefix가 필요합니다(서브넷 2~8개)')
            if self.prefix < 24:
                raise ValueError('split은 넷째 옥텟 안에서만 나눕니다(prefix 24 이상)')
            if to_int(self.address) & ~mask_of(self.prefix) & 0xFFFFFFFF:
                raise ValueError(f'기준 주소 {ip(self.address)}에 호스트 비트가 남아 있습니다. 네트워크 주소를 쓰세요')
        if self.mode == 'same' and self.other is None:
            raise ValueError('same에는 비교할 주소 other가 필요합니다')
        return self


## @brief 접두사 경계가 들어 있는 옥텟 번호(0~3)와 그 옥텟 안의 네트워크 비트 수(0~8)
def boundary(prefix: int) -> tuple[int, int]:
    index = min(prefix // 8, 3)
    return index, prefix - 8 * index


## @brief 한 줄에 옥텟 4칸(x=1..4)과 이름표(x=0)를 그림. active 옥텟만 강조
def octet_row(key, name, octets, y, tone='normal', active=None) -> list[Box]:
    boxes = [Box(id=f'{key}Name', label=name, x=0, y=y, shape='label')] if name else []
    return boxes + [Box(id=f'{key}{i}', label=str(o), x=1 + i, y=y, shape='cell',
                        tone='active' if i == active else tone) for i, o in enumerate(octets)]


## @brief 마스크 계산. 경계 옥텟을 8비트로 펼쳐 마스크와 AND, 호스트 비트를 0·1로 채워 두 주소를 구함
def simulate_mask(req: SubnetRequest) -> list[Frame]:
    index, net_bits = boundary(req.prefix)
    value = req.address[index]
    host = 32 - req.prefix
    network = to_octets(to_int(req.address) & mask_of(req.prefix))
    broadcast = to_octets(to_int(req.address) | (~mask_of(req.prefix) & 0xFFFFFFFF))
    bits = format(value, '08b')

    # 비트 줄(이름표 + 8칸). 상자 상한(40개) 안에 들도록 경계 옥텟 줄은 이름표 없이, 결과 줄은 0·1 채움을 한 줄에서 바꿔 그림
    # - 접두사 길이는 마스크 줄 이름표에 함께 적음
    def bit_row(key, name, text, y, new=False):
        label = [Box(id=f'{key}Name', label=name, x=0, y=y, shape='label')] if name else []
        return label + [Box(id=f'{key}{i}', label=b, x=2 + i, y=y, shape='cell',
                            tone='new' if new and i >= net_bits else 'active' if i < net_bits else 'normal')
                        for i, b in enumerate(text)]

    def calm(boxes):
        return [Box(**{**b.model_dump(), 'tone': 'normal'}) for b in boxes]

    top = octet_row('addr', '', req.address, 0, active=index)
    rows = bit_row('bits', '', bits, 2)
    frames = [Frame(boxes=top, arrows=[], caption=f'{ip(req.address)}/{req.prefix}: 앞 {req.prefix}비트 네트워크, 뒤 {host}비트 호스트'),
              Frame(boxes=top + rows, arrows=[], caption=f'{index + 1}번째 옥텟 {value}{josa(value, "을", "를")} 2진수로: {bits}')]
    mask_bits = '1' * net_bits + '0' * (8 - net_bits)
    rows += bit_row('mask', f'마스크/{req.prefix}', mask_bits, 3)
    frames.append(Frame(boxes=top + rows, arrows=[], caption=f'마스크 이 옥텟 = {mask_bits}({int(mask_bits, 2)}): 1인 자리가 네트워크 비트'))
    found = octet_row('network', '', network, 6, tone='new') + [Box(id='networkName', label='네트워크', x=6, y=6, shape='label')]
    frames.append(Frame(boxes=top + rows + bit_row('result', '0으로', bits[:net_bits] + '0' * (8 - net_bits), 4, True) + found,
                        arrows=[], caption=f'호스트 비트를 모두 0 → 네트워크 주소 {ip(network)}'))
    found = calm(found) + octet_row('broadcast', '', broadcast, 7, tone='new') + [
        Box(id='broadcastName', label='브로드캐스트', x=6, y=7, shape='label')]
    result = bit_row('result', '1로', bits[:net_bits] + '1' * (8 - net_bits), 4, True)
    frames.append(Frame(boxes=top + rows + result + found, arrows=[], caption=f'호스트 비트를 모두 1 → 브로드캐스트 주소 {ip(broadcast)}'))
    frames.append(Frame(boxes=top + calm(rows + result + found), arrows=[], caption=(
        f'할당 가능 호스트 = 2^{host} − 2 = {2 ** host - 2}개(네트워크·브로드캐스트 주소 제외)')))
    return frames


## @brief 같은 크기로 나누기. 주소 블록을 8칸 막대로 그리고, 비트를 하나 빌릴 때마다 칸을 둘로 쪼갬
# - 블록은 넷째 옥텟 안이므로 각 서브넷 범위를 '.시작~.끝'으로 짧게 적음
def simulate_split(req: SubnetRequest) -> list[Frame]:
    start = req.address[3]
    size = 2 ** (32 - req.prefix)

    def frame(prefix, caption, fresh):
        parts = 2 ** (prefix - req.prefix)
        width = size // parts
        boxes = [Box(id='lo', label=f'.{start}', x=2, y=2, shape='label'),
                 Box(id='hi', label=f'.{start + size - 1}', x=9, y=2, shape='label')]
        # 막대 8칸: 칸 하나 = 블록의 1/8. 서브넷 번호가 바뀔 때마다 강조를 번갈아 경계를 보이게 함
        boxes += [Box(id=f'bar{i}', label=f'S{i * parts // 8 + 1}' if parts > 1 else '', x=2 + i, y=1, shape='cell',
                      tone=('new' if fresh else 'active') if (i * parts // 8) % 2 else 'normal') for i in range(8)]
        if parts > 1:
            boxes += [Box(id=f'sub{j}', label=f'S{j + 1} .{start + j * width}~.{start + (j + 1) * width - 1}',
                          x=1 + 6 * (j // 4), y=4 + j % 4, shape='label') for j in range(parts)]
        return Frame(boxes=boxes, arrows=[], caption=caption)

    frames = [frame(req.prefix, f'{ip(req.address)}/{req.prefix}: 주소 {size}개(.{start}~.{start + size - 1})', False)]
    for prefix in range(req.prefix + 1, req.new_prefix + 1):
        parts, width = 2 ** (prefix - req.prefix), size // 2 ** (prefix - req.prefix)
        frames.append(frame(prefix, f'호스트 비트 {prefix - req.prefix}개 빌림(/{prefix}): 서브넷 {parts}개, 각 주소 {width}개', True))
    parts, hosts = 2 ** (req.new_prefix - req.prefix), 2 ** (32 - req.new_prefix) - 2
    frames.append(frame(req.new_prefix, f'결과 /{req.new_prefix}: 서브넷 {parts}개 × 할당 가능 호스트 {hosts}개', False))
    return frames


## @brief 같은 서브넷 판단. 두 주소에 같은 마스크를 AND해 네트워크 주소를 비교
def simulate_same(req: SubnetRequest) -> list[Frame]:
    index, _ = boundary(req.prefix)
    mask = to_octets(mask_of(req.prefix))
    nets = [to_octets(to_int(a) & mask_of(req.prefix)) for a in (req.address, req.other)]
    rows = octet_row('a', 'A', req.address, 0) + octet_row('b', 'B', req.other, 1)
    frames = [Frame(boxes=rows, arrows=[], caption=f'A {ip(req.address)}, B {ip(req.other)}: /{req.prefix}에서 같은 서브넷인가?')]
    rows += octet_row('m', '마스크', mask, 3, active=index)
    frames.append(Frame(boxes=rows, arrows=[], caption=f'/{req.prefix} 마스크 = {ip(mask)}'))
    for key, name, address, net, y in (('na', 'A', req.address, nets[0], 5), ('nb', 'B', req.other, nets[1], 6)):
        rows = [Box(**{**b.model_dump(), 'tone': 'normal'}) for b in rows]
        rows += octet_row(key, f'{name} 네트', net, y, tone='new', active=index)
        frames.append(Frame(boxes=rows, arrows=[], caption=(
            f'{name}: {address[index]} AND {mask[index]} = {net[index]} → 네트워크 {ip(net)}')))
    same = nets[0] == nets[1]
    rows = [Box(**{**b.model_dump(), 'tone': 'normal'}) for b in rows]
    rows.append(Box(id='verdict', label='같은 서브넷' if same else '다른 서브넷', x=7, y=7, shape='label'))
    frames.append(Frame(boxes=rows, arrows=[], caption=(
        '네트워크 주소가 같음 → 같은 서브넷, 라우터 없이 직접 전달' if same
        else '네트워크 주소가 다름 → 다른 서브넷, 기본 게이트웨이(라우터)로 보냄')))
    return frames


## @brief 서브넷 장면 실행. mode별(mask·split·same) 계산 프레임을 Animation으로 묶음
def simulate_subnet(req: SubnetRequest) -> Animation:
    frames = {'mask': simulate_mask, 'split': simulate_split, 'same': simulate_same}[req.mode](req)
    title = {'mask': '네트워크·브로드캐스트 주소 계산', 'split': '서브넷 나누기', 'same': '같은 서브넷 판단'}[req.mode]
    return build_animation('subnet', title, frames)


# --- 라우팅 ---

RouterName = Field(min_length=1, max_length=3)


## @brief 두 라우터를 잇는 양방향 링크와 비용(1~9, 표 칸에 한 자리로 들어가는 값)
class Link(Strict):
    a: str = RouterName
    b: str = RouterName
    cost: int = Field(ge=1, le=9)


## @brief 링크 상태 라우팅 장면. 라우터 3~6개·링크 9개는 그래프와 오른쪽 표가 한 화면에 들어가는 분량
# - dijkstra: source에서 다익스트라로 최소 비용과 다음 홉 표를 만드는 과정(경로 계산 = 라우팅)
# - forward: 각 라우터가 자기 표만 보고 패킷을 다음 홉으로 넘기는 과정(전달). destination 필요
# - 동점 규칙: 비용이 같으면 이름이 앞선 라우터를 먼저 확정하고, 같은 비용의 다른 경로는 먼저 찾은 것을 유지
class LinkStateRequest(Strict):
    kind: Literal['link_state']
    mode: Literal['dijkstra', 'forward']
    nodes: list[str] = Field(min_length=3, max_length=6)
    links: list[Link] = Field(min_length=2, max_length=9)
    source: str
    destination: str | None = None

    ## @brief 이름 중복·형식, 없는 라우터, 자기·중복 링크, 출발점에서 닿지 않는 라우터를 거부
    @model_validator(mode='after')
    def validate_graph(self):
        if len(set(self.nodes)) != len(self.nodes) or any(not 1 <= len(n) <= 3 for n in self.nodes):
            raise ValueError('라우터 이름은 1~3글자이고 서로 달라야 합니다')
        if self.source not in self.nodes:
            raise ValueError(f'출발 라우터 {self.source}: nodes에 없습니다')
        seen = set()
        for link in self.links:
            if link.a not in self.nodes or link.b not in self.nodes:
                raise ValueError(f'링크 {link.a}-{link.b}: 끝점이 nodes에 없습니다')
            key = tuple(sorted((link.a, link.b)))
            if link.a == link.b or key in seen:
                raise ValueError(f'링크 {link.a}-{link.b}: 자기 링크이거나 이미 있는 링크입니다')
            seen.add(key)
        if len(dijkstra(self, self.source)[0]) != len(self.nodes):
            raise ValueError('출발 라우터에서 닿지 않는 라우터가 있습니다')
        if self.mode == 'forward' and (self.destination not in self.nodes or self.destination == self.source):
            raise ValueError('forward에는 출발점과 다른 destination(nodes 안의 이름)이 필요합니다')
        return self


## @brief 다익스트라 실행. 반환: (확정 순서, 최소 비용, 다음 홉, 단계별 기록)
# - 단계 기록 = (확정한 라우터, 그때 비용, [(갱신된 라우터, 이전 비용, 새 비용)])
# - 다음 홉: 출발점의 이웃이면 그 이웃, 아니면 직전 라우터의 다음 홉을 물려받음
def dijkstra(req: LinkStateRequest, source: str):
    adj = {n: [] for n in req.nodes}
    for link in req.links:
        if link.a in adj and link.b in adj:
            adj[link.a].append((link.b, link.cost))
            adj[link.b].append((link.a, link.cost))
    dist, hop, done, steps = {source: 0}, {source: '-'}, [], []
    while True:
        frontier = [n for n in dist if n not in done]
        if not frontier:
            return done, dist, hop, steps
        u = min(frontier, key=lambda n: (dist[n], n))
        done.append(u)
        changes = []
        for v, cost in sorted(adj[u]):
            if v not in done and dist[u] + cost < dist.get(v, float('inf')):
                changes.append((v, dist.get(v), dist[u] + cost))
                dist[v] = dist[u] + cost
                hop[v] = v if u == source else hop[u]
        steps.append((u, dist[u], changes))


# 라우터 수별 자리(x 0..7, y 0..6): 볼록 다각형 꼭짓점이라 링크 선이 다른 라우터를 지나지 않음. 오른쪽 x 9..11은 표 자리
ROUTER_SLOTS = {
    3: [(3, 0), (6, 5), (0, 5)],
    4: [(0, 0), (6, 0), (6, 5), (0, 5)],
    5: [(3, 0), (7, 2), (5, 6), (1, 6), (0, 2)],
    6: [(1, 0), (5, 0), (7, 3), (5, 6), (1, 6), (0, 3)],
}


## @brief 라우터 그래프와 오른쪽 표(목적지·비용·다음 홉)를 그림
# - tones: 라우터별 강조, fresh: 이번 단계에 바뀐 표 칸, directed: 화살촉을 달 링크(패킷이 지나간 방향)
def route_frame(req, owner, dist, hop, caption, tones=None, fresh=(), directed=()) -> Frame:
    slots = dict(zip(req.nodes, ROUTER_SLOTS[len(req.nodes)]))
    boxes = [Box(id=f'r{n}', label=n, x=x, y=y, shape='node', tone=(tones or {}).get(n, 'normal'))
             for n, (x, y) in slots.items()]
    boxes += [Box(id='owner', label=f'{owner}의 표', x=9, y=0, shape='label'),
              Box(id='costHead', label='비용', x=10, y=0, shape='label'),
              Box(id='hopHead', label='다음홉', x=11, y=0, shape='label')]
    for i, n in enumerate(req.nodes):
        tone = 'new' if n in fresh else 'normal'
        boxes += [Box(id=f'dest{n}', label=n, x=9, y=i + 1, shape='label'),
                  Box(id=f'cost{n}', label=str(dist[n]) if n in dist else '∞', x=10, y=i + 1, shape='cell', tone=tone),
                  Box(id=f'hop{n}', label=hop.get(n, '-'), x=11, y=i + 1, shape='cell', tone=tone)]
    arrows = []
    for link in req.links:
        if (link.b, link.a) in directed:
            arrows.append(Arrow(source=f'r{link.b}', target=f'r{link.a}', label=str(link.cost)))
        else:
            arrows.append(Arrow(source=f'r{link.a}', target=f'r{link.b}', label=str(link.cost),
                                head=(link.a, link.b) in directed))
    return Frame(boxes=boxes, arrows=arrows, caption=caption)


## @brief 다익스트라 과정. 라우터를 하나 확정할 때마다 프레임 하나, 갱신된 표 칸을 강조
def simulate_dijkstra(req: LinkStateRequest) -> list[Frame]:
    s = req.source
    _, _, _, steps = dijkstra(req, s)
    dist, hop, done = {s: 0}, {s: '-'}, []
    frames = [route_frame(req, s, dist, hop, f'{s}에서 출발: 자기 비용 0, 나머지는 아직 모름(∞)')]
    for u, cost, changes in steps:
        done.append(u)
        for v, _, new in changes:
            dist[v] = new
            hop[v] = v if u == s else hop[u]
        shown = ', '.join(f'{v} {"∞" if old is None else old}→{new}' for v, old, new in changes) or '없음'
        tones = {n: 'active' for n in done} | {u: 'new'}
        frames.append(route_frame(req, s, dist, hop, f'{u} 확정(비용 {cost}). 이웃 비용 갱신: {shown}',
                                  tones, fresh=[v for v, _, _ in changes]))
    frames.append(route_frame(req, s, dist, hop, f'완료: {s}의 표 = 목적지별 최소 비용과 첫 번째로 보낼 이웃(다음 홉)',
                              {n: 'active' for n in done}))
    return frames


## @brief 홉 단위 전달. 패킷을 가진 라우터의 표(그 라우터에서 계산한 다익스트라 결과)로 다음 홉을 고름
def simulate_forward(req: LinkStateRequest) -> list[Frame]:
    d, current, path, frames = req.destination, req.source, [req.source], []
    total = dijkstra(req, req.source)[1][d]
    while current != d:
        _, dist, hop, _ = dijkstra(req, current)
        nxt = hop[d]
        tones = {n: 'active' for n in path} | {current: 'new'}
        caption = f'패킷이 {current}에: {current}의 표에서 목적지 {d} → 다음 홉 {nxt}(남은 비용 {dist[d]})'
        frames.append(route_frame(req, current, dist, hop, caption, tones, fresh=[d],
                                  directed=list(zip(path, path[1:])) + [(current, nxt)]))
        path.append(nxt)
        current = nxt
    _, dist, hop, _ = dijkstra(req, d)
    frames.append(route_frame(req, d, dist, hop, f'{d} 도착: 경로 {"→".join(path)}, 총 비용 {total}',
                              {n: 'active' for n in path} | {d: 'new'}, directed=list(zip(path, path[1:]))))
    return frames


## @brief 링크 상태 장면 실행. dijkstra는 경로 계산, forward는 홉 단위 전달 프레임을 만듦
def simulate_link_state(req: LinkStateRequest) -> Animation:
    if req.mode == 'dijkstra':
        return build_animation('link_state', f'링크 상태 라우팅: {req.source}의 최소 비용 경로', simulate_dijkstra(req))
    return build_animation('link_state', f'홉 단위 전달: {req.source} → {req.destination}', simulate_forward(req))


## @brief 전달 표 항목 하나: 목적지 접두사(주소·길이)와 다음 홉 이름
class Route(Strict):
    prefix: list[int] = Field(min_length=4, max_length=4)
    length: int = Field(ge=0, le=32)
    hop: str = RouterName


## @brief 최장 접두사 일치 장면. 항목 2~6개는 표 한 화면 분량. 0.0.0.0/0(기본 경로) 항목도 쓸 수 있음
class PrefixMatchRequest(Strict):
    kind: Literal['prefix_match']
    destination: list[int] = Field(min_length=4, max_length=4)
    entries: list[Route] = Field(min_length=2, max_length=6)

    ## @brief 옥텟 범위, 호스트 비트가 남은 접두사, 같은 접두사 중복을 거부
    @model_validator(mode='after')
    def validate_table(self):
        seen = set()
        for octets in [self.destination] + [e.prefix for e in self.entries]:
            if any(not 0 <= o <= 255 for o in octets):
                raise ValueError('옥텟은 0~255여야 합니다')
        for e in self.entries:
            if to_int(e.prefix) & ~mask_of(e.length) & 0xFFFFFFFF:
                raise ValueError(f'{ip(e.prefix)}/{e.length}: 접두사 뒤 호스트 비트는 0이어야 합니다')
            if (to_int(e.prefix), e.length) in seen:
                raise ValueError(f'{ip(e.prefix)}/{e.length}: 같은 항목이 두 번 있습니다')
            seen.add((to_int(e.prefix), e.length))
        return self


## @brief 표 항목을 위에서부터 하나씩 비교(목적지 AND 마스크 = 접두사?)하고, 일치한 것 중 가장 긴 접두사를 고름
def simulate_prefix_match(req: PrefixMatchRequest) -> Animation:
    target = to_int(req.destination)
    head = [Box(id='destName', label='목적지', x=0, y=0, shape='label')] + [
        Box(id=f'dest{i}', label=str(o), x=2 + i, y=0, shape='cell') for i, o in enumerate(req.destination)]
    head += [Box(id='prefixHead', label='접두사', x=1, y=1, shape='label'),
             Box(id='matchHead', label='일치', x=5, y=1, shape='label'),
             Box(id='hopHead', label='다음홉', x=7, y=1, shape='label')]
    rows = []
    for i, e in enumerate(req.entries):
        rows += [Box(id=f'prefix{i}', label=f'{ip(e.prefix)}/{e.length}', x=1, y=2 + i, shape='label'),
                 Box(id=f'hop{i}', label=e.hop, x=7, y=2 + i, shape='cell')]
    frames = [Frame(boxes=head + rows, arrows=[], caption=f'목적지 {ip(req.destination)}: 표의 모든 항목과 접두사를 비교')]
    best = None
    for i, e in enumerate(req.entries):
        masked = ip(to_octets(target & mask_of(e.length)))
        hit = masked == ip(e.prefix)
        if hit and (best is None or e.length > req.entries[best].length):
            best = i
        rows = [Box(**{**b.model_dump(), 'tone': 'normal'}) for b in rows]
        rows.append(Box(id=f'match{i}', label='O' if hit else 'X', x=5, y=2 + i, shape='cell', tone='new' if hit else 'removed'))
        default = '(기본 경로) ' if e.length == 0 else ''
        caption = (f'/{e.length}: {default}목적지 앞 {e.length}비트 = {masked} → 일치' if hit
                   else f'/{e.length}: 목적지 앞 {e.length}비트 = {masked} ≠ {ip(e.prefix)} → 불일치')
        frames.append(Frame(boxes=head + rows, arrows=[], caption=caption))
    rows = [Box(**{**b.model_dump(), 'tone': 'normal'}) for b in rows]
    if best is None:
        caption = '일치하는 항목이 없음 → 패킷 폐기'
    else:
        chosen = req.entries[best]
        rows = [Box(**{**b.model_dump(), 'tone': 'active'}) if b.id in (f'prefix{best}', f'hop{best}') else b for b in rows]
        rows.append(Box(id='chosen', label=chosen.hop, x=10, y=2 + best, shape='cell', tone='new'))
        caption = f'일치한 것 중 가장 긴 /{chosen.length} 선택 → 다음 홉 {chosen.hop}'
    arrows = [Arrow(source=f'hop{best}', target='chosen')] if best is not None else []
    frames.append(Frame(boxes=head + rows, arrows=arrows, caption=caption))
    return build_animation('prefix_match', f'최장 접두사 일치: {ip(req.destination)}', frames)


# --- TCP ---

# 순서 번호 상한: 칸 안에 세 자리로 들어가는 값. 실제 TCP는 32비트지만 장면에서는 작은 수로 원리만 보임
SEQ_MAX = 999


## @brief TCP 장면
# - handshake: 데이터 없는 3-way handshake의 SYN·SYN+ACK·ACK 순서 번호와 확인 번호(client_isn·server_isn)
# - transfer: seq번 바이트부터 mss바이트 세그먼트 segments개를 보내며 누적 ACK·재전송을 추적
#   lost: 처음 보낼 때 사라지는 세그먼트 번호(1부터), lost_acks: 그 세그먼트가 처음 도착해 만든 ACK가 사라지는 번호
#   재전송한 세그먼트와 그 ACK는 사라지지 않음(장면이 끝나도록 하기 위함)
class TcpRequest(Strict):
    kind: Literal['tcp']
    mode: Literal['handshake', 'transfer']
    client_isn: int = Field(default=100, ge=0, le=SEQ_MAX - 1)
    server_isn: int = Field(default=300, ge=0, le=SEQ_MAX - 1)
    seq: int = Field(default=1, ge=0, le=SEQ_MAX - 1)
    mss: int = Field(default=100, ge=10, le=200)
    segments: int = Field(default=3, ge=1, le=5)
    lost: list[int] = Field(default=[], max_length=3)
    lost_acks: list[int] = Field(default=[], max_length=3)

    ## @brief 마지막 확인 번호의 자릿수, 범위 밖·중복 번호, 데이터와 ACK 유실의 겹침을 거부
    # - 데이터가 사라진 세그먼트는 처음 도착할 때 만든 ACK가 없으므로 lost_acks와 함께 쓸 수 없음
    @model_validator(mode='after')
    def validate_transfer(self):
        if self.seq + self.segments * self.mss > SEQ_MAX:
            raise ValueError(f'마지막 확인 번호 {self.seq + self.segments * self.mss}: {SEQ_MAX} 이하가 되게 seq·mss·segments를 줄이세요')
        for name, numbers in (('lost', self.lost), ('lost_acks', self.lost_acks)):
            if len(set(numbers)) != len(numbers) or any(not 1 <= n <= self.segments for n in numbers):
                raise ValueError(f'{name}: 1~{self.segments} 사이의 서로 다른 세그먼트 번호여야 합니다')
        if set(self.lost) & set(self.lost_acks):
            raise ValueError('같은 세그먼트를 lost와 lost_acks에 함께 넣을 수 없습니다')
        return self


## @brief 3-way handshake. 메시지 하나에 프레임 하나, 메시지 화살표 아래 줄에 seq·ack 값, 맨 아래 줄에 양쪽 상태
def simulate_handshake(req: TcpRequest) -> Animation:
    x, y = req.client_isn, req.server_isn
    messages = [('c', 'SYN', f'seq {x}', 'SYN_SENT', 'SYN_RCVD', f'SYN: 클라이언트가 초기 순서 번호 {x}{josa(x, "을", "를")} 알림'),
                ('s', 'SYN+ACK', f'seq {y} ack {x + 1}', 'ESTABLISHED', 'SYN_RCVD',
                 f'SYN+ACK: 서버 초기 순서 번호 {y}, SYN이 번호 1개를 써서 ack {x + 1}'),
                ('c', 'ACK', f'seq {x + 1} ack {y + 1}', 'ESTABLISHED', 'ESTABLISHED',
                 f'ACK: ack {y + 1}로 서버 SYN 확인 → 연결 수립, 첫 데이터 바이트는 {x + 1}번')]
    head = [Box(id='client', label='클라이언트', x=1, y=0, shape='label'), Box(id='server', label='서버', x=10, y=0, shape='label')]

    def states(client, server):
        return [Box(id='cstate', label=client, x=1, y=7, shape='label'), Box(id='sstate', label=server, x=10, y=7, shape='label')]

    frames = [Frame(boxes=head + states('CLOSED', 'LISTEN'), arrows=[], caption='클라이언트 CLOSED, 서버 LISTEN(연결 요청 대기)')]
    boxes, arrows = list(head), []
    for k, (sender, flags, info, client, server, caption) in enumerate(messages):
        r = 1 + 2 * k
        boxes += [Box(id=f'c{k}', label='', x=2, y=r, shape='label'), Box(id=f's{k}', label='', x=9, y=r, shape='label'),
                  Box(id=f'info{k}', label=info, x=5, y=r + 1, shape='label')]
        arrows.append(Arrow(source=f'{sender}{k}', target=f'{"s" if sender == "c" else "c"}{k}', label=flags))
        frames.append(Frame(boxes=boxes + states(client, server), arrows=list(arrows), caption=caption))
    return build_animation('tcp', f'3-way handshake(ISN {x}·{y})', frames)


## @brief 세그먼트 전송·유실·누적 ACK·재전송. 전송 한 번에 프레임 하나
# - 화면: y=1 송신 쪽 확인 안 된 세그먼트(시작 번호), y=3 유실 표시, y=4 수신 쪽(빈칸 = 아직 못 받음), y=6 이번 ACK
# - 송신 순서: 빠른 재전송 대기 → 아직 안 보낸 새 세그먼트 → (모두 보냈는데 확인 안 된 것이 있으면) 타임아웃 재전송
#   새 세그먼트를 연달아 보내고 ACK가 그 순서대로 돌아온다고 단순화함
# - 중복 ACK: 송신자가 받은 마지막 ACK와 같은 값. handshake에서 받은 ack(=seq)가 첫 기준이고, 3개 쌓이면 빠른 재전송
def simulate_transfer(req: TcpRequest) -> Animation:
    n, mss = req.segments, req.mss
    start = [req.seq + i * mss for i in range(n + 1)]
    received, sent, acked = set(), set(), 0
    last_ack, dup, fast = req.seq, 0, False
    xs = [1 + 2 * i for i in range(n + 1)]

    def frame(caption, active=None, lost=False, ack=None, ack_lost=False):
        boxes = [Box(id='sendName', label='송신', x=0, y=1, shape='label'), Box(id='recvName', label='수신', x=0, y=4, shape='label')]
        for i in range(n):
            if i >= acked:
                boxes.append(Box(id=f'snd{i}', label=str(start[i]), x=xs[i], y=1, shape='cell' if i in sent else 'slot',
                                 tone='active' if i == active else 'normal'))
            boxes.append(Box(id=f'rcv{i}', label=str(start[i]) if i in received else '', x=xs[i], y=4,
                             shape='cell' if i in received else 'slot', tone='new' if i == active and not lost else 'normal'))
        if lost:
            boxes.append(Box(id='lost', label='유실', x=xs[active], y=3, shape='cell', tone='removed'))
        if ack is not None:
            boxes += [Box(id='ackName', label='ACK', x=0, y=6, shape='label'),
                      Box(id='ack', label=str(ack), x=xs[(ack - req.seq) // mss], y=6, shape='cell',
                          tone='removed' if ack_lost else 'new')]
        return Frame(boxes=boxes, arrows=[], caption=caption)

    frames = [frame(f'{req.seq}번 바이트부터 {mss}바이트씩 세그먼트 {n}개를 보냄')]
    while acked < n:
        if fast:
            i, how = acked, '중복 ACK 3개 → 빠른 재전송'
        elif len(sent) < n:
            i, how = len(sent), '전송'
        else:
            i, how = acked, '타임아웃 → 재전송'
        first = i not in sent
        sent.add(i)
        fast = False
        span = f'{how} {start[i]}~{start[i + 1] - 1}'
        if first and i + 1 in req.lost:
            frames.append(frame(f'{span}: 가는 길에 유실', active=i, lost=True))
            continue
        received.add(i)
        ack = start[next(k for k in range(n + 1) if k not in received)]
        if first and i + 1 in req.lost_acks:
            frames.append(frame(f'{span} 도착, ACK {ack}{josa(ack, "은", "는")} 돌아오다 유실', active=i, ack=ack, ack_lost=True))
            continue
        if ack > last_ack:
            # 한 세그먼트보다 많이 늘면 앞서 받아 둔 바이트나 유실된 ACK의 몫까지 한 번에 확인한 것
            note = f'ACK {ack}: {last_ack}~{ack - 1} 한꺼번에 확인(누적)' if ack - last_ack > mss else f'ACK {ack}'
            last_ack, dup = ack, 0
            acked = (ack - req.seq) // mss
        else:
            dup += 1
            fast = dup == 3
            note = f'빈칸이 있어 ACK {ack} 반복(중복 {dup}번째)'
        frames.append(frame(f'{span} 도착, {note}', active=i, ack=ack))
    frames.append(frame(f'모든 바이트 확인 완료: 마지막 ACK {start[n]}'))
    return build_animation('tcp', f'TCP 누적 ACK와 재전송({n}개)', frames)


## @brief TCP 장면 실행. mode에 따라 handshake 또는 전송·재전송 장면으로 보냄
def simulate_tcp(req: TcpRequest) -> Animation:
    return simulate_handshake(req) if req.mode == 'handshake' else simulate_transfer(req)


# --- 흐름 제어·혼잡 제어 ---

## @brief 송신자가 지금 보낼 수 있는 만큼(min(rwnd, cwnd) − 미확인) 보내는 연산
class FlowSendOp(Strict):
    op: Literal['send']


## @brief 수신자가 지금까지 받은 세그먼트를 모두 확인하고 버퍼 빈칸을 rwnd로 알리는 연산
class FlowAckOp(Strict):
    op: Literal['ack']


## @brief 수신 쪽 응용이 버퍼에서 count개를 읽어 빈칸을 만드는 연산
class FlowReadOp(Strict):
    op: Literal['read']
    count: int = Field(ge=1, le=6)


## @brief 흐름 제어 장면. 세그먼트 단위(1칸 = mss바이트)로 수신 버퍼(buffer칸)와 고정 cwnd를 둠
# - 송신자는 마지막으로 알림 받은 rwnd만 앎(처음에는 버퍼 전체). 보낸 세그먼트는 유실 없이 바로 버퍼에 들어감
# - 연산 10개(프레임 11개), 보낸 세그먼트 12개(가로 12칸)까지
class FlowWindowRequest(Strict):
    kind: Literal['flow_window']
    buffer: int = Field(ge=2, le=6)
    cwnd: int = Field(default=8, ge=1, le=8)
    mss: int = Field(default=100, ge=10, le=1000)
    ops: list[Annotated[Union[FlowSendOp, FlowAckOp, FlowReadOp], Field(discriminator='op')]] = Field(min_length=1, max_length=10)


## @brief 흐름 제어 실행. 보낼 수 있는 양 = min(rwnd, cwnd) − 미확인
# - 화면: y=1 보낸 세그먼트(확인됨 보통, 미확인 강조, 이번에 보냄 새로) + 지금 보낼 수 있는 빈 자리, y=4 수신 버퍼, y=6 윈도우 값
def simulate_flow_window(req: FlowWindowRequest) -> Animation:
    mss = req.mss
    sent, acked, rwnd = 0, 0, req.buffer
    buffer: list[int] = []

    def sendable():
        return max(min(rwnd, req.cwnd) - (sent - acked), 0)

    def frame(caption, fresh=()):
        boxes = [Box(id='sendName', label='송신 쪽 세그먼트', x=1, y=0, shape='label'),
                 Box(id='bufName', label='수신 버퍼', x=1, y=3, shape='label')]
        boxes += [Box(id=f'seg{k}', label=str(k + 1), x=k, y=1, shape='cell',
                      tone='new' if k in fresh else 'active' if k >= acked else 'normal') for k in range(sent)]
        boxes += [Box(id=f'win{k}', label='', x=k, y=1, shape='slot') for k in range(sent, min(sent + sendable(), 12))]
        boxes += [Box(id=f'buf{k}', label=str(buffer[k] + 1) if k < len(buffer) else '', x=k, y=4,
                      shape='cell' if k < len(buffer) else 'slot') for k in range(req.buffer)]
        boxes += [Box(id='rwnd', label=f'rwnd {rwnd * mss}B', x=0, y=6, shape='label'),
                  Box(id='cwnd', label=f'cwnd {req.cwnd * mss}B', x=3, y=6, shape='label'),
                  Box(id='unacked', label=f'미확인 {(sent - acked) * mss}B', x=6, y=6, shape='label'),
                  Box(id='sendable', label=f'가능 {sendable() * mss}B', x=9, y=6, shape='label')]
        return Frame(boxes=boxes, arrows=[], caption=caption)

    frames = [frame(f'수신 버퍼 {req.buffer}칸({req.buffer * mss}B), cwnd {req.cwnd * mss}B: 처음 rwnd는 버퍼 전체')]
    for op in req.ops:
        if op.op == 'send':
            k = sendable()
            formula = f'min(rwnd {rwnd * mss}, cwnd {req.cwnd * mss}) − 미확인 {(sent - acked) * mss}'
            if sent + k > 12:
                raise ValueError('send: 세그먼트는 12개까지만 그립니다')
            fresh = range(sent, sent + k)
            buffer += list(fresh)
            sent += k
            caption = f'보냄 {k * mss}B = {formula}' if k else f'보낼 수 없음: {formula} = 0'
            frames.append(frame(caption, fresh))
        elif op.op == 'ack':
            acked, rwnd = sent, req.buffer - len(buffer)
            frames.append(frame(f'ACK: 받은 것 모두 확인, 버퍼 빈칸 {rwnd}칸 → rwnd {rwnd * mss}B 알림'))
        else:
            if op.count > len(buffer):
                raise ValueError(f'read {op.count}: 버퍼에 {len(buffer)}개뿐입니다')
            buffer = buffer[op.count:]
            frames.append(frame(f'응용이 {op.count}개 읽음 → 버퍼 빈칸 {req.buffer - len(buffer)}칸(다음 ACK에서 알림)'))
    return build_animation('flow_window', f'흐름 제어: 버퍼 {req.buffer}칸, cwnd {req.cwnd}칸', frames)


## @brief 혼잡 윈도우 장면의 손실 사건. round번째 RTT에 손실을 알아챔
# - timeout: ssthresh = cwnd/2, 다음 RTT cwnd = 1(느린 시작부터)
# - dup_ack: 3중복 ACK, ssthresh = cwnd/2, 다음 RTT cwnd = ssthresh(빠른 회복 단순화)
class Loss(Strict):
    round: int = Field(ge=1, le=12)
    type: Literal['timeout', 'dup_ack']


## @brief 혼잡 윈도우 장면. cwnd 1에서 시작해 RTT마다 한 점씩 그리는 꺾은선(RTT 12개 = 가로 12칸)
# - 느린 시작은 RTT마다 2배지만 ssthresh를 넘지 않게 멈추고, 그 뒤 혼잡 회피는 RTT마다 +1(단위 MSS)
class CongestionRequest(Strict):
    kind: Literal['congestion']
    ssthresh: int = Field(ge=2, le=16)
    rounds: int = Field(ge=3, le=12)
    losses: list[Loss] = Field(default=[], max_length=3)

    ## @brief 손실 라운드가 마지막 RTT 전이고 서로 다른지 확인(마지막 RTT 손실은 효과를 그릴 칸이 없음)
    @model_validator(mode='after')
    def validate_losses(self):
        rounds = [loss.round for loss in self.losses]
        if len(set(rounds)) != len(rounds) or any(r >= self.rounds for r in rounds):
            raise ValueError(f'손실 round는 1~{self.rounds - 1} 사이의 서로 다른 값이어야 합니다')
        return self


## @brief cwnd 변화 계산. 반환: RTT별 (cwnd, ssthresh, 손실 종류 또는 None)
def congestion_rounds(req: CongestionRequest) -> list[tuple]:
    losses = {loss.round: loss.type for loss in req.losses}
    cwnd, threshold, rows = 1, req.ssthresh, []
    for r in range(1, req.rounds + 1):
        rows.append((cwnd, threshold, losses.get(r)))
        if r in losses:
            threshold = max(cwnd // 2, 2)
            cwnd = 1 if losses[r] == 'timeout' else threshold
        elif cwnd < threshold:
            cwnd = min(2 * cwnd, threshold)
        else:
            cwnd += 1
    return rows


## @brief 혼잡 윈도우 꺾은선. RTT 하나에 프레임 하나, y 0..5는 cwnd 높이, y=6 RTT 번호, y=7 그때의 ssthresh
def simulate_congestion(req: CongestionRequest) -> Animation:
    rows = congestion_rounds(req)
    top = max(c for c, _, _ in rows)
    frames = []
    for r, (cwnd, threshold, loss) in enumerate(rows):
        boxes, arrows = [], []
        for k in range(r + 1):
            c, t, lost = rows[k]
            level = round((c - 1) * 5 / (top - 1)) if top > 1 else 0
            tone = 'removed' if lost else 'new' if k == r else 'normal'
            boxes += [Box(id=f'c{k}', label=str(c), x=k, y=5 - level, shape='cell', tone=tone),
                      Box(id=f'rtt{k}', label=str(k + 1), x=k, y=6, shape='label'),
                      Box(id=f'th{k}', label=f'th{t}', x=k, y=7, shape='label')]
            if k:
                arrows.append(Arrow(source=f'c{k - 1}', target=f'c{k}', head=False))
        previous = rows[r - 1] if r else None
        if loss:
            new = max(cwnd // 2, 2)
            caption = (f'RTT {r + 1}: cwnd {cwnd}에서 타임아웃 → ssthresh {new}, 다음 cwnd 1' if loss == 'timeout'
                       else f'RTT {r + 1}: cwnd {cwnd}에서 3중복 ACK → ssthresh {new}, 다음 cwnd {new}(절반)')
        elif previous is None:
            caption = f'RTT 1: cwnd 1로 느린 시작(ssthresh {threshold}). 아래 줄 th = ssthresh'
        elif previous[2]:
            caption = f'RTT {r + 1}: 손실 뒤 cwnd {cwnd}로 다시 시작(ssthresh {threshold})'
        elif previous[0] < previous[1]:
            grown = '2배' if cwnd == 2 * previous[0] else 'ssthresh에서 멈춤'
            reached = ' → 이제 혼잡 회피' if cwnd == threshold else ''
            caption = f'RTT {r + 1}: 느린 시작으로 cwnd {previous[0]} → {cwnd}({grown}){reached}'
        else:
            caption = f'RTT {r + 1}: 혼잡 회피로 cwnd {previous[0]} → {cwnd}(+1)'
        frames.append(Frame(boxes=boxes, arrows=arrows, caption=caption))
    return build_animation('congestion', f'혼잡 윈도우(ssthresh {req.ssthresh})', frames)


# --- DNS ---

DNS_LABEL = re.compile(r'[a-z0-9-]{1,10}')
# 이름 전체 상한: 상자 옆 이름표(16자)에 들어가는 길이
DNS_NAME_MAX = 16


## @brief DNS 장면. name = 레이블 2~3개(예: ['www', 'example', 'com'])
# - 서버는 루트(.) → TLD(마지막 레이블) → 권한 서버(마지막 두 레이블 영역) 세 단계로 둠. 더 깊은 위임은 다루지 않음
# - iterative: 로컬 DNS가 루트·TLD·권한 서버에 차례로 묻고 참조를 받음(반복 질의)
# - recursive: 각 서버가 다음 서버에 대신 묻고 답이 거꾸로 돌아옴(재귀 질의, 개념 비교용)
# - cache: 로컬 DNS가 TTL(초) 동안 답을 캐시. queries = 질의 시각(초, 오름차순 1~5개)
class DnsRequest(Strict):
    kind: Literal['dns']
    mode: Literal['iterative', 'recursive', 'cache']
    name: list[str] = Field(min_length=2, max_length=3)
    ttl: int = Field(default=300, ge=1, le=999)
    queries: list[int] = Field(default=[], max_length=5)

    ## @brief 레이블 형식(소문자·숫자·하이픈 1~10자), TLD 3자 이하, 이름 길이, 캐시 질의 시각을 확인
    @model_validator(mode='after')
    def validate_name(self):
        if any(not DNS_LABEL.fullmatch(label) for label in self.name):
            raise ValueError('레이블은 소문자·숫자·하이픈 1~10자여야 합니다')
        if len(self.name[-1]) > 3 or len('.'.join(self.name)) > DNS_NAME_MAX:
            raise ValueError(f'TLD는 3자 이하, 이름 전체는 {DNS_NAME_MAX}자 이하여야 합니다')
        if self.mode == 'cache':
            if not self.queries or any(not 0 <= t <= 999 for t in self.queries) or self.queries != sorted(set(self.queries)):
                raise ValueError('cache에는 0~999초 사이의 질의 시각 1~5개를 오름차순으로 넣으세요')
        return self


## @brief DNS 조회 화면: PC·로컬 DNS(왼쪽), 루트·TLD·권한 서버(오른쪽 세로 계층)
# - 긴 영역 이름은 맨 오른쪽 열이 아닌 권한 서버 칸 아래에 둠: 맨 오른쪽 이름표는 그림 폭을 넘어 잘리기 때문
# - step: 이번에 오가는 메시지 (보내는 쪽, 받는 쪽, 화살표 이름표). 위임 관계는 머리 없는 선으로 늘 그림
def dns_frame(req, caption, step=None) -> Frame:
    zone = '.'.join(req.name[-2:])
    boxes = [Box(id='pc', label='PC', x=1, y=3, shape='cell'),
             Box(id='local', label='DNS', x=4, y=3, shape='cell'),
             Box(id='localName', label='로컬 DNS', x=4, y=4, shape='label'),
             Box(id='root', label='.', x=7, y=0, shape='cell'),
             Box(id='rootName', label='루트', x=9, y=0, shape='label'),
             Box(id='tld', label=req.name[-1], x=7, y=3, shape='cell'),
             Box(id='tldName', label=f'TLD {req.name[-1]}', x=9, y=3, shape='label'),
             Box(id='auth', label='NS', x=7, y=6, shape='cell'),
             Box(id='authName', label='권한 서버', x=9, y=6, shape='label'),
             Box(id='zoneName', label=zone, x=7, y=7, shape='label')]
    arrows = [Arrow(source='root', target='tld', head=False), Arrow(source='tld', target='auth', head=False)]
    if step:
        source, target, text = step
        boxes = [Box(**{**b.model_dump(), 'tone': 'active'}) if b.id in (source, target) else b for b in boxes]
        arrows = [a for a in arrows if {a.source, a.target} != {source, target}]
        arrows.append(Arrow(source=source, target=target, label=text))
    return Frame(boxes=boxes, arrows=arrows, caption=caption)


## @brief 반복·재귀 질의의 메시지 순서와 설명
def dns_steps(req) -> list[tuple]:
    name, tld, zone = '.'.join(req.name), req.name[-1], '.'.join(req.name[-2:])
    first = ('pc', 'local', '질의', f'PC가 로컬 DNS에 {name}의 IP를 물음')
    last = ('local', 'pc', '응답', '로컬 DNS가 IP를 PC에 전달하고 TTL 동안 캐시')
    if req.mode == 'iterative':
        return [first,
                ('local', 'root', '질의', '로컬 DNS가 루트 서버에 같은 이름을 물음'),
                ('root', 'local', '참조', f'루트: 답 대신 {tld} TLD 서버 주소를 알려 줌(참조)'),
                ('local', 'tld', '질의', f'로컬 DNS가 {tld} 서버에 다시 물음'),
                ('tld', 'local', '참조', f'{tld} 서버: {zone}의 권한 서버 주소를 알려 줌(위임)'),
                ('local', 'auth', '질의', f'로컬 DNS가 {zone} 권한 서버에 물음'),
                ('auth', 'local', 'A', f'권한 서버가 {name}의 A 레코드(IP 주소)로 답함'),
                last]
    return [first,
            ('local', 'root', '질의', '로컬 DNS가 루트에 묻고 답을 기다림'),
            ('root', 'tld', '질의', f'루트가 {tld} 서버에 대신 물음(재귀)'),
            ('tld', 'auth', '질의', f'{tld} 서버가 {zone} 권한 서버에 대신 물음'),
            ('auth', 'tld', 'A', '권한 서버가 A 레코드로 답함'),
            ('tld', 'root', 'A', f'{tld} 서버가 받은 답을 루트로 돌려줌'),
            ('root', 'local', 'A', '루트가 로컬 DNS에 답을 돌려줌(실제 루트는 부하 때문에 반복만 받음)'),
            last]


## @brief 캐시 장면. 질의 하나에 프레임 하나: 적중이면 로컬 DNS에서 바로 답하고, 없거나 만료면 서버에 다시 물음
# - 남은 TTL = TTL − (질의 시각 − 저장 시각). 0 이하(정확히 TTL만큼 지남 포함)면 만료
def simulate_dns_cache(req: DnsRequest) -> list[Frame]:
    name = '.'.join(req.name)
    stored = None

    def frame(caption, t=None, remain=None, hit=False):
        boxes = [Box(id='pc', label='PC', x=1, y=3, shape='cell'),
                 Box(id='local', label='DNS', x=4, y=3, shape='cell'),
                 Box(id='localName', label='로컬 DNS', x=4, y=4, shape='label'),
                 Box(id='servers', label='NS', x=8, y=3, shape='cell'),
                 Box(id='serversName', label='루트·TLD·권한', x=8, y=4, shape='label')]
        boxes += [Box(id=f'q{k}', label=str(q), x=2 + 2 * k, y=0, shape='cell',
                      tone='active' if q == t else 'normal') for k, q in enumerate(req.queries) if t is not None and q <= t]
        if t is not None:
            boxes += [Box(id='qName', label='t', x=0, y=0, shape='label'),
                      Box(id='cacheName', label='캐시', x=1, y=6, shape='label'),
                      Box(id='record', label='A', x=3, y=6, shape='cell', tone='normal' if hit else 'new'),
                      Box(id='recordName', label=name, x=5, y=6, shape='label'),
                      Box(id='remain', label=f'남은 TTL {remain}', x=8, y=6, shape='label')]
        arrows = [Arrow(source='pc', target='local', label='질의')]
        if t is not None and not hit:
            arrows.append(Arrow(source='local', target='servers', label='조회'))
        return Frame(boxes=boxes, arrows=arrows if t is not None else [], caption=caption)

    frames = [frame(f'로컬 DNS는 받은 답을 TTL {req.ttl}초 동안 캐시에 둠')]
    for t in req.queries:
        if stored is not None and t - stored < req.ttl:
            remain = req.ttl - (t - stored)
            frames.append(frame(f't={t}: 캐시 적중, 남은 TTL = {req.ttl} − ({t}−{stored}) = {remain}초', t, remain, True))
            continue
        reason = '캐시 없음' if stored is None else f'만료({t}−{stored} ≥ TTL {req.ttl})'
        again = '' if stored is None else '다시 '
        stored = t
        frames.append(frame(f't={t}: {reason} → 서버에 {again}조회, 만료 시각 t={t + req.ttl}', t, req.ttl))
    return frames


## @brief DNS 장면 실행. cache는 TTL 장면, 나머지는 시작 프레임 + 메시지마다 프레임 하나
def simulate_dns(req: DnsRequest) -> Animation:
    name = '.'.join(req.name)
    if req.mode == 'cache':
        return build_animation('dns', f'DNS 캐시와 TTL {req.ttl}초', simulate_dns_cache(req))
    label = '반복' if req.mode == 'iterative' else '재귀'
    frames = [dns_frame(req, f'{name}의 IP 찾기: {label} 질의')]
    frames += [dns_frame(req, caption, (source, target, text)) for source, target, text, caption in dns_steps(req)]
    return build_animation('dns', f'DNS {label} 질의: {name}', frames)


# --- 등록: model.py·__init__.py가 읽는 묶음 ---

# AnimationRequest 합집합에 넣는 요청 타입들(Union은 펼쳐져 kind 판별에 그대로 들어감)
NetworkRequest = Union[EncapsulationRequest, SubnetRequest, LinkStateRequest, PrefixMatchRequest, TcpRequest,
                       FlowWindowRequest, CongestionRequest, DnsRequest]

NETWORK_SIMULATORS = {
    'encapsulation': simulate_encapsulation,
    'subnet': simulate_subnet,
    'link_state': simulate_link_state,
    'prefix_match': simulate_prefix_match,
    'tcp': simulate_tcp,
    'flow_window': simulate_flow_window,
    'congestion': simulate_congestion,
    'dns': simulate_dns,
}

# 막힘 보충에서 개념별로 고를 수 있는 장면 종류
NETWORK_SCENES: dict[str, tuple[str, ...]] = {
    'network-layers': ('encapsulation',),
    'ip-subnet': ('subnet',),
    'routing': ('link_state', 'prefix_match'),
    'tcp-reliability': ('tcp',),
    'flow-congestion-control': ('flow_window', 'congestion'),
    'dns': ('dns',),
}

# 장면 선택 요청에 넣는 형식 예시(짧고 평범한 값)
NETWORK_FORMATS = {
    'encapsulation': {'kind': 'encapsulation', 'mode': 'send', 'data_bytes': 100},
    'subnet': {'kind': 'subnet', 'mode': 'mask', 'address': [192, 168, 1, 10], 'prefix': 24},
    'link_state': {'kind': 'link_state', 'mode': 'dijkstra', 'nodes': ['A', 'B', 'C'], 'source': 'A',
                   'links': [{'a': 'A', 'b': 'B', 'cost': 1}, {'a': 'B', 'b': 'C', 'cost': 2}]},
    'prefix_match': {'kind': 'prefix_match', 'destination': [10, 1, 2, 3],
                     'entries': [{'prefix': [10, 0, 0, 0], 'length': 8, 'hop': 'R1'},
                                 {'prefix': [0, 0, 0, 0], 'length': 0, 'hop': 'R2'}]},
    'tcp': {'kind': 'tcp', 'mode': 'handshake', 'client_isn': 100, 'server_isn': 300},
    'flow_window': {'kind': 'flow_window', 'buffer': 4, 'ops': [{'op': 'send'}, {'op': 'ack'}]},
    'congestion': {'kind': 'congestion', 'ssthresh': 8, 'rounds': 6},
    'dns': {'kind': 'dns', 'mode': 'iterative', 'name': ['example', 'com']},
}
