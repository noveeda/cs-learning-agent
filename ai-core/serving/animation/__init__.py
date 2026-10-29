## @file
# @brief 애니메이션 패키지 진입점. 검증된 장면 요청을 종류별 시뮬레이터로 보내고, 채팅 artifact 형식의 결과를 만듦
# - 채팅 도구(show_animation)와 막힘 보충(S4)이 함께 쓰는 단일 경로
# - 막힘 보충의 장면 선택에 쓰는 개념별 장면 표·제한표·형식 예시·응답 schema도 여기서 만듦(장면 정의와 한곳에서 맞추기 위함)
"""Deterministic step-by-step data structure animations."""
import json
from uuid import uuid4

from serving.tools.diagram import inline_schema

from .call_stack import simulate_call_stack
from .graph import simulate_graph_traversal, simulate_tree_traversal
from .hierarchical import simulate_bst, simulate_hash_table, simulate_heap
from .linear import simulate_array, simulate_linked_list, simulate_queue, simulate_stack
from .model import Animation, AnimationRequest, AnimationRequestModel
from .network import NETWORK_FORMATS, NETWORK_SIMULATORS
from .scheduling import simulate_cpu_schedule
from .sorting import simulate_sort

# 장면 종류(kind) → 시뮬레이터. 요청 스키마의 kind 목록과 같아야 함
SIMULATORS = {
    'array': simulate_array,
    'linked_list': simulate_linked_list,
    'stack': simulate_stack,
    'queue': simulate_queue,
    'hash_table': simulate_hash_table,
    'bst': simulate_bst,
    'heap': simulate_heap,
    'graph_traversal': simulate_graph_traversal,
    'sort': simulate_sort,
    'tree_traversal': simulate_tree_traversal,
    'cpu_schedule': simulate_cpu_schedule,
    'call_stack': simulate_call_stack,
    **NETWORK_SIMULATORS,
}


# 막힘 보충(S4)에서 개념마다 보여 줄 수 있는 장면 종류(개념 지도 ID → kind)
# - 여기 없는 개념은 장면 선택 LLM을 부르지 않음: 알맞은 장면이 없는 개념에 보충 지연만 늘지 않게 하기 위함
CONCEPT_SCENES: dict[str, tuple[str, ...]] = {
    'array': ('array',), 'dynamic-array': ('array',), 'memory-address': ('array',),
    'reference': ('linked_list',), 'linked-list': ('linked_list',),
    'stack': ('stack',), 'recursion': ('call_stack',),
    'queue': ('queue',),
    'hash-function': ('hash_table',), 'hash-table': ('hash_table',),
    # 해시 인덱스의 버킷 배치·체이닝·실제 키 비교 원리를 보여 줌(디스크 페이지 동작은 다루지 않음)
    'hash-index': ('hash_table',),
    'binary-tree': ('bst',), 'bst': ('bst',), 'tree-traversal': ('tree_traversal',),
    'heap': ('heap',), 'priority-queue': ('heap',), 'heap-sort': ('heap',),
    'graph': ('graph_traversal',), 'bfs': ('graph_traversal',), 'dfs': ('graph_traversal',),
    'sorting-basics': ('sort',), 'merge-sort': ('sort',), 'quick-sort': ('sort',),
    'scheduling-metrics': ('cpu_schedule',), 'fcfs': ('cpu_schedule',), 'sjf': ('cpu_schedule',),
    'round-robin': ('cpu_schedule',), 'priority-scheduling': ('cpu_schedule',),
}

# 개념별로 장면 필드의 허용 값을 좁히는 표(개념 지도 ID → {필드: 허용 값})
# - 같은 장면 종류라도 개념에 맞는 알고리즘만 보여 주기 위함. 예: 병합 정렬 보충에 삽입 정렬 장면이 나오지 않게 함
# - graph(그래프 기초)는 BFS로 둠: 시작점에서 가까운 정점부터 퍼지는 모습이 경로·연결 요소를 보여 주기에 알맞음
# - sorting-basics는 선택·삽입·버블 중 장면이 있는 삽입 정렬만. tree-traversal은 전위·중위·후위 모두 허용
# - priority-queue는 최소 힙만: 이 저장소의 우선순위 큐 문항이 모두 "숫자가 작을수록 먼저"라 보충 장면도 같은 방향이어야 함
#   heap·heap-sort는 최대·최소를 모두 다루므로 제한하지 않음
# - scheduling-metrics는 같은 입력의 대기 시간을 비교하기 쉬운 fcfs·sjf만 허용
# - 장면 선택 schema·형식 예시·응답 검증에 모두 적용(scene_schema, scene_formats, check_limits)
CONCEPT_SCENE_LIMITS: dict[str, dict[str, tuple[str, ...]]] = {
    'graph': {'order': ('bfs',)}, 'bfs': {'order': ('bfs',)}, 'dfs': {'order': ('dfs',)},
    'priority-queue': {'order': ('min',)},
    'sorting-basics': {'algorithm': ('insertion',)}, 'merge-sort': {'algorithm': ('merge',)}, 'quick-sort': {'algorithm': ('quick',)},
    'scheduling-metrics': {'algorithm': ('fcfs', 'sjf')}, 'fcfs': {'algorithm': ('fcfs',)}, 'sjf': {'algorithm': ('sjf',)},
    'round-robin': {'algorithm': ('rr',)}, 'priority-scheduling': {'algorithm': ('priority',)},
}

