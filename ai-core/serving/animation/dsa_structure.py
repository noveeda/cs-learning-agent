## @file
# @brief 자료구조·알고리즘(dsa) 과목의 트리·그래프 용어 장면: 루트·리프·깊이·높이·서브트리, 연결 요소·경로·사이클
# - 요청 모델과 시뮬레이터를 한 파일에 둠. model.py는 REQUESTS를 합집합에, __init__.py는 SIMULATORS·FORMATS를 등록 표에 한 줄로 합침
# - 트리 배치(tidy_layout)는 재귀 호출 트리(dsa_analysis.fib_dp)도 함께 씀
# - 용어 판정(리프·깊이·사이클 등)은 실제 그래프 탐색으로 계산하고, 그 결과를 강조 tone과 캡션으로만 보여 줌
"""DSA tree terminology and graph property scenes."""
from collections import deque
from types import SimpleNamespace
from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from .dsa_linear import josa_word
from .graph import layout
from .model import GRID_HEIGHT, GRID_WIDTH, Animation, Arrow, Box, Frame, NodeName, Strict, build_animation

# 간선 하나: [부모, 자식] 또는 [끝점, 끝점]. 기존 그래프 장면과 같은 길이 2 배열 형식
Edge = Annotated[list[NodeName], Field(min_length=2, max_length=2)]


# --- 장면 요청 ---

## @brief 인자 없는 트리 질문: 루트·리프·높이
class TreeSimpleOp(Strict):
    op: Literal['root', 'leaves', 'height']


## @brief 노드 하나에 대한 트리 질문: 자식·부모·서브트리·루트에서의 경로
class TreeNodeOp(Strict):
    op: Literal['children', 'parent', 'subtree', 'path']
    node: NodeName


## @brief 깊이 level에 있는 노드 묻기
class TreeDepthOp(Strict):
    op: Literal['depth']
    level: int = Field(ge=0, le=GRID_HEIGHT - 1)


## @brief 트리 용어 장면. edges의 [부모, 자식]으로 트리를 정의하고(자식 순서는 나열 순서), ops마다 해당 노드를 강조
class TreeRequest(Strict):
    kind: Literal['dsa_tree']
    edges: list[Edge] = Field(min_length=1, max_length=11)
    ops: list[Annotated[Union[TreeSimpleOp, TreeNodeOp, TreeDepthOp], Field(discriminator='op')]] = Field(min_length=1, max_length=6)

    ## @brief 트리 조건을 확인: 자식마다 부모가 하나, 부모가 없는 루트가 정확히 하나, 루트에서 모든 노드에 닿음(사이클 없음)
    @model_validator(mode='after')
    def validate_tree(self):
        parents = {}
        for parent, child in self.edges:
            if parent == child:
                raise ValueError(f'간선 {parent}-{child}: 자기 자신을 자식으로 둘 수 없습니다')
            if child in parents:
                raise ValueError(f'{child}: 부모가 둘 이상이면 트리가 아닙니다')
            parents[child] = parent
        nodes = {name for edge in self.edges for name in edge}
        roots = nodes - set(parents)
        if len(roots) != 1:
            raise ValueError('부모가 없는 루트가 정확히 하나여야 트리입니다')
        children = tree_children(self.edges)
        reached, stack = set(), list(roots)
        while stack:
            node = stack.pop()
            reached.add(node)
            stack.extend(children.get(node, []))
        if reached != nodes:
            raise ValueError('루트에서 닿지 않는 노드가 있어(사이클) 트리가 아닙니다')
        return self


## @brief 그래프 전체 질문: 연결 요소(무방향만)·사이클 찾기
class GraphSimpleOp(Strict):
    op: Literal['components', 'cycle']


## @brief source에서 target까지 간선 수가 가장 적은 경로 찾기(방향 그래프는 간선 방향으로만)
class GraphPathOp(Strict):
    op: Literal['path']
    source: NodeName
    target: NodeName


## @brief 간선 하나를 더하는 연산
class GraphAddOp(Strict):
    op: Literal['add']
    edge: Edge


