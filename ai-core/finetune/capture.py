## @file
# @brief 26B 하네스 실행 중 모델 호출을 녹화하고, 그중 성공한 호출만 학습 행으로 바꿈
# - 녹화는 OpenAI 클라이언트를 감싸서 함: 운영 게이트웨이 코드(재시도·검증)를 고치지 않고 client_factory만 바꿔 끼움
# - ground·repair는 정답을 구성으로 만들 수 없어 26B가 실제로 낸 출력 중 검사를 통과한 것을 정답으로 씀
import json
from types import SimpleNamespace
from typing import Callable

from finetune.examples import chat_row
from serving.stuck_llm import PROMPTS


## @brief OpenAI 클라이언트 래퍼; chat.completions.create 호출마다 {role, messages, content, finish_reason}를 sink에 넣음
# - role은 system 지시문으로 PROMPTS에서 역으로 찾음. 하네스 밖 지시문이면 None
# - 연결 실패처럼 응답 자체가 없는 호출은 남기지 않음(예외는 그대로 게이트웨이에 넘겨 원래 재시도를 따름)
# - 기록은 jsonl로 저장할 수 있게 dict·문자열만 담음
class RecordingClient:
    def __init__(self, client, sink: list[dict]):
        self.client, self.sink = client, sink
        self.models = client.models
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def __enter__(self):
        self.client.__enter__()
        return self

    def __exit__(self, *exc):
        return self.client.__exit__(*exc)

    def _create(self, **kwargs):
        response = self.client.chat.completions.create(**kwargs)
        messages = [dict(m) for m in kwargs['messages']]
        try:
            choice = response.choices[0]
            content, finish_reason = choice.message.content, choice.finish_reason
        except (IndexError, TypeError, AttributeError):
            # 응답 구조가 빠진 경우도 실패 녹화로 남김: 게이트웨이는 이를 재시도하고, recorded_rows는 학습 행으로 쓰지 않음
            content, finish_reason = None, None
        role = next((r for r, prompt in PROMPTS.items() if messages[0]['content'] == prompt), None)
        self.sink.append({'role': role, 'messages': messages, 'content': content, 'finish_reason': finish_reason})
        return response


## @brief 녹화에서 같은 요청(역할·messages)마다 정상 종료·JSON 해석·keep 검사를 모두 통과한 마지막 응답만 학습 행으로 만듦
# - 재시도 전의 실패 응답, 잘린 응답(finish_reason=length), 끝내 실패한 호출은 행이 되지 않음
# - 같은 요청을 묶는 기준은 순서가 아니라 내용: 동시 실행으로 녹화가 섞여도 시도들이 한 요청으로 모이게 하기 위함
# - keep: 역할별 검사(예: 한자 섞임, 재판정 결과)를 호출하는 쪽이 정함
# - assistant는 26B 원문 대신 compact로 다시 직렬화: 공백이 섞인 성공 응답도 운영 문법 모양으로 맞춤
def recorded_rows(records: list[dict], keep: Callable[[dict], bool]) -> list[dict]:
    last = {}
    for record in records:
        if record['role'] is None or record['finish_reason'] != 'stop' or not (record['content'] or '').strip():
            continue
        try:
            output = json.loads(record['content'])
        except ValueError:
            continue
        if keep(record):
            last[(record['role'], json.dumps(record['messages'], ensure_ascii=False))] = (record, output)
    return [chat_row(record['role'], json.loads(record['messages'][1]['content']), output)
            for record, output in last.values()]
