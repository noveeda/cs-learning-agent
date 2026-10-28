## @file
# @brief 해시 테이블·이진 탐색 트리·힙 장면 요청을 실제 자료구조로 실행하며 단계별 프레임을 만드는 시뮬레이터
# - 해시 테이블·BST는 연산 하나마다 프레임 하나. 힙만 sift-up·sift-down 교환마다 프레임을 더해 MAX_FRAMES 안에서 허용
# - 트리는 깊이를 y로, 중위 순서를 x로 둠. 중위 순서는 노드마다 달라 상자가 겹치지 않음
# - 실행할 수 없는 연산(중복 삽입, 없는 값 삭제, 빈 힙 extract)과 그릴 수 없는 크기는 한국어 ValueError로 거부
"""Hash table, binary search tree and heap simulators."""
from dataclasses import dataclass
from itertools import count

from .linear import tone_of
from .model import (GRID_HEIGHT, Animation, Arrow, BSTRequest, Box, Frame, HashTableRequest, HeapRequest,
                    build_animation, josa)

# 체인 길이 상한: 버킷 줄(y=0) 아래 남은 세로 칸 수
CHAIN_MAX = GRID_HEIGHT - 1
# BST 노드 수 상한: 학생이 한 화면에서 경로를 따라갈 수 있는 교육적 분량
BST_MAX_NODES = 9


## @brief 버킷을 y=0 줄의 slot으로, 각 체인을 그 버킷 아래 세로 node 줄로 그리고 버킷→첫 노드→다음 노드를 화살표로 이음
def hash_frame(chains, caption, active=(), new=()) -> Frame:
    boxes, arrows = [], []
    for bucket, chain in enumerate(chains):
        previous = f'b{bucket}'
        boxes.append(Box(id=previous, label=str(bucket), x=bucket, y=0, shape='slot', tone=tone_of(previous, active)))
        for depth, (i, key) in enumerate(chain, start=1):
            boxes.append(Box(id=i, label=str(key), x=bucket, y=depth, shape='node', tone=tone_of(i, active, new)))
            arrows.append(Arrow(source=previous, target=i))
            previous = i
    return Frame(boxes=boxes, arrows=arrows, caption=caption)


## @brief 체이닝 해시 테이블 장면 실행. 캡션마다 h(k) 계산식을 넣고, 탐색 시 따라간 체인 노드를 active로 표시
# - 이미 있는 키의 insert는 집합처럼 넣지 않는 단계로 보여 줌(실행 불가가 아니므로 오류로 보지 않음)
def simulate_hash_table(req: HashTableRequest) -> Animation:
    ids = count()
    m = req.buckets
    chains: list[list[tuple[str, int]]] = [[] for _ in range(m)]
    frames = [hash_frame(chains, f'빈 해시 테이블(버킷 {m}개, h(k) = k mod {m})')]
    for op in req.ops:
        bucket = op.key % m
        formula = f'h({op.key}) = {op.key} mod {m} = {bucket}'
        chain = chains[bucket]
        found = next((j for j, (_, key) in enumerate(chain) if key == op.key), None)
        if op.op == 'search':
            visited = [i for i, _ in chain[:None if found is None else found + 1]]
            result = '체인 끝까지 없어 실패합니다' if found is None else f'체인 {found + 1}번째에서 찾았습니다'
            frames.append(hash_frame(chains, f'search {op.key}: {formula}, {result}', active=[f'b{bucket}', *visited]))
        elif found is not None:
            frames.append(hash_frame(chains, f'insert {op.key}: {formula}, 이미 있는 키라 넣지 않습니다',
                                     active=[f'b{bucket}', chain[found][0]]))
        else:
            if len(chain) == CHAIN_MAX:
                raise ValueError(f'insert {op.key}: 한 버킷의 체인은 {CHAIN_MAX}개까지만 그립니다')
            how = '충돌이라 체인 끝에 붙입니다' if chain else '빈 버킷에 넣습니다'
            chain.append((f'k{next(ids)}', op.key))
            frames.append(hash_frame(chains, f'insert {op.key}: {formula}, {how}', active=[f'b{bucket}'], new=[chain[-1][0]]))
    return build_animation('hash_table', f'해시 테이블(체이닝, 버킷 {m}개)', frames)


