# 파일 역할: 완료된 문답과 도식을 모델 메시지로 바꾸고 실제 입력 토큰 예산에 맞춰 최근 대화 선택
"""Fit complete recent exchanges to the actual vLLM chat-template token budget."""
import json

from serving.tools.diagram import TOOL


# 역할: 문답에 생성 도식 데이터를 붙이고 user/assistant 쌍 순서를 검증
def history_messages(history):
    messages = []
    for turn in history:
        content = turn['content']
        if turn.get('artifacts'):
            data = [{'artifact_id': a['artifact_id'], 'diagram': a['diagram']}
                    for a in turn['artifacts']]
            content += '\n\n[생성한 도식 데이터]\n' + json.dumps(data, ensure_ascii=False)
        messages.append({'role': turn['role'], 'content': content})
    if len(messages) % 2 or any(m['role'] != ('user' if i % 2 == 0 else 'assistant')
                                for i, m in enumerate(messages)):
        raise ValueError('대화 기록은 완료된 질문·답변 쌍이어야 합니다.')
    return messages


# 역할: 현재 질문·도구 결과를 보존하면서 예산에 맞는 가장 긴 최근 문답 구간 반환
def fit_context(system, history, current, count_tokens, input_budget):
    # Keep the current request/tool loop intact; remove only whole oldest exchanges.
    # 역할: 오래된 문답 쌍을 start개 제외한 후보 메시지 구성
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


# 역할: 실제 모델 서버의 채팅 템플릿으로 토큰 수를 계산하는 함수 생성
def token_counter(http, model):
    # 역할: 도구 스키마까지 포함해 /tokenize에 전달하고 입력 토큰 수 반환
    def count(messages):
        response = http.post('/tokenize', json={
            'model': model, 'messages': messages, 'tools': [TOOL],
            'add_generation_prompt': True,
            'chat_template_kwargs': {'enable_thinking': False},
        })
        response.raise_for_status()
        return response.json()['count']
    return count
