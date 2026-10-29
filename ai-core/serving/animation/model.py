## @file
# @brief 단계별 애니메이션의 프레임 스키마(Box·Arrow·Frame·Animation)와 모델이 고르는 장면 요청 스키마
# - 모델은 장면 종류와 연산 입력(AnimationRequest)만 고름
# - 프레임은 시뮬레이터(linear.py·hierarchical.py·graph.py·sorting.py·scheduling.py·call_stack.py)가 연산을 실제로 실행해 만듦. 모델이 상태를 지어내면 틀린 그림을 잡을 수 없기 때문
# - 프레임 JSON은 artifact 이벤트·막힘 view로 프런트엔드 재생기에 그대로 전달됨
"""Animation frame and scene request schemas."""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

# 교육적 분량 제한: 학생이 이전·다음 버튼으로 한 장면을 끝까지 따라갈 수 있는 단계 수
MAX_FRAMES = 16
# 그리기 격자 크기(x 0..11, y 0..7): 휴대폰 폭으로 축소해도 상자 라벨을 읽을 수 있는 칸 수
GRID_WIDTH = 12
GRID_HEIGHT = 8

# 모든 원소 값의 범위: 상자 안에 세 글자 이내로 들어가는 정수
Value = Annotated[int, Field(ge=-99, le=999)]


## @brief 모든 스키마의 공통 설정. 모르는 필드와 타입 자동 변환을 막아 모델 출력 오류를 그대로 드러냄
class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


## @brief 격자 한 칸에 그리는 상자. 같은 원소는 프레임이 바뀌어도 같은 id를 유지해 프런트엔드가 이동으로 그림
# - shape: cell(배열 칸), node(연결 노드), slot(인덱스가 있는 자리), label(head·top 같은 이름표)
# - tone: 이번 단계에서 읽은 것(active), 새로 생긴 것(new), 빠지는 것(removed)을 강조
class Box(Strict):
    id: str = Field(min_length=1, max_length=16)
    label: str = Field(max_length=16)
    x: int = Field(ge=0, le=GRID_WIDTH - 1)
    y: int = Field(ge=0, le=GRID_HEIGHT - 1)
    shape: Literal['cell', 'node', 'slot', 'label']
    tone: Literal['normal', 'active', 'new', 'removed'] = 'normal'


## @brief 같은 프레임의 두 상자를 잇는 화살표(포인터·참조)
# - head: False면 화살촉 없는 선. 무방향 그래프 간선처럼 방향이 없는 연결에 씀(기본은 화살촉 있음)
class Arrow(Strict):
    source: str
    target: str
    label: str | None = Field(default=None, max_length=8)
    head: bool = True


## @brief 한 단계의 화면. 상자·화살표 개수 상한은 한 화면에 그릴 수 있는 분량
class Frame(Strict):
    boxes: list[Box] = Field(max_length=40)
    arrows: list[Arrow] = Field(max_length=40)
    caption: str = Field(min_length=1, max_length=80)

    ## @brief 상자 id·위치 중복과 없는 상자를 가리키는 화살표를 거부해 재생기가 그릴 수 있는 프레임만 통과시킴
    @model_validator(mode='after')
    def validate_frame(self):
        ids = [box.id for box in self.boxes]
        if len(set(ids)) != len(ids):
            raise ValueError('Box IDs must be unique')
        cells = [(box.x, box.y) for box in self.boxes]
        if len(set(cells)) != len(cells):
            raise ValueError('Boxes must occupy distinct grid cells')
        for arrow in self.arrows:
            if arrow.source not in ids or arrow.target not in ids:
                raise ValueError('Arrows must reference existing box IDs')
        return self


## @brief 재생기에 넘기는 완성 결과. kind는 장면 종류(stack 등), frames[0]은 초기 상태
class Animation(Strict):
    kind: str
    title: str = Field(min_length=1, max_length=60)
    frames: list[Frame] = Field(min_length=1, max_length=MAX_FRAMES)


## @brief 프레임 수 상한을 한국어 오류로 먼저 확인한 뒤 Animation을 만듦
# - Pydantic 오류 대신 이유를 알려 줘야 채팅 모델이 연산 수를 줄여 다시 호출할 수 있음
def build_animation(kind: str, title: str, frames: list[Frame]) -> Animation:
    if len(frames) > MAX_FRAMES:
        raise ValueError(f'분량 초과: 단계가 {len(frames)}개로 상한 {MAX_FRAMES}개를 넘습니다. 연산 수를 줄이세요')
    return Animation(kind=kind, title=title, frames=frames)


