## @file
# @brief 하네스 역할(judge·ground·repair·scope) 미세조정 학습 예시 한 행의 형식을 만듦
# - 한 행은 운영 하네스가 모델에 보내는 것과 같은 system 지시문·user JSON과, 정답 assistant JSON으로 이루어짐
# - 운영과 글자 하나라도 다르면 학습한 입력 모양과 실제 입력 모양이 어긋나므로, 지시문은 PROMPTS를, user는 게이트웨이와 같은 구성·직렬화를 씀
import json

from serving.concept_map import ConceptMap
from serving.harness_llm import scope_payload
from serving.stuck_llm import PROMPTS


## @brief 공백 없는 한 줄 JSON으로 직렬화; 한글은 이스케이프하지 않음
# - 운영 xgrammar 문법(disable_any_whitespace)이 허용하는 모양과 같아야 미세조정 모델이 운영에서 같은 토큰열을 냄
def compact(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))


## @brief 역할·user 입력·정답 출력으로 chat 형식 학습 행 하나를 만듦
# - user는 StuckModelGateway._call과 같은 json.dumps(ensure_ascii=False) 기본 직렬화: 운영 요청과 글자 그대로 같게 하기 위함
def chat_row(role: str, payload: dict, output: dict) -> dict:
    return {'role': role, 'messages': [{'role': 'system', 'content': PROMPTS[role]},
                                       {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
                                       {'role': 'assistant', 'content': compact(output)}]}


## @brief 일치 판정(judge) 학습 행; 문장과 근거 문단을 HarnessGateway.judge와 같은 키 순서로 넣고 판정·근거 구절을 정답으로 둠
# - 어떤 판정이 정답인지(교란 방식별 규칙)는 여기서 정하지 않음: 호출하는 데이터 생성기가 verdict·evidence를 정해 넘김
def judge_row(sentence: str, passage: str, verdict: str, evidence: str) -> dict:
    return chat_row('judge', {'sentence': sentence, 'source': passage}, {'verdict': verdict, 'evidence': evidence})


## @brief 범위 결정(scope) 학습 행; user는 운영과 같은 scope_payload, 정답은 개념 ID 목록과 후속 질문 여부
# - expected가 비어 있으면(지도 밖 질문) 운영 schema대로 ["none"]을 정답으로 씀
def scope_row(question: str, previous: list[str], expected: list[str], follow_up: bool, concept_map: ConceptMap) -> dict:
    titles = {c.id: c.title for c in concept_map.concepts}
    payload = scope_payload(question, previous, concept_map, lambda cid: titles.get(cid, cid))
    return chat_row('scope', payload, {'concepts': expected or ['none'], 'follow_up': follow_up})
