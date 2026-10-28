## @file
# @brief 완료된 문답과 도식·애니메이션을 모델 메시지로 바꾸고 실제 입력 토큰 예산에 맞춰 최근 대화 선택
"""Fit complete recent exchanges to the actual vLLM chat-template token budget."""
import json

from serving.animation import ANIMATION_TOOL
from serving.tools.diagram import TOOL

# 채팅 요청(agent.run_agent)과 /tokenize 토큰 계산(token_counter)이 함께 쓰는 도구 목록
# - agent가 이 모듈을 import하므로 순환 import를 피하려고 여기에 두고 agent.TOOLS로 다시 내보냄
TOOLS = [TOOL, ANIMATION_TOOL]


## @brief 저장된 artifact를 모델에 다시 보여 줄 최소 데이터로 바꿈
# - 애니메이션은 프레임 전체 대신 장면 요청만 넘김. 프레임은 요청에서 다시 계산되고 문맥 토큰만 차지하기 때문
# - 도식은 지금처럼 그래프 구조를 넘겨 수정 요청에 쓰게 함
def artifact_data(artifact):
    if artifact.get('kind') == 'animation':
        return {'artifact_id': artifact['artifact_id'], 'animation_request': artifact['request']}
    return {'artifact_id': artifact['artifact_id'], 'diagram': artifact['diagram']}


## @brief 문답에 생성 도식·애니메이션 데이터를 붙이고 user/assistant 쌍 순서를 검증
def history_messages(history):
    messages = []
    for turn in history:
        content = turn['content']
        if turn.get('artifacts'):
            data = [artifact_data(a) for a in turn['artifacts']]
            content += '\n\n[생성한 도식 데이터]\n' + json.dumps(data, ensure_ascii=False)
        messages.append({'role': turn['role'], 'content': content})
    if len(messages) % 2 or any(m['role'] != ('user' if i % 2 == 0 else 'assistant')
                                for i, m in enumerate(messages)):
        raise ValueError('대화 기록은 완료된 질문·답변 쌍이어야 합니다.')
    return messages


## @brief 현재 질문·도구 결과를 보존하면서 예산에 맞는 가장 긴 최근 문답 구간 반환
def fit_context(system, history, current, count_tokens, input_budget):
    # Keep the current request/tool loop intact; remove only whole oldest exchanges.
    ## @brief 오래된 문답 쌍을 start개 제외한 후보 메시지 구성
    def build(start):
        return [system, *history[start * 2:], *current]

    minimum = count_tokens(build(len(history) // 2))
    if minimum > input_budget:
        raise ValueError('현재 질문과 도식/도구 결과가 컨텍스트 예산을 초과했습니다.')
    low, high = 0, len(history) // 2
    # 오래된 문답을 더 제외할수록 입력이 줄어드는 성질을 이용해 이분 탐색; 토큰 계산 호출 수 절감
    while low < high:
        middle = (low + high) // 2
        if count_tokens(build(middle)) <= input_budget:
            high = middle
        else:
            low = middle + 1
    messages = build(low)
    return messages, low * 2, count_tokens(messages)


## @brief 실제 모델 서버의 채팅 템플릿으로 토큰 수를 계산하는 함수 생성
def token_counter(http, model):
    ## @brief 도구 스키마까지 포함해 /tokenize에 전달하고 입력 토큰 수 반환
    def count(messages):
        response = http.post('/tokenize', json={
            'model': model, 'messages': messages, 'tools': TOOLS,
            'add_generation_prompt': True,
            'chat_template_kwargs': {'enable_thinking': False},
        })
        response.raise_for_status()
        return response.json()['count']
    return count