## @brief 숫자 뒤 조사를 한국어 읽기(일·이·삼…)의 받침에 맞춰 고름. 캡션이 '1를'처럼 어색해지지 않게 함
# - 0으로 끝나면 영·십·백처럼 받침이 있고, 끝자리 2·4·5·9(이·사·오·구)만 받침이 없음
def josa(number: int, with_final: str, without_final: str) -> str:
    return without_final if str(abs(number))[-1] in '2459' else with_final


# --- 장면 요청: 모델이 고르는 입력. 연산 개수 상한은 한 장면의 교육적 분량 제한 ---

## @brief 배열에서 인덱스로 읽거나 지우는 연산
class ArrayIndexOp(Strict):
    op: Literal['access', 'delete']
    index: int = Field(ge=0, le=GRID_WIDTH - 1)


## @brief 배열의 인덱스 자리에 값을 넣고 뒤 원소를 미는 연산
class ArrayInsertOp(Strict):
    op: Literal['insert']
    index: int = Field(ge=0, le=GRID_WIDTH - 1)
    value: Value


## @brief 배열 장면. 초기 원소 8개·연산 6개는 한 줄에 그려 따라갈 수 있는 분량
class ArrayRequest(Strict):
    kind: Literal['array']
    values: list[Value] = Field(min_length=1, max_length=8)
    ops: list[Annotated[Union[ArrayIndexOp, ArrayInsertOp], Field(discriminator='op')]] = Field(max_length=6)


## @brief 연결 리스트 맨 앞 삽입 또는 값으로 찾아 삭제하는 연산
class ListValueOp(Strict):
    op: Literal['insert_head', 'delete']
    value: Value


## @brief target 값을 가진 첫 노드 뒤에 새 노드를 잇는 연산
class ListInsertAfterOp(Strict):
    op: Literal['insert_after']
    target: Value
    value: Value


## @brief 단일 연결 리스트 장면. 노드 6개 + 삽입 6번이어도 한 줄(12칸)에 들어감
class LinkedListRequest(Strict):
    kind: Literal['linked_list']
    values: list[Value] = Field(max_length=6)
    ops: list[Annotated[Union[ListValueOp, ListInsertAfterOp], Field(discriminator='op')]] = Field(max_length=6)


## @brief 스택에 값을 올리는 연산
class PushOp(Strict):
    op: Literal['push']
    value: Value


## @brief 스택 top을 꺼내는 연산
class PopOp(Strict):
    op: Literal['pop']


## @brief 빈 스택에서 시작하는 장면. 연산 10개(프레임 11개)는 MAX_FRAMES 안
class StackRequest(Strict):
    kind: Literal['stack']
    ops: list[Annotated[Union[PushOp, PopOp], Field(discriminator='op')]] = Field(min_length=1, max_length=10)


## @brief 큐 rear 뒤에 값을 넣는 연산
class EnqueueOp(Strict):
    op: Literal['enqueue']
    value: Value


## @brief 큐 front에서 값을 꺼내는 연산
class DequeueOp(Strict):
    op: Literal['dequeue']


## @brief 배열 기반 큐 장면. circular=False면 rear가 끝에 닿은 뒤 앞쪽 빈칸을 다시 쓰지 못함
class QueueRequest(Strict):
    kind: Literal['queue']
    capacity: int = Field(ge=2, le=8)
    circular: bool
    ops: list[Annotated[Union[EnqueueOp, DequeueOp], Field(discriminator='op')]] = Field(min_length=1, max_length=10)


## @brief 해시 테이블에 키를 넣거나 찾는 연산
class HashOp(Strict):
    op: Literal['insert', 'search']
    key: Value


## @brief h(k) = k mod buckets, 체이닝 해시 테이블 장면. 버킷 11개는 가로 12칸 안에 들어가는 수
class HashTableRequest(Strict):
    kind: Literal['hash_table']
    buckets: int = Field(ge=3, le=11)
    ops: list[HashOp] = Field(min_length=1, max_length=8)


