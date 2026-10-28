## @file
# @brief 근거 검사 하네스의 모델 역할 4가지(범위 결정·근거 생성·일치 판정·수정)를 vLLM(OpenAI 호환) 모델에 연결
# - StuckModelGateway를 이어받아 같은 _call(모델 잠금·재시도·LLMUnavailable 변환)과 역할 설정표(stuck_llm.ROLE_SETTINGS)를 씀
# - 모든 역할은 JSON schema로 형식을 묶고, 받은 뒤 harness.py 검증 규칙으로 다시 확인함(실패하면 _call이 재시도)
# - 판정 근거 구절의 문단 대조와 단계 흐름은 하지 않음: 호출 순서와 판정 처리는 하네스 흐름(계획 Task 6)이 정함
import json
from typing import Literal

from serving.concept_map import ConceptMap
from serving.harness import MAX_SENTENCE_CHARS, MAX_SENTENCES, validate_sentences
from serving.stuck_llm import StuckModelGateway, _schema

VERDICTS = ('supported', 'unsupported', 'partial')


## @brief 문장 {text, sources} 하나의 schema; 근거 키를 실제 근거로 묶어 없는 번호를 만들지 못하게 함
def _sentence_schema(source_keys):
    return {'type': 'object', 'additionalProperties': False, 'required': ['text', 'sources'],
            'properties': {'text': {'type': 'string', 'maxLength': MAX_SENTENCE_CHARS},
                           'sources': {'type': 'array', 'minItems': 1, 'items': {'type': 'string', 'enum': sorted(source_keys)}}}}


## @brief 하네스판 모델 게이트웨이; 막힘 흐름의 역할(explain 등)도 그대로 물려받아 쓸 수 있음
class HarnessGateway(StuckModelGateway):
    ## @brief 5-1 범위 결정; 질문이 다루는 개념 ID 1~2개와 후속 질문 여부를 반환, 지도 밖이면 ([], follow_up)
    # - previous: 직전 턴의 개념 ID 목록. 모델이 "더 쉽게" 같은 후속 요청을 알아보게 제목과 함께 넘김
    # - 선택지를 지도 ID와 "none"으로 묶고, 받은 뒤에도 ID와 "none" 단독 여부를 다시 확인
    # - 같은 ID를 두 번 고른 경우는 하나로 줄임: 같은 개념 문단을 두 번 검색하지 않기 위함
    def scope(self, question: str, previous: list[str], concept_map: ConceptMap) -> tuple[list[str], bool]:
        ids = [c.id for c in concept_map.concepts]
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['concepts', 'follow_up'],
                  'properties': {'concepts': {'type': 'array', 'minItems': 1, 'maxItems': 2,
                                              'items': {'type': 'string', 'enum': ids + ['none']}},
                                 'follow_up': {'type': 'boolean'}}}

        def parse(content):
            data = json.loads(content)
            concepts, follow_up = list(dict.fromkeys(data['concepts'])), data['follow_up']
            if not isinstance(follow_up, bool) or not 1 <= len(concepts) <= 2:
                raise ValueError(f'범위 결정 형식 오류: {data}')
            if concepts == ['none']:
                return [], follow_up
            if any(c not in ids for c in concepts):
                raise ValueError(f'지도에 없는 개념이거나 none이 섞임: {concepts}')
            return concepts, follow_up

        user = {'concepts': [{'id': c.id, 'title': c.title, 'description': c.description} for c in concept_map.concepts],
                'previous': [{'id': p, 'title': self._title(p)} for p in previous],
                'question': question}
        return self._call('scope', user, parse, _schema('scope', schema))

    ## @brief 5-3 근거 기반 생성; 근거 키별 원문으로 (문장, 근거 키 묶음) 목록을 받음
    # - sources: {'p1': 문단 원문, 'a1': 애니메이션 단계 설명, ...}. 키 목록이 곧 허용 근거 번호
    # - prior: 직전 답에서 검사를 통과한 문장(후속 질문일 때). 같은 문장을 되풀이하지 않고 요청에 맞게 다시 쓰게 함
    def ground(self, question: str, sources: dict[str, str], concept_titles: list[str],
               prior: list[str]) -> list[tuple[str, tuple[str, ...]]]:
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['sentences'],
                  'properties': {'sentences': {'type': 'array', 'minItems': 1, 'maxItems': MAX_SENTENCES,
                                               'items': _sentence_schema(sources)}}}
        user = {'question': question, 'concepts': concept_titles,
                'sources': [{'key': key, 'text': text} for key, text in sources.items()], 'prior': prior}
        return self._call('ground', user, lambda content: validate_sentences(json.loads(content), set(sources)),
                          _schema('ground', schema))

    ## @brief 5-4 일치 검사; 문장 하나와 그 문장이 가리킨 근거 원문만 주고 (판정, 근거 구절)을 받음
    # - 다른 문장이나 생성 이유는 넘기지 않음: 앞뒤 문맥에 끌려 근거 없는 문장을 맞다고 보지 않게 하기 위함
    # - 근거 구절이 원문에 그대로 있는지는 여기서 보지 않음: 흐름이 evidence_in으로 확인하고 없으면 틀림으로 처리
    def judge(self, sentence: str, source_text: str) -> tuple[Literal['supported', 'unsupported', 'partial'], str]:
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['verdict', 'evidence'],
                  'properties': {'verdict': {'type': 'string', 'enum': list(VERDICTS)},
                                 'evidence': {'type': 'string', 'maxLength': 400}}}

        def parse(content):
            data = json.loads(content)
            if data['verdict'] not in VERDICTS or not isinstance(data['evidence'], str):
                raise ValueError(f'판정 형식 오류: {data}')
            return data['verdict'], data['evidence']

        return self._call('judge', {'sentence': sentence, 'source': source_text}, parse, _schema('judge', schema))

    ## @brief 5-5 수정; 틀린 문장을 같은 근거 원문에 맞게 고친 문장 하나와 근거 키를 받음
    # - 근거 키는 source_key 하나로 묶음: 수정 문장도 같은 문단으로 다시 판정하기 때문
    def repair(self, sentence: str, source_text: str, source_key: str) -> tuple[str, tuple[str, ...]]:
        user = {'sentence': sentence, 'source': source_text, 'source_key': source_key}
        return self._call('repair', user,
                          lambda content: validate_sentences({'sentences': [json.loads(content)]}, {source_key})[0],
                          _schema('repair', _sentence_schema({source_key})))