## @brief 그래프 용어 장면. 정점 8개·간선 12개(추가 포함)는 기존 그래프 장면의 다각형 배치 상한
class GraphPartsRequest(Strict):
    kind: Literal['graph_parts']
    nodes: list[NodeName] = Field(min_length=2, max_length=8)
    edges: list[Edge] = Field(max_length=12)
    directed: bool
    ops: list[Annotated[Union[GraphSimpleOp, GraphPathOp, GraphAddOp], Field(discriminator='op')]] = Field(min_length=1, max_length=5)

    ## @brief 정점 이름 중복과 nodes에 없는 이름(간선·연산)을 거부. 자기 간선·중복 간선은 시뮬레이터가 추가 순서대로 확인
    @model_validator(mode='after')
    def validate_names(self):
        if len(set(self.nodes)) != len(self.nodes):
            raise ValueError('정점 이름이 중복되었습니다')
        names = [name for edge in self.edges for name in edge]
        for op in self.ops:
            names += op.edge if op.op == 'add' else [op.source, op.target] if op.op == 'path' else []
        missing = [name for name in names if name not in self.nodes]
        if missing:
            raise ValueError(f'{missing[0]}: nodes에 없는 정점입니다')
        return self


REQUESTS = (TreeRequest, GraphPartsRequest)


# --- 트리 ---

## @brief [부모, 자식] 간선을 부모 → 자식 목록(나열 순서 유지)으로 바꿈
def tree_children(edges) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {}
    for parent, child in edges:
        children.setdefault(parent, []).append(child)
    return children


## @brief 트리를 격자에 배치: 깊이를 y로, 리프를 왼쪽부터 같은 간격으로 두고 부모는 첫·마지막 자식의 가운데
# - 서로 다른 서브트리는 리프 구간이 겹치지 않아 같은 깊이의 노드끼리 x가 겹치지 않음
# - 리프가 6개 이하면 두 칸 간격으로 벌려 형제 사이 선이 보이게 하고, 그보다 많으면 한 칸 간격
# - 격자(12×8)를 넘으면 한국어 ValueError
def tidy_layout(root: str, children: dict[str, list[str]]) -> dict[str, tuple[int, int]]:
    leaves = []

    def collect(node):
        kids = children.get(node, [])
        if not kids:
            leaves.append(node)
        for kid in kids:
            collect(kid)
    collect(root)
    if len(leaves) > GRID_WIDTH:
        raise ValueError(f'리프가 {len(leaves)}개라 가로 {GRID_WIDTH}칸에 그릴 수 없습니다')
    gap = 2 if len(leaves) <= GRID_WIDTH // 2 else 1
    places: dict[str, tuple[int, int]] = {}

    def place(node, depth):
        if depth >= GRID_HEIGHT:
            raise ValueError(f'깊이가 {depth}{josa_word(str(depth), "이라", "라")} 세로 {GRID_HEIGHT}칸에 그릴 수 없습니다')
        kids = children.get(node, [])
        if kids:
            xs = [place(kid, depth + 1) for kid in kids]
            x = (xs[0] + xs[-1]) // 2
        else:
            x = leaves.index(node) * gap
        places[node] = (x, depth)
        return x
    place(root, 0)
    return places