## @brief 이진 탐색 트리에 값을 넣거나 지우는 연산
class BSTOp(Strict):
    op: Literal['insert', 'delete']
    value: Value


## @brief 빈 이진 탐색 트리에서 시작하는 장면
class BSTRequest(Strict):
    kind: Literal['bst']
    ops: list[BSTOp] = Field(min_length=1, max_length=10)


## @brief 힙 끝에 값을 넣고 sift-up하는 연산
class HeapInsertOp(Strict):
    op: Literal['insert']
    value: Value


## @brief 힙 루트를 꺼내고 sift-down하는 연산
class HeapExtractOp(Strict):
    op: Literal['extract']


## @brief 빈 최소·최대 힙에서 시작하는 장면. 교환 단계까지 포함한 프레임 수는 시뮬레이터가 MAX_FRAMES로 막음
class HeapRequest(Strict):
    kind: Literal['heap']
    order: Literal['min', 'max']
    ops: list[Annotated[Union[HeapInsertOp, HeapExtractOp], Field(discriminator='op')]] = Field(min_length=1, max_length=10)


# 정점 이름: 노드 상자 안에 들어가는 세 글자 이내
NodeName = Annotated[str, Field(min_length=1, max_length=3)]


## @brief 그래프 탐색(BFS·DFS) 장면. 정점 8개·간선 12개는 격자 한 화면에 선이 읽히는 분량
# - 이웃은 이름 오름차순으로 방문(교재 예시와 같은 결정적 순서). 시작 정점에서 닿지 않는 정점은 방문하지 않음
# - edges의 각 항목은 [끝점, 끝점]. 가이드 디코딩이 튜플(prefixItems)보다 길이 2 배열을 안정적으로 다뤄 이 형식을 씀
class GraphTraversalRequest(Strict):
    kind: Literal['graph_traversal']
    nodes: list[NodeName] = Field(min_length=2, max_length=8)
    edges: list[Annotated[list[NodeName], Field(min_length=2, max_length=2)]] = Field(max_length=12)
    directed: bool
    start: NodeName
    order: Literal['bfs', 'dfs']

    ## @brief 정점 이름 중복, 없는 시작 정점, 없는 정점을 잇는 간선, 자기 간선, 중복 간선을 거부
    # - 무방향 그래프에서 [A,B]와 [B,A]는 같은 간선이라 중복으로 봄
    @model_validator(mode='after')
    def validate_graph(self):
        if len(set(self.nodes)) != len(self.nodes):
            raise ValueError('정점 이름이 중복되었습니다')
        if self.start not in self.nodes:
            raise ValueError(f'시작 정점 {self.start}: nodes에 없는 이름입니다')
        seen = set()
        for a, b in self.edges:
            if a not in self.nodes or b not in self.nodes:
                raise ValueError(f'간선 {a}-{b}: 끝점이 nodes에 없습니다')
            if a == b:
                raise ValueError(f'간선 {a}-{b}: 정점 자신을 잇는 간선은 쓸 수 없습니다')
            key = (a, b) if self.directed else tuple(sorted((a, b)))
            if key in seen:
                raise ValueError(f'간선 {a}-{b}: 이미 있는 간선입니다')
            seen.add(key)
        return self


## @brief 정렬 장면. 원소 8개는 한 줄에 그리고 단계 수가 MAX_FRAMES 안에 드는 분량
# - insertion: 원소 하나를 제자리에 넣을 때마다, merge: 구간 병합마다, quick: Lomuto 분할(피벗은 마지막 원소)마다 프레임 하나
class SortRequest(Strict):
    kind: Literal['sort']
    algorithm: Literal['insertion', 'merge', 'quick']
    values: list[Value] = Field(min_length=2, max_length=8)


## @brief 트리 순회 장면. values를 차례로 이진 탐색 트리에 넣은 뒤 전위·중위·후위 순서로 방문
class TreeTraversalRequest(Strict):
    kind: Literal['tree_traversal']
    values: list[Value] = Field(min_length=1, max_length=9)
    order: Literal['preorder', 'inorder', 'postorder']