## @brief BST 노드. id는 생성 순서로 붙여 삭제·대체로 자리가 바뀌어도 같은 상자로 이어지게 함
@dataclass(eq=False)
class TreeNode:
    id: str
    value: int
    left: 'TreeNode | None' = None
    right: 'TreeNode | None' = None


## @brief 트리를 깊이(y)·중위 순서(x)로 배치하고 부모→자식 화살표를 그림
# - 삭제된 노드는 같은 단계에서만 원래 깊이에 removed로 남김. 값 순서로 x를 함께 매겨 다른 노드와 겹치지 않음
def bst_frame(root, caption, active=(), new=(), removed=None) -> Frame:
    placed, arrows = [], []

    def walk(node, depth):
        if node:
            walk(node.left, depth + 1)
            placed.append((node, depth))
            arrows.extend(Arrow(source=node.id, target=child.id) for child in (node.left, node.right) if child)
            walk(node.right, depth + 1)
    walk(root, 0)
    if removed:
        placed = sorted(placed + [removed], key=lambda pair: pair[0].value)
    boxes = [Box(id=node.id, label=str(node.value), x=x, y=depth, shape='node',
                 tone='removed' if removed and node is removed[0] else tone_of(node.id, active, new))
             for x, (node, depth) in enumerate(placed)]
    return Frame(boxes=boxes, arrows=arrows, caption=caption)


## @brief 값으로 노드를 찾으며 지나간 경로(루트부터 부모까지)와 노드를 반환. 없으면 node는 None
def descend(root, value):
    path, node = [], root
    while node and node.value != value:
        path.append(node)
        node = node.left if value < node.value else node.right
    return path, node


## @brief 이진 탐색 트리 장면 실행
# - insert: 비교 경로를 active로, 새 노드를 new로 표시
# - delete: 자식이 둘이면 중위 후속자 노드를 떼어 와 삭제 자리에 붙임. 값만 복사하지 않아 후속자 상자가 올라가는 모습이 보임
def simulate_bst(req: BSTRequest) -> Animation:
    ids = count()
    root, size = None, 0
    frames = [bst_frame(root, '빈 이진 탐색 트리에서 시작합니다')]
    for op in req.ops:
        path, node = descend(root, op.value)
        if op.op == 'insert':
            if node:
                raise ValueError(f'insert {op.value}: 이미 있는 값이라 넣을 수 없습니다(BST는 중복 없음)')
            if size == BST_MAX_NODES:
                raise ValueError(f'insert {op.value}: 노드는 {BST_MAX_NODES}개까지만 그립니다')
            if len(path) == GRID_HEIGHT:
                raise ValueError(f'insert {op.value}: 트리 높이가 {GRID_HEIGHT}층을 넘어 그릴 수 없습니다')
            new = TreeNode(f't{next(ids)}', op.value)
            if not path:
                root, caption = new, f'insert {op.value}: 빈 트리라 루트가 됩니다'
            else:
                parent = path[-1]
                side = '왼쪽' if op.value < parent.value else '오른쪽'
                setattr(parent, 'left' if op.value < parent.value else 'right', new)
                route = '→'.join(str(n.value) for n in path)
                caption = f'insert {op.value}: 비교 경로 {route}, {parent.value}의 {side} 자식으로 붙입니다'
            size += 1
            frames.append(bst_frame(root, caption, active=[n.id for n in path], new=[new.id]))
            continue
        if not node:
            raise ValueError(f'delete {op.value}: {op.value}{josa(op.value, "이", "가")} 트리에 없습니다')
        if node.left and node.right:
            successor_parent, successor = node, node.right
            while successor.left:
                successor_parent, successor = successor, successor.left
            if successor_parent is not node:
                successor_parent.left = successor.right
                successor.right = node.right
            successor.left = node.left
            replacement = successor
            caption = f'delete {op.value}: 자식이 둘이라 중위 후속자 {successor.value}{josa(successor.value, "이", "가")} 자리를 대신합니다'
        else:
            replacement = node.left or node.right
            caption = (f'delete {op.value}: 자식 {replacement.value}{josa(replacement.value, "이", "가")} {op.value}의 자리로 올라옵니다'
                       if replacement else f'delete {op.value}: 리프 노드라 바로 떼어 냅니다')
        if not path:
            root = replacement
        elif path[-1].left is node:
            path[-1].left = replacement
        else:
            path[-1].right = replacement
        size -= 1
        frames.append(bst_frame(root, caption, active=[replacement.id] if replacement else [], removed=(node, len(path))))
    return build_animation('bst', '이진 탐색 트리 insert·delete', frames)