## @brief 트리 용어 장면 실행. 질문마다 답이 되는 노드를 new, 기준 노드를 active로 강조하고 계산한 답을 캡션에 적음
def simulate_tree(req: TreeRequest) -> Animation:
    children = tree_children(req.edges)
    parent = {child: p for p, child in req.edges}
    nodes = list(dict.fromkeys(name for edge in req.edges for name in edge))
    root = next(node for node in nodes if node not in parent)
    places = tidy_layout(root, children)
    depth = {node: y for node, (_, y) in places.items()}
    leaves = [node for node in nodes if node not in children]
    height = max(depth.values())

    def frame(caption, new=(), active=()):
        boxes = [Box(id=f't_{node}', label=node, x=places[node][0], y=places[node][1], shape='node',
                     tone='new' if node in new else 'active' if node in active else 'normal') for node in nodes]
        arrows = [Arrow(source=f't_{p}', target=f't_{c}', head=False) for p, c in req.edges]
        return Frame(boxes=boxes, arrows=arrows, caption=caption)

    def subtree(node):
        found = [node]
        for kid in children.get(node, []):
            found += subtree(kid)
        return found

    def path_to(node):
        path = [node]
        while path[-1] in parent:
            path.append(parent[path[-1]])
        return path[::-1]

    frames = [frame(f'노드 {len(nodes)}개, 간선 {len(req.edges)}개인 트리')]
    for op in req.ops:
        if op.op in ('children', 'parent', 'subtree', 'path') and op.node not in places:
            raise ValueError(f'{op.op} {op.node}: 트리에 없는 노드입니다')
        if op.op == 'root':
            frames.append(frame(f'루트 {root}: 부모가 없는 유일한 노드(깊이 0)', new=[root]))
        elif op.op == 'leaves':
            frames.append(frame(f'리프 {", ".join(leaves)}: 자식이 없는 노드', new=leaves))
        elif op.op == 'height':
            deepest = next(node for node in nodes if depth[node] == height)
            caption = f'높이 {height}: 루트에서 가장 먼 리프 {deepest}까지 간선 {height}개'
            perfect = all(len(children.get(n, [])) in (0, 2) for n in nodes) and all(depth[n] == height for n in leaves)
            if perfect:
                caption += f', 포화 이진 트리라 노드 2^{height + 1}-1 = {len(nodes)}개'
            frames.append(frame(caption, new=path_to(deepest)))
        elif op.op == 'depth':
            level = [node for node in nodes if depth[node] == op.level]
            if not level:
                raise ValueError(f'depth {op.level}: 그 깊이에 노드가 없습니다(높이 {height})')
            frames.append(frame(f'깊이 {op.level}: {", ".join(level)}(루트에서 간선 {op.level}개, 노드 {len(level)}개)', new=level))
        elif op.op == 'children':
            kids = children.get(op.node, [])
            caption = (f'{op.node}의 자식: {", ".join(kids)}' if kids
                       else f'{op.node}{josa_word(op.node, "은", "는")} 자식이 없어 리프입니다')
            frames.append(frame(caption, new=kids, active=[op.node]))
        elif op.op == 'parent':
            if op.node == root:
                frames.append(frame(f'{op.node}{josa_word(op.node, "은", "는")} 루트라 부모가 없습니다', active=[op.node]))
            else:
                frames.append(frame(f'{op.node}의 부모: {parent[op.node]}', new=[parent[op.node]], active=[op.node]))
        elif op.op == 'subtree':
            found = subtree(op.node)
            frames.append(frame(f'{op.node}{josa_word(op.node, "을", "를")} 루트로 하는 서브트리: 노드 {len(found)}개, 이것도 트리',
                                new=found))
        else:
            path = path_to(op.node)
            frames.append(frame(f'루트→{op.node} 경로: {" → ".join(path)}(깊이 {depth[op.node]})', new=[op.node], active=path[:-1]))
    return build_animation('dsa_tree', '트리 용어', frames)


# --- 그래프 ---

