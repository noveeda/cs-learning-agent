# 파일 역할: 그래프 탐색(BFS·DFS)과 트리 순회(전위·중위·후위) 장면 요청을 실제로 실행하며 단계별 프레임을 만드는 시뮬레이터
# - 방문 하나마다 프레임 하나. 이번 단계에 방문한 정점은 new, 이미 방문한 정점은 active, 아직 안 간 정점은 normal
# - 화면 아래 두 줄에 방문 순서(y=6 또는 7)와 큐·스택 상태(y=7)를 함께 그려, 순서가 왜 그렇게 정해지는지 보이게 함
# - 방향 간선은 화살표, 무방향 간선은 화살촉 없는 선(Arrow.head=False) 하나로 그림
"""Graph traversal and binary tree traversal simulators."""
from itertools import count

from .hierarchical import TreeNode, bst_frame, descend
from .model import GRID_HEIGHT, Animation, Arrow, Box, Frame, GraphTraversalRequest, TreeTraversalRequest, build_animation

# 정점 수별 자리: 볼록 다각형의 꼭짓점을 위쪽부터 시계 방향으로(x 0..8, y 0..5)
# - 볼록 다각형 위에 두면 두 정점을 잇는 선이 다른 정점을 지나지 않음
# - 화면 픽셀(칸 64×56)로 정점 중심끼리 120px 이상, 간선과 다른 정점 중심 사이 35px 이상 떨어지도록 고른 자리
#   (정점은 폭 44·높이 40이라 사이 선이 보이고 간선이 정점 테두리를 지나지 않음)
# - 가로를 9칸(x 0..8)으로 묶어 막힘 패널 폭에서도 정점·이름표 글자를 읽을 수 있게 함. 방문 줄도 최대 9칸(이름표+정점 8개)
# - 아래 두 줄(y 6·7)은 방문 순서와 큐·스택 칸으로 비워 둠
SLOTS = {
    2: [(2, 2), (6, 2)],
    3: [(4, 0), (7, 5), (1, 5)],
    4: [(1, 0), (7, 0), (7, 5), (1, 5)],
    5: [(4, 0), (8, 2), (6, 5), (2, 5), (0, 2)],
    6: [(2, 0), (6, 0), (8, 2), (6, 5), (2, 5), (0, 2)],
    7: [(3, 0), (7, 0), (8, 2), (7, 4), (4, 5), (1, 4), (0, 2)],
    8: [(2, 0), (6, 0), (8, 1), (8, 4), (6, 5), (2, 5), (0, 4), (0, 1)],
}
VISIT_ROW, WAIT_ROW = GRID_HEIGHT - 2, GRID_HEIGHT - 1
# 캡션 상한(Frame.caption max_length)
CAPTION_MAX = 80


# 역할: 긴 캡션(정점·값 목록이 길 때)을 상한 안의 짧은 문장으로 바꿈
def fit(caption: str, short: str) -> str:
    return caption if len(caption) <= CAPTION_MAX else short


# 역할: 이웃 목록을 이름 오름차순으로 만듦. 무방향이면 양쪽에 넣음
def adjacency(req: GraphTraversalRequest, directed: bool) -> dict[str, list[str]]:
    adj = {node: set() for node in req.nodes}
    for a, b in req.edges:
        adj[a].add(b)
        if not directed:
            adj[b].add(a)
    return {node: sorted(adj[node]) for node in req.nodes}


# 역할: 정점을 정점 수에 맞는 다각형 자리에 배치. 시작 정점부터 깊이 우선 순서로 돌며 자리를 주고, 닿지 않는 정점은 그 뒤에 둠
# - 경로를 따라 이웃한 자리에 놓여 선이 덜 엇갈림. 방향과 탐색 방식에 관계없이 같은 배치라 BFS·DFS를 같은 그림으로 비교할 수 있음
def layout(req: GraphTraversalRequest) -> dict[str, tuple[int, int]]:
    adj, order = adjacency(req, directed=False), []

    def visit(node):
        order.append(node)
        for nxt in adj[node]:
            if nxt not in order:
                visit(nxt)
    for node in [req.start] + req.nodes:
        if node not in order:
            visit(node)
    return dict(zip(order, SLOTS[len(order)]))