## @brief 완전 이진 트리 크기 n에서 배열 인덱스 → 중위 순서(x)를 계산
def inorder_positions(n: int) -> dict[int, int]:
    order = []

    def walk(i):
        if i < n:
            walk(2 * i + 1)
            order.append(i)
            walk(2 * i + 2)
    walk(0)
    return {i: x for x, i in enumerate(order)}


## @brief 힙 장면 실행. 같은 원소를 트리 노드와 배열 칸 두 상자로 함께 그려 인덱스 관계(부모 (i-1)//2)를 보여 줌
# - 먼저 모든 단계를 기록하고 최대 깊이를 안 뒤 그림. 배열 줄(y=최대 깊이+2)이 프레임마다 출렁이지 않게 하기 위함
# - extract는 루트 제거 → 마지막 원소를 루트로 이동 → sift-down 교환 순서로 단계를 나눔
def simulate_heap(req: HeapRequest) -> Animation:
    better = (lambda a, b: a < b) if req.order == 'min' else (lambda a, b: a > b)
    sign = '<' if req.order == 'min' else '>'
    name = '최소 힙' if req.order == 'min' else '최대 힙'
    ids = count()
    heap: list[tuple[str, int]] = []
    states = [([], f'빈 {name}에서 시작합니다', {})]

    def record(caption, tones):
        states.append((list(heap), caption, tones))

    for op in req.ops:
        if op.op == 'insert':
            heap.append((str(next(ids)), op.value))
            i = len(heap) - 1
            record(f'insert {op.value}: 배열 끝 [{i}]에 넣습니다', {heap[i][0]: 'new'})
            while i and better(heap[i][1], heap[(i - 1) // 2][1]):
                parent = (i - 1) // 2
                caption = f'sift-up: {heap[i][1]} {sign} 부모 {heap[parent][1]}, 둘을 교환합니다'
                heap[i], heap[parent] = heap[parent], heap[i]
                record(caption, {heap[i][0]: 'active', heap[parent][0]: 'active'})
                i = parent
            continue
        if not heap:
            raise ValueError('extract: 힙이 비어 있어 꺼낼 원소가 없습니다')
        top = heap[0]
        record(f'extract: 루트 {top[1]}{josa(top[1], "을", "를")} 꺼냅니다', {top[0]: 'removed'})
        last = heap.pop()
        if not heap:
            continue
        heap[0] = last
        record(f'마지막 원소 {last[1]}{josa(last[1], "을", "를")} 루트로 옮깁니다', {last[0]: 'active'})
        i = 0
        while True:
            child = 2 * i + 1
            if child + 1 < len(heap) and better(heap[child + 1][1], heap[child][1]):
                child += 1
            if child >= len(heap) or not better(heap[child][1], heap[i][1]):
                break
            caption = f'sift-down: 자식 {heap[child][1]} {sign} {heap[i][1]}, 둘을 교환합니다'
            heap[i], heap[child] = heap[child], heap[i]
            record(caption, {heap[i][0]: 'active', heap[child][0]: 'active'})
            i = child
    # 최대 깊이 = bit_length - 1 이므로 배열 줄은 최대 깊이 + 2
    array_row = max(len(items) for items, _, _ in states).bit_length() + 1
    frames = []
    for items, caption, tones in states:
        x_of = inorder_positions(len(items))
        boxes, arrows = [], []
        for i, (element, value) in enumerate(items):
            tone = tones.get(element, 'normal')
            boxes.append(Box(id=f'h{element}', label=str(value), x=x_of[i], y=(i + 1).bit_length() - 1, shape='node', tone=tone))
            boxes.append(Box(id=f'a{element}', label=str(value), x=i, y=array_row, shape='cell', tone=tone))
            if i:
                arrows.append(Arrow(source=f'h{items[(i - 1) // 2][0]}', target=f'h{element}'))
        frames.append(Frame(boxes=boxes, arrows=arrows, caption=caption))
    return build_animation('heap', f'{name} insert·extract', frames)