# 장면 종류별 입력 형식 예시; 장면 선택 요청에 허용 종류의 예시만 넣음
# - json_schema(가이드 디코딩)는 모델 입력에 보이지 않아, 예시가 없으면 모델이 연산 형식을 몰라 null로 쏠림
#   (실측 2026-10-26: 예시 없이 90건 중 null 81건 → 예시를 넣자 null 31건 → schema에서 null 분기도 뺌)
# - 값은 일부러 짧고 평범하게 둠: 모델이 예시를 그대로 베끼지 않고 학습 목표에 맞게 새로 고르게 하기 위함
SCENE_FORMATS = {
    'call_stack': {'kind': 'call_stack', 'function': 'factorial', 'n': 4},
    'array': {'kind': 'array', 'values': [2, 4, 6], 'ops': [{'op': 'access', 'index': 1}, {'op': 'insert', 'index': 0, 'value': 1},
                                                             {'op': 'delete', 'index': 2}]},
    'linked_list': {'kind': 'linked_list', 'values': [2, 4], 'ops': [{'op': 'insert_head', 'value': 1},
                                                                      {'op': 'insert_after', 'target': 2, 'value': 3},
                                                                      {'op': 'delete', 'value': 4}]},
    'stack': {'kind': 'stack', 'ops': [{'op': 'push', 'value': 1}, {'op': 'push', 'value': 2}, {'op': 'pop'}]},
    'queue': {'kind': 'queue', 'capacity': 4, 'circular': False, 'ops': [{'op': 'enqueue', 'value': 1}, {'op': 'dequeue'}]},
    'hash_table': {'kind': 'hash_table', 'buckets': 5, 'ops': [{'op': 'insert', 'key': 3}, {'op': 'search', 'key': 3}]},
    'bst': {'kind': 'bst', 'ops': [{'op': 'insert', 'value': 5}, {'op': 'insert', 'value': 2}, {'op': 'delete', 'value': 5}]},
    'heap': {'kind': 'heap', 'order': 'max', 'ops': [{'op': 'insert', 'value': 4}, {'op': 'extract'}]},
    'graph_traversal': {'kind': 'graph_traversal', 'nodes': ['A', 'B', 'C'], 'edges': [['A', 'B'], ['B', 'C']],
                        'directed': False, 'start': 'A', 'order': 'bfs'},
    'sort': {'kind': 'sort', 'algorithm': 'insertion', 'values': [3, 1, 2]},
    'tree_traversal': {'kind': 'tree_traversal', 'values': [2, 1, 3], 'order': 'inorder'},
    # quantum·priority를 함께 넣어 둠: 개념별 제한으로 algorithm을 rr·priority로 바꿔도 예시가 그대로 유효하게 하기 위함
    'cpu_schedule': {'kind': 'cpu_schedule', 'algorithm': 'fcfs', 'quantum': 2,
                     'processes': [{'name': 'P1', 'arrival': 0, 'burst': 3, 'priority': 2},
                                   {'name': 'P2', 'arrival': 1, 'burst': 2, 'priority': 1}]},
    **NETWORK_FORMATS,
}


## @brief 허용 종류(kinds)의 형식 예시를 반환. 제한표에 걸린 필드는 허용 값의 첫 번째로 바꿈
# - 예시가 schema와 어긋나면(merge-sort인데 예시는 insertion) 모델이 헷갈리기 때문
def scene_formats(kinds, limits=None) -> dict:
    formats = {}
    for kind in kinds:
        example = dict(SCENE_FORMATS[kind])
        for field, allowed in (limits or {}).items():
            if field in example:
                example[field] = allowed[0]
        formats[kind] = example
    return formats