# 역할: 한 단계 화면. 정점·간선 + 방문 순서 줄 + 큐(BFS)·스택(DFS) 줄
def graph_frame(req, places, visited, current, waiting, caption) -> Frame:
    ids = {node: f'n{i}' for i, node in enumerate(req.nodes)}
    boxes = []
    for node in req.nodes:
        tone = 'new' if node == current else 'active' if node in visited else 'normal'
        boxes.append(Box(id=ids[node], label=node, x=places[node][0], y=places[node][1], shape='node', tone=tone))
    arrows = []
    for a, b in req.edges:
        arrows.append(Arrow(source=ids[a], target=ids[b], head=req.directed))
    boxes.append(Box(id='lv', label='방문', x=0, y=VISIT_ROW, shape='label'))
    boxes += [Box(id=f'v{i}', label=node, x=i + 1, y=VISIT_ROW, shape='cell', tone='new' if node == current else 'normal')
              for i, node in enumerate(visited)]
    boxes.append(Box(id='lw', label='queue' if req.order == 'bfs' else 'stack', x=0, y=WAIT_ROW, shape='label'))
    boxes += [Box(id=f'q{i}', label=node, x=i + 1, y=WAIT_ROW, shape='cell') for i, node in enumerate(waiting)]
    return Frame(boxes=boxes, arrows=arrows, caption=caption)


# 역할: BFS 장면 실행. 큐에 넣을 때 발견 표시를 해 같은 정점을 두 번 넣지 않음(교재의 표준 BFS)
def simulate_bfs(req: GraphTraversalRequest, places) -> list[Frame]:
    adj = adjacency(req, req.directed)
    queue, seen, visited = [req.start], {req.start}, []
    frames = [graph_frame(req, places, visited, None, queue, f'시작 정점 {req.start}부터 BFS: 큐에 {req.start} 추가')]
    while queue:
        node = queue.pop(0)
        visited.append(node)
        added = [nxt for nxt in adj[node] if nxt not in seen]
        seen.update(added)
        queue += added
        caption = (f'{node} 방문 → 큐 앞에서 꺼냄, 새 이웃 {", ".join(added)} 큐 뒤에 추가' if added
                   else f'{node} 방문 → 큐 앞에서 꺼냄, 새로 넣을 이웃 없음')
        frames.append(graph_frame(req, places, visited, node, queue, fit(caption, f'{node} 방문 → 새 이웃 {len(added)}개를 큐에 추가')))
    return frames


# 역할: DFS 장면 실행(재귀). 스택 줄은 지금 들어가 있는 호출 경로(시작 → 현재)
# - 되돌아가는 단계는 따로 프레임을 만들지 않고 다음 방문 캡션에 "어디까지 돌아갔는지"를 적음
#   정점 8개에 방문·되돌아감을 모두 그리면 MAX_FRAMES(16)를 넘기 때문
def simulate_dfs(req: GraphTraversalRequest, places) -> list[Frame]:
    adj = adjacency(req, req.directed)
    path, visited, returned = [], [], []
    frames = [graph_frame(req, places, visited, None, path, f'시작 정점 {req.start}부터 DFS: 갈 수 있는 데까지 먼저 내려감')]

    def visit(node, parent):
        path.append(node)
        visited.append(node)
        if returned:
            caption = f'{returned[0]}에서 더 갈 곳이 없어 {parent}까지 되돌아간 뒤, {node} 방문'
        elif parent:
            caption = f'{parent}의 아직 안 간 이웃 중 이름이 앞선 {node} 방문'
        else:
            caption = f'{node} 방문'
        returned.clear()
        frames.append(graph_frame(req, places, visited, node, path, fit(caption, f'{node} 방문')))
        for nxt in adj[node]:
            if nxt not in visited:
                visit(nxt, node)
        path.pop()
        if not returned:
            returned.append(node)
    visit(req.start, None)
    return frames


