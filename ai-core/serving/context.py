"""Fit complete recent exchanges to the actual vLLM chat-template token budget."""
import json

from serving.tools.diagram import TOOL


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


def fit_context(system, history, current, count_tokens, input_budget):
    # Keep the current request/tool loop intact; remove only whole oldest exchanges.
    def build(start):
        return [system, *history[start * 2:], *current]

    minimum = count_tokens(build(len(history) // 2))
    if minimum > input_budget:
        raise ValueError('현재 질문과 도식/도구 결과가 컨텍스트 예산을 초과했습니다.')
    low, high = 0, len(history) // 2
    while low < high:
        middle = (low + high) // 2
        if count_tokens(build(middle)) <= input_budget:
            high = middle
        else:
            low = middle + 1
    messages = build(low)
    return messages, low * 2, count_tokens(messages)


def token_counter(http, model):
    def count(messages):
        response = http.post('/tokenize', json={
            'model': model, 'messages': messages, 'tools': [TOOL],
            'add_generation_prompt': True,
            'chat_template_kwargs': {'enable_thinking': False},
        })
        response.raise_for_status()
        return response.json()['count']
    return count