## @brief 막힘 보충의 장면 선택 응답 schema {"scene": kinds에 든 장면 요청 중 하나}를 만듦(가이드 디코딩용)
# - null 분기를 두지 않음: 장면 목록(CONCEPT_SCENES)이 이미 장면이 맞는 개념만 고르고,
#   null을 열어 두면 모델이 장면이 분명한 개념(heap·hash-table)에서도 null로 쏠렸기 때문(실측 2026-10-26)
# - $ref를 펼치고(inline_schema), discriminator 키워드를 빼고 oneOf를 anyOf로 바꿈
#   가이드 디코딩 엔진이 $ref·discriminator를 다루지 못해도 같은 제약을 걸 수 있게 하기 위함(kind const로 구분되므로 뜻은 같음)
# - kinds 순서를 유지하고, 제한표(limits)의 필드는 enum을 허용 값으로 좁힘
def scene_schema(kinds, limits=None) -> dict:
    def clean(node):
        if isinstance(node, list):
            return [clean(value) for value in node]
        if not isinstance(node, dict):
            return node
        return {('anyOf' if key == 'oneOf' else key): clean(value) for key, value in node.items() if key != 'discriminator'}

    options = clean(inline_schema(AnimationRequestModel.model_json_schema()))['anyOf']
    allowed = [next(o for o in options if o['properties']['kind']['const'] == kind) for kind in kinds]
    for option in allowed:
        for field, values in (limits or {}).items():
            if field in option['properties']:
                option['properties'][field] = {'type': 'string', 'enum': list(values)}
    return {'type': 'object', 'additionalProperties': False, 'required': ['scene'],
            'properties': {'scene': {'anyOf': allowed}}}


## @brief 검증된 장면 요청이 제한표를 지키는지 확인. 어기면 ValueError(가이드 디코딩이 꺼진 서버에서 온 응답도 막기 위함)
def check_limits(request: AnimationRequest, limits=None) -> None:
    for field, values in (limits or {}).items():
        value = getattr(request, field, None)
        if value is not None and value not in values:
            raise ValueError(f'허용하지 않은 {field}: {value}')


## @brief 검증된 요청을 실행해 프레임을 계산. 실행할 수 없는 연산이면 한국어 ValueError
def simulate(request: AnimationRequest) -> Animation:
    return SIMULATORS[request.kind](request)


