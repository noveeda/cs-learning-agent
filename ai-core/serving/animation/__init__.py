# 파일 역할: 애니메이션 패키지 진입점. 검증된 장면 요청을 종류별 시뮬레이터로 보내고, 채팅 artifact 형식의 결과를 만듦
# - 채팅 도구(show_animation)와 막힘 보충(S4)이 함께 쓰는 단일 경로
"""Deterministic step-by-step data structure animations."""
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


# 역할: 도구 인자(JSON 문자열)를 검증·실행하고 기존 도식과 같은 artifact 결과 dict를 반환
# - request를 함께 돌려줘 이후 "이 장면을 바꿔 줘" 같은 재참조 요청에 쓸 수 있게 함
# - 스키마 위반(Pydantic ValidationError)과 실행 불가 연산 모두 ValueError 계열로 올라감
def create_animation(arguments: str) -> dict:
    request = AnimationRequestModel.model_validate_json(arguments).root
    animation = simulate(request)
    return {'ok': True, 'artifact_id': uuid4().hex, 'kind': 'animation',
            'animation': animation.model_dump(), 'request': request.model_dump()}