## @brief 그래프 용어 장면 실행
# - 정점 배치는 기존 그래프 탐색 장면(graph.layout)의 다각형 자리를 그대로 씀(추가될 간선까지 포함해 한 번만 배치)
# - components는 요소 하나마다 프레임 하나, cycle·path·add는 연산 하나마다 프레임 하나
def simulate_graph_parts(req: GraphPartsRequest) -> Animation:
    edges: list[tuple[str, str]] = []

    def key(a, b):
        return (a, b) if req.directed else tuple(sorted((a, b)))

    def add(a, b):
        if a == b:
            raise ValueError(f'간선 {a}-{b}: 정점 자신을 잇는 간선은 쓸 수 없습니다')
        if key(a, b) in {key(*e) for e in edges}:
            raise ValueError(f'간선 {a}-{b}: 이미 있는 간선입니다')
        if len(edges) == 12:
            raise ValueError('간선은 12개까지만 그립니다')
        edges.append((a, b))

    for a, b in req.edges:
        add(a, b)
    added = [op.edge for op in req.ops if op.op == 'add']
    places = layout(SimpleNamespace(nodes=req.nodes, edges=req.edges + added, start=req.nodes[0]))
    ids = {node: f'g{i}' for i, node in enumerate(req.nodes)}

    def frame(caption, new=(), active=()):
        boxes = [Box(id=ids[n], label=n, x=places[n][0], y=places[n][1], shape='node',
                     tone='new' if n in new else 'active' if n in active else 'normal') for n in req.nodes]
        arrows = [Arrow(source=ids[a], target=ids[b], head=req.directed) for a, b in edges]
        return Frame(boxes=boxes, arrows=arrows, caption=caption)

    def neighbors():
        adj = {n: [] for n in req.nodes}
        for a, b in edges:
            adj[a].append(b)
            if not req.directed:
                adj[b].append(a)
        return {n: sorted(adj[n]) for n in req.nodes}

    def components():
        adj, seen, groups = neighbors(), set(), []
        for start in req.nodes:
            if start in seen:
                continue
            group, stack = [], [start]
            seen.add(start)
            while stack:
                node = stack.pop()
                group.append(node)
                for nxt in adj[node]:
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
            groups.append([n for n in req.nodes if n in group])
        return groups

    ## @brief DFS로 사이클 하나를 찾아 정점 순서로 반환(없으면 None)
    # - 무방향: 부모로 돌아가는 간선은 제외하고 이미 방문한 정점을 만나면 사이클
    # - 방향: 현재 탐색 경로(스택)에 있는 정점으로 가는 간선이 사이클
    def find_cycle():
        adj, state, trail = neighbors(), {}, []

        def visit(node, came_from):
            state[node] = 'open'
            trail.append(node)
            for nxt in adj[node]:
                if not req.directed and nxt == came_from:
                    continue
                if state.get(nxt) == 'open':
                    return trail[trail.index(nxt):]
                if nxt not in state:
                    found = visit(nxt, node)
                    if found:
                        return found
            state[node] = 'done'
            trail.pop()
            return None
        for node in req.nodes:
            if node not in state:
                found = visit(node, None)
                if found:
                    return found
        return None

    def shortest_path(source, target):
        adj, back, queue = neighbors(), {source: None}, deque([source])
        while queue:
            node = queue.popleft()
            if node == target:
                path = [node]
                while back[path[-1]] is not None:
                    path.append(back[path[-1]])
                return path[::-1]
            for nxt in adj[node]:
                if nxt not in back:
                    back[nxt] = node
                    queue.append(nxt)
        return None

    kind = '방향' if req.directed else '무방향'
    frames = [frame(f'정점 {len(req.nodes)}개, 간선 {len(edges)}개인 {kind} 그래프')]
    for op in req.ops:
        if op.op == 'components':
            if req.directed:
                raise ValueError('components: 연결 요소는 무방향 그래프에서만 구합니다')
            groups = components()
            for number, group in enumerate(groups, start=1):
                tail = f', 연결 요소는 모두 {len(groups)}개' if number == len(groups) else ''
                frames.append(frame(f'연결 요소 {number}: {", ".join(group)}(서로 경로로 이어짐){tail}', new=group))
        elif op.op == 'cycle':
            cycle = find_cycle()
            if cycle:
                frames.append(frame(f'사이클: {" → ".join(cycle + cycle[:1])}', new=cycle))
            elif not req.directed and len(components()) == 1:
                frames.append(frame('사이클 없음: 모두 연결돼 있고 사이클이 없으니 트리입니다', active=req.nodes))
            else:
                frames.append(frame('사이클 없음'))
        elif op.op == 'path':
            path = shortest_path(op.source, op.target)
            if path:
                frames.append(frame(f'{op.source}→{op.target} 경로: {" → ".join(path)}(간선 {len(path) - 1}개)',
                                    new=[op.source, op.target], active=path[1:-1]))
            else:
                frames.append(frame(f'{op.source}에서 {op.target}{josa_word(op.target, "으로", "로")} 가는 경로가 없습니다',
                                    new=[op.source, op.target]))
        else:
            a, b = op.edge
            add(a, b)
            frames.append(frame(f'간선 {a}{"→" if req.directed else "-"}{b} 추가(간선 {len(edges)}개)', new=[a, b]))
    return build_animation('graph_parts', f'{kind} 그래프 용어', frames)


SIMULATORS = {'dsa_tree': simulate_tree, 'graph_parts': simulate_graph_parts}

# 장면 선택 요청에 넣는 형식 예시(짧고 평범한 값; SCENE_FORMATS와 같은 역할)
FORMATS = {
    'dsa_tree': {'kind': 'dsa_tree', 'edges': [['A', 'B'], ['A', 'C']], 'ops': [{'op': 'root'}, {'op': 'leaves'}]},
    'graph_parts': {'kind': 'graph_parts', 'nodes': ['A', 'B', 'C'], 'edges': [['A', 'B']], 'directed': False,
                    'ops': [{'op': 'path', 'source': 'A', 'target': 'B'}]},
}