## @brief CPU 스케줄링 장면의 프로세스 하나
# - 도착 0~9·실행 1~6은 총 길이가 간트 12칸 안에 들어가도록 고른 범위
# - priority는 숫자가 작을수록 먼저 실행. priority 알고리즘에서만 쓰고 다른 알고리즘에서는 무시함
class Proc(Strict):
    name: Literal['P1', 'P2', 'P3', 'P4', 'P5']
    arrival: int = Field(ge=0, le=9)
    burst: int = Field(ge=1, le=6)
    priority: int | None = Field(default=None, ge=1, le=5)


## @brief CPU 스케줄링 간트 차트 장면. 비선점 FCFS·SJF·우선순위와 RR만 다룸(선점 SRTF는 범위 밖)
# - quantum은 rr에서만 씀. 다른 알고리즘에서 받은 값은 무시함
#   형식 예시 하나(quantum·priority 포함)를 개념별 알고리즘 제한으로 바꿔 써도 그대로 유효하게 하기 위함
class CpuScheduleRequest(Strict):
    kind: Literal['cpu_schedule']
    algorithm: Literal['fcfs', 'sjf', 'rr', 'priority']
    processes: list[Proc] = Field(min_length=1, max_length=5)
    quantum: int | None = Field(default=None, ge=1, le=4)

    ## @brief 이름 중복, rr의 quantum 누락, priority의 우선순위 누락, 간트 칸 수 초과를 거부
    # - 총 길이는 도착 순으로 실행해 계산: 쉬지 않는 스케줄러는 알고리즘과 관계없이 마지막 종료 시각이 같음
    @model_validator(mode='after')
    def validate_schedule(self):
        names = [p.name for p in self.processes]
        if len(set(names)) != len(names):
            raise ValueError('프로세스 이름이 중복되었습니다')
        if self.algorithm == 'rr' and self.quantum is None:
            raise ValueError('rr에는 quantum(1~4)이 필요합니다')
        if self.algorithm == 'priority' and any(p.priority is None for p in self.processes):
            raise ValueError('priority 알고리즘은 모든 프로세스에 priority(1~5)가 필요합니다')
        end = 0
        for p in sorted(self.processes, key=lambda p: (p.arrival, p.name)):
            end = max(end, p.arrival) + p.burst
        if end > GRID_WIDTH:
            raise ValueError(f'총 길이 {end}: 간트 칸 {GRID_WIDTH}칸을 넘습니다. 실행 시간이나 도착 시각을 줄이세요')
        return self


## @brief 재귀 호출 스택 장면. factorial은 0~6, fib는 0~3을 전체 호출·반환으로 그림
# - 교육적 분량 제한: 초기 1단계 + 호출·반환 각 1단계, factorial(6)은 13단계·fib(3)은 11단계
# - fib(4)는 9회 호출로 19단계가 되어 MAX_FRAMES를 넘음. 임의 함수를 실행하지 않고 두 함수만 허용
class CallStackRequest(Strict):
    kind: Literal['call_stack']
    function: Literal['factorial', 'fib']
    n: int = Field(ge=0, le=6)

    ## @brief 지수적으로 늘어나는 fib 호출 수를 제한해 모든 호출을 생략 없이 따라가게 함
    @model_validator(mode='after')
    def validate_call_count(self):
        if self.function == 'fib' and self.n > 3:
            raise ValueError('fib는 n=0~3만 지원합니다. 모든 호출·반환을 16단계 안에 그리기 위한 분량 제한입니다')
        return self


# 과목별 장면 요청 모듈: 위의 Strict·Value 등을 쓰므로 정의가 끝난 이 자리에서 가져옴(순환 import 방지)
from .db import DB_REQUESTS  # noqa: E402
from .network import NetworkRequest  # noqa: E402

# 모델이 고르는 장면 요청 전체. kind 값으로 요청 타입을 구분함
AnimationRequest = Annotated[
    Union[ArrayRequest, LinkedListRequest, StackRequest, QueueRequest, HashTableRequest, BSTRequest, HeapRequest,
          GraphTraversalRequest, SortRequest, TreeTraversalRequest, CpuScheduleRequest, CallStackRequest,
          *DB_REQUESTS, NetworkRequest],
    Field(discriminator='kind')]


## @brief JSON 문자열·dict 요청을 AnimationRequest로 검증하는 진입점이자 도구 스키마의 원본
class AnimationRequestModel(RootModel[AnimationRequest]):
    pass