# 역할: 그래프 탐색 장면 실행. 마지막 프레임은 큐·스택이 빈 상태에서 전체 방문 순서를 정리함
def simulate_graph_traversal(req: GraphTraversalRequest) -> Animation:
    places = layout(req)
    frames = simulate_bfs(req, places) if req.order == 'bfs' else simulate_dfs(req, places)
    visited = [box.label for box in frames[-1].boxes if box.id.startswith('v')]
    name = 'BFS' if req.order == 'bfs' else 'DFS'
    caption = f'{name} 끝: 방문 순서 {" → ".join(visited)}'
    frames.append(graph_frame(req, places, visited, None, [], fit(caption, f'{name} 끝: {len(visited)}개 정점 방문')))
    kind = '방향' if req.directed else '무방향'
    return build_animation('graph_traversal', f'{name}({kind} 그래프, 시작 {req.start})', frames)


TRAVERSAL_NAMES = {'preorder': '전위', 'inorder': '중위', 'postorder': '후위'}
# 순회 방식별 방문 시점 설명; 캡션마다 붙여 "왜 지금 이 노드인지"를 보여 줌
TRAVERSAL_RULES = {'preorder': '자신을 먼저 보고 왼쪽, 오른쪽으로 내려감',
                   'inorder': '왼쪽 서브트리를 마친 뒤 자신을 봄',
                   'postorder': '왼쪽, 오른쪽 서브트리를 마친 뒤 자신을 봄'}


# 역할: 트리 순회 장면 실행. values를 차례로 이진 탐색 트리에 넣고, 순서대로 방문하는 노드마다 프레임 하나
# - 맨 아래 줄(y=7)을 방문 순서 칸으로 쓰므로 트리는 7층(깊이 0~6)까지만 그림
def simulate_tree_traversal(req: TreeTraversalRequest) -> Animation:
    ids, root = count(), None
    for value in req.values:
        path, node = descend(root, value)
        if node:
            raise ValueError(f'values의 {value}: 이미 넣은 값이라 이진 탐색 트리에 다시 넣을 수 없습니다')
        if len(path) >= GRID_HEIGHT - 1:
            raise ValueError(f'values의 {value}: 트리가 {GRID_HEIGHT - 1}층을 넘어 그릴 수 없습니다. 값 순서를 섞어 높이를 줄이세요')
        new = TreeNode(f't{next(ids)}', value)
        if not path:
            root = new
        elif value < path[-1].value:
            path[-1].left = new
        else:
            path[-1].right = new

    order = []

    def walk(node):
        if node:
            if req.order == 'preorder':
                order.append(node)
            walk(node.left)
            if req.order == 'inorder':
                order.append(node)
            walk(node.right)
            if req.order == 'postorder':
                order.append(node)
    walk(root)

    name = TRAVERSAL_NAMES[req.order]

    def frame(visited, caption):
        base = bst_frame(root, caption, active=[n.id for n in visited[:-1]], new=[n.id for n in visited[-1:]])
        cells = [Box(id='lv', label=name, x=0, y=GRID_HEIGHT - 1, shape='label')]
        cells += [Box(id=f'v{i}', label=str(n.value), x=i + 1, y=GRID_HEIGHT - 1, shape='cell',
                      tone='new' if i == len(visited) - 1 else 'normal') for i, n in enumerate(visited)]
        return Frame(boxes=base.boxes + cells, arrows=base.arrows, caption=caption)

    frames = [frame([], f'values를 차례로 넣은 이진 탐색 트리. {name} 순회: {TRAVERSAL_RULES[req.order]}')]
    for i in range(len(order)):
        frames.append(frame(order[:i + 1], f'{order[i].value} 방문: {TRAVERSAL_RULES[req.order]}'))
    return build_animation('tree_traversal', f'트리 {name} 순회', frames)