## @brief 값이 null인 키를 모든 깊이에서 제거한 사본을 반환
# - 모델은 평평한 도구 스키마를 보고 다른 kind 전용 필드를 null로 채워 보낼 수 있음
# - null이 아닌 엉뚱한 필드는 남겨 두어 strict 검증이 거부하게 함(모델이 고쳐 다시 부름)
def drop_nulls(value):
    if isinstance(value, dict):
        return {k: drop_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [drop_nulls(v) for v in value]
    return value


## @brief 도구 인자(JSON 문자열)를 검증·실행하고 기존 도식과 같은 artifact 결과 dict를 반환
# - request를 함께 돌려줘 이후 "이 장면을 바꿔 줘" 같은 재참조 요청에 쓸 수 있게 함
# - JSON 문법 오류, 스키마 위반(Pydantic ValidationError), 실행 불가 연산 모두 ValueError 계열로 올라감
def create_animation(arguments: str) -> dict:
    request = AnimationRequestModel.model_validate(drop_nulls(json.loads(arguments))).root
    animation = simulate(request)
    return {'ok': True, 'artifact_id': uuid4().hex, 'kind': 'animation',
            'animation': animation.model_dump(), 'request': request.model_dump()}


## @brief 요청 스키마의 모든 연산 이름(op)을 모음. 도구 스키마의 op 목록이 모델 정의와 어긋나지 않게 함
def op_names() -> list[str]:
    names = []
    for definition in AnimationRequestModel.model_json_schema()['$defs'].values():
        op = definition.get('properties', {}).get('op')
        if op:
            names += [name for name in op.get('enum', [op.get('const')]) if name not in names]
    return names


INTEGER = {'type': 'integer', 'description': 'Integer from -99 to 999.'}

# 채팅 모델에 보여 주는 show_animation 도구 정의
# - 운영 모델(Gemma 4) 채팅 템플릿은 parameters의 최상위 properties만 렌더링함
#   kind별 union(oneOf)을 그대로 넣으면 모델에게 parameters:{}로 보여 인자를 알 수 없으므로 평평한 object로 펼침
# - 실제 검증은 create_animation이 strict AnimationRequestModel로 함. 이 스키마는 모델 안내용
# - 연산 개수 상한은 한 장면을 학생이 따라갈 수 있는 교육적 분량 제한(model.py와 같은 값)
ANIMATION_TOOL = {'type': 'function', 'function': {
    'name': 'show_animation',
    'description': ('Show a step-by-step animation of data structure operations, recursive calls or CPU scheduling. The server runs the '
                    'operations and draws every step, so choose only the scene kind and its inputs. '
                    'Omit fields that do not belong to the chosen kind.'),
    'parameters': {
        'type': 'object',
        'properties': {
            'kind': {'type': 'string', 'enum': list(SIMULATORS), 'description': (
                'array: values + ops access/delete{index}, insert{index,value} (max 6 ops). '
                'linked_list: values + ops insert_head{value}, insert_after{target,value}, delete{value} (max 6). '
                'stack: ops push{value}, pop (1-10). '
                'queue: capacity, circular + ops enqueue{value}, dequeue (1-10). '
                'hash_table: buckets + ops insert{key}, search{key} (1-8), h(k) = k mod buckets with chaining. '
                'bst: ops insert{value}, delete{value} (1-10, at most 9 nodes, no duplicates). '
                'heap: order + ops insert{value}, extract (1-10). '
                'graph_traversal: nodes, edges, directed, start, order bfs|dfs (no ops). '
                'sort: algorithm + values (no ops). '
                'tree_traversal: values inserted into a BST, order preorder|inorder|postorder (no ops). '
                'cpu_schedule: algorithm fcfs|sjf|rr|priority + processes, quantum for rr (no ops); '
                'non-preemptive sjf/priority, total time with idle gaps at most 12. '
                'call_stack: function factorial|fib + n (no ops). factorial n=0..6, fib n=0..3; '
                'example {kind:call_stack,function:factorial,n:4}.')},
            'function': {'type': 'string', 'enum': ['factorial', 'fib'], 'description': 'call_stack only. Recursive function.'},
            'n': {'type': 'integer', 'minimum': 0, 'maximum': 6,
                  'description': 'call_stack only. factorial: 0-6; fib: 0-3. All calls and returns fit in 16 frames.'},
            'values': {'type': 'array', 'items': INTEGER,
                       'description': ('Initial values. array: 1-8 items, linked_list: 0-6 items, sort: 2-8 items, '
                                       'tree_traversal: 1-9 distinct items. Other kinds: omit.')},
            'capacity': {'type': 'integer', 'description': 'queue only. Array size from 2 to 8.'},
            'circular': {'type': 'boolean', 'description': 'queue only. true for a circular queue.'},
            'buckets': {'type': 'integer', 'description': 'hash_table only. Bucket count from 3 to 11.'},
            'order': {'type': 'string', 'enum': ['min', 'max', 'bfs', 'dfs', 'preorder', 'inorder', 'postorder'],
                      'description': 'heap: min|max. graph_traversal: bfs|dfs. tree_traversal: preorder|inorder|postorder.'},
            'nodes': {'type': 'array', 'items': {'type': 'string'},
                      'description': 'graph_traversal only. 2-8 unique vertex names of at most 3 characters.'},
            'edges': {'type': 'array', 'items': {'type': 'array', 'items': {'type': 'string'}},
                      'description': 'graph_traversal only. At most 12 [from, to] pairs of names in nodes.'},
            'directed': {'type': 'boolean', 'description': 'graph_traversal only.'},
            'start': {'type': 'string', 'description': 'graph_traversal only. A name in nodes.'},
            'algorithm': {'type': 'string', 'enum': ['insertion', 'merge', 'quick', 'fcfs', 'sjf', 'rr', 'priority'],
                          'description': 'sort: insertion|merge|quick. cpu_schedule: fcfs|sjf|rr|priority.'},
            'processes': {'type': 'array', 'description': 'cpu_schedule only. 1-5 processes with unique names.', 'items': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string', 'enum': ['P1', 'P2', 'P3', 'P4', 'P5']},
                    'arrival': {'type': 'integer', 'description': 'Arrival time from 0 to 9.'},
                    'burst': {'type': 'integer', 'description': 'CPU time from 1 to 6.'},
                    'priority': {'type': 'integer', 'description': 'priority only. 1 to 5, smaller runs first.'},
                },
                'required': ['name', 'arrival', 'burst'],
            }},
            'quantum': {'type': 'integer', 'description': 'cpu_schedule rr only. Time slice from 1 to 4.'},
            'ops': {'type': 'array', 'description': 'Operations run in order from the starting state.', 'items': {
                'type': 'object',
                'properties': {
                    'op': {'type': 'string', 'enum': op_names()},
                    'index': {'type': 'integer', 'description': 'array only. 0-based position.'},
                    'value': INTEGER,
                    'target': {'type': 'integer', 'description': 'linked_list insert_after only. Existing value to insert after.'},
                    'key': {'type': 'integer', 'description': 'hash_table only. Integer from -99 to 999.'},
                },
                'required': ['op'],
            }},
        },
        'required': ['kind'],
    },
}}
