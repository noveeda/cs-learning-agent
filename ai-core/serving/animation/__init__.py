# 파일 역할: 애니메이션 패키지 진입점. 검증된 장면 요청을 종류별 시뮬레이터로 보내고, 채팅 artifact 형식의 결과를 만듦
# - 채팅 도구(show_animation)와 막힘 보충(S4)이 함께 쓰는 단일 경로
"""Deterministic step-by-step data structure animations."""
import json
from uuid import uuid4

from .hierarchical import simulate_bst, simulate_hash_table, simulate_heap
from .linear import simulate_array, simulate_linked_list, simulate_queue, simulate_stack
from .model import Animation, AnimationRequest, AnimationRequestModel

# 장면 종류(kind) → 시뮬레이터. 요청 스키마의 kind 목록과 같아야 함
SIMULATORS = {
    'array': simulate_array,
    'linked_list': simulate_linked_list,
    'stack': simulate_stack,
    'queue': simulate_queue,
    'hash_table': simulate_hash_table,
    'bst': simulate_bst,
    'heap': simulate_heap,
}


# 역할: 검증된 요청을 실행해 프레임을 계산. 실행할 수 없는 연산이면 한국어 ValueError
def simulate(request: AnimationRequest) -> Animation:
    return SIMULATORS[request.kind](request)


# 역할: 값이 null인 키를 모든 깊이에서 제거한 사본을 반환
# - 모델은 평평한 도구 스키마를 보고 다른 kind 전용 필드를 null로 채워 보낼 수 있음
# - null이 아닌 엉뚱한 필드는 남겨 두어 strict 검증이 거부하게 함(모델이 고쳐 다시 부름)
def drop_nulls(value):
    if isinstance(value, dict):
        return {k: drop_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [drop_nulls(v) for v in value]
    return value


# 역할: 도구 인자(JSON 문자열)를 검증·실행하고 기존 도식과 같은 artifact 결과 dict를 반환
# - request를 함께 돌려줘 이후 "이 장면을 바꿔 줘" 같은 재참조 요청에 쓸 수 있게 함
# - JSON 문법 오류, 스키마 위반(Pydantic ValidationError), 실행 불가 연산 모두 ValueError 계열로 올라감
def create_animation(arguments: str) -> dict:
    request = AnimationRequestModel.model_validate(drop_nulls(json.loads(arguments))).root
    animation = simulate(request)
    return {'ok': True, 'artifact_id': uuid4().hex, 'kind': 'animation',
            'animation': animation.model_dump(), 'request': request.model_dump()}


# 역할: 요청 스키마의 모든 연산 이름(op)을 모음. 도구 스키마의 op 목록이 모델 정의와 어긋나지 않게 함
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
    'description': ('Show a step-by-step animation of data structure operations. The server runs the '
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
                'heap: order + ops insert{value}, extract (1-10).')},
            'values': {'type': 'array', 'items': INTEGER,
                       'description': 'Initial values. array: 1-8 items, linked_list: 0-6 items. Other kinds: omit.'},
            'capacity': {'type': 'integer', 'description': 'queue only. Array size from 2 to 8.'},
            'circular': {'type': 'boolean', 'description': 'queue only. true for a circular queue.'},
            'buckets': {'type': 'integer', 'description': 'hash_table only. Bucket count from 3 to 11.'},
            'order': {'type': 'string', 'enum': ['min', 'max'], 'description': 'heap only.'},
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
        'required': ['kind', 'ops'],
    },
}}
