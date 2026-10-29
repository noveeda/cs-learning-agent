## @file
# @brief [막혔어요] 흐름의 보충 설명·서술 채점·복귀 요약을 근거 검사 하네스로 바꾼 게이트웨이(CS_HARNESS=1일 때만 /stuck/step이 씀)
# - stuck.Gateway 계약을 그대로 지킴: explain은 마크다운 문자열, grade_explanation은 list[bool], summarize는 문자열
#   그래서 막힘 상태 모델·Spring·화면은 바뀌지 않음
# - 보충 설명은 하네스 5-2~5-6(검색 → 근거 생성 → 문장 판정 → 수정 → 표시)을 씀. 개념은 엔진이 이미 정했으므로 범위 결정(5-1)은 없음
# - 모델 잠금은 기존처럼 호출마다 잡음(요청 전체를 막지 않음). 한 보충 단계 안의 생성·판정·수정 호출 사이에 다른 요청이 끼어들 수 있고,
#   그때 판정 호출이 잠금을 못 잡으면 그 문장은 "확인 필요"가 됨(하네스 흐름 규칙)
import logging

from openai import OpenAIError

from rag.search import Passage
from rag.sources import BOOKS
from serving.concept_map import Concept, ConceptMap
from serving.harness import (NO_SOURCE_TEXT, _check_sentence, _sentence_markdown, fallback_answer, grading_evidence_in,
                             render_answer)
from serving.harness_llm import HarnessGateway
from serving.item_bank import Item
from serving.stuck import Entry, LLMUnavailable, StuckState

# 장면 단계로 근거를 삼은 보충에서 생성이 실패했을 때의 문구; 근거가 교재가 아니라 코드가 계산한 애니메이션 단계라 FAILED_TEXT와 나눔
SCENE_FAILED_TEXT = '설명을 만들지 못했습니다. 아래 애니메이션 단계를 확인해 주세요.'

# 보충에 이미 쓴 문단 id; 키는 (막힌 곳 entry, 개념 ID). 재보충(2·3차)이 앞 시도와 다른 문단으로 설명하게 함
# - 게이트웨이는 요청마다 새로 만들므로 프로세스 안 표에 둠. 서버가 재시작되면 잊고, 그때는 앞과 같은 문단을 써도 오류가 아님
# - 1차 보충에서 그 키의 기록을 새로 시작함
_USED_PASSAGES: dict[tuple[str, str], set[str]] = {}

# 받침 판정에 쓰는 읽는 소리: 영문자는 이름(엘·엠·엔·알만 받침), 숫자는 한자어 읽기(영·일·삼·육·칠·팔만 받침)
_LATIN_FINAL = set('lmnr')
_DIGIT_FINAL = set('013678')


## @brief 낱말 끝소리에 받침이 있는지; 한글은 음절 코드로, 영문·숫자는 읽는 소리로 판정
# - 끝의 괄호 설명("최단 경로(다익스트라)")은 건너뛰고 앞 낱말로 봄. 조사는 괄호 밖 낱말에 맞추기 때문
def _has_final(word: str) -> bool:
    word = word.rstrip()
    while word.endswith(')') and '(' in word:
        word = word[:word.rindex('(')].rstrip()
    last = next((ch for ch in reversed(word) if ch.isalnum()), '')
    if '가' <= last <= '힣':
        return (ord(last) - ord('가')) % 28 != 0
    return last.lower() in _LATIN_FINAL or last in _DIGIT_FINAL


## @brief 낱말 뒤에 받침에 맞는 조사를 붙임; particle은 받침 있는 형태('을' 또는 '은')로 받음
def with_particle(word: str, particle: str) -> str:
    return word + (particle if _has_final(word) else {'을': '를', '은': '는'}[particle])


## @brief 장면 단계 근거로 만든 답 마크다운: 본문 + 각주(애니메이션 n단계 캡션). 교재 출처 블록은 붙이지 않음
def _render_scene(sentences, steps: dict[str, Passage]) -> str:
    body = ' '.join(_sentence_markdown(s) for s in sentences)
    keys = dict.fromkeys(key for s in sentences for key in s.sources)
    notes = '\n\n'.join(f'[^{k}]: 애니메이션 {k[1:]}단계: {steps[k].text}' for k in keys)
    return f'{body}\n\n{notes}'


## @brief 장면 근거 생성이 실패했을 때의 답: 고정 문구 + 코드가 계산한 단계 캡션 목록(모델 문장 없음)
def _scene_fallback(steps: dict[str, Passage]) -> str:
    lines = '\n'.join(f'{n}. {p.text}' for n, p in enumerate(steps.values(), 1))
    return f'{SCENE_FAILED_TEXT}\n\n{lines}'


## @brief 막힘 흐름 Gateway의 하네스판; 장면 선택·개념 특정은 기존 StuckModelGateway 그대로 물려받음
# - retriever: rag.search.Retriever(테스트는 가짜). 보충 설명의 근거 문단을 고름
class HarnessStuckGateway(HarnessGateway):
    def __init__(self, concept_map: ConceptMap, lock, retriever, client_factory=None):
        super().__init__(concept_map, lock, client_factory)
        self.retriever = retriever

    ## @brief S4 보충 설명을 근거 검사 하네스로 만들어 render_answer 마크다운으로 돌려줌
    # - 근거: 1차는 개념 문단 상위 3개, 2차는 장면이 있으면 scene['steps']를 a1..aN으로(없으면 앞에서 안 쓴 문단),
    #   3차는 앞 시도에서 안 쓴 문단(안 쓴 문단이 없으면 상위 문단부터 다시). 기존 EXPLAIN_APPROACHES의 예시·비유 지시는 쓰지 않음(사용자 결정, 설계 5-3)
    # - 근거가 없으면 근거 없음 문구, 생성이 끝내 실패하거나 확인 필요 문장이 절반을 넘으면 원문(또는 장면 단계) 대체 답
    # - 시작할 때 모델 잠금이 잡혀 있으면(채팅 처리 중 등) 기존처럼 LLMUnavailable(→ 503, 상태 보존)
    #   - 이 확인이 없으면 바쁜 순간에 누른 [계속]이 재시도 안내 대신 대체 답으로 넘어가 보충 기회 한 번을 씀
    def explain(self, concept: Concept, attempt: int, entry: Entry, scene: dict | None = None) -> str:
        if not self.lock.acquire(blocking=False):
            raise LLMUnavailable('다른 요청을 처리 중입니다. 잠시 후 다시 시도해 주세요.')
        self.lock.release()
        key = (entry.model_dump_json(), concept.id)
        if attempt == 1 or key not in _USED_PASSAGES:
            _USED_PASSAGES[key] = set()
        if attempt == 2 and scene and scene.get('steps'):
            steps = {f'a{n}': Passage(f'scene:{n}', 'scene', '', '', step, 0.0) for n, step in enumerate(scene['steps'], 1)}
            return self._grounded(concept, steps, lambda sentences: _render_scene(sentences, steps),
                                  lambda: _scene_fallback(steps))
        try:
            query = f'{concept.title} {concept.description}'
            found = self.retriever.passages([concept.id], query, exclude=_USED_PASSAGES[key])
            if not found and _USED_PASSAGES[key]:
                # 남은 문단이 없으면 기억을 비우고 상위 문단부터 다시 씀
                # - 1단계 개념은 설명 차례 2부터 시작해 1차의 기억 초기화를 거치지 않으므로, 같은 막힘을 여러 세션 반복하면 문단이 바닥날 수 있음
                _USED_PASSAGES[key] = set()
                found = self.retriever.passages([concept.id], query, exclude=_USED_PASSAGES[key])
        except OpenAIError as exc:
            # 임베딩 서버 오류도 모델 오류처럼 503(상태 보존)으로 끝내 [계속]을 다시 누를 수 있게 함
            logging.warning('Stuck harness search failed for %s: %s', concept.id, exc)
            raise LLMUnavailable('교재 검색 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.') from None
        if not found:
            return NO_SOURCE_TEXT
        _USED_PASSAGES[key].update(p.id for p in found)
        passages = {f'p{n}': p for n, p in enumerate(found, 1)}
        return self._grounded(concept, passages, lambda sentences: render_answer(sentences, passages, BOOKS),
                              lambda: fallback_answer(passages, BOOKS))

    ## @brief 근거 생성 → 문장마다 판정·수정을 하고 render(문장 목록) 또는 fallback()의 결과를 돌려줌
    # - 질문 문장에 학습 목표를 넣어 생성이 목표에 맞는 사실을 근거에서 고르게 함. 사실은 sources에서만 씀(ground 지시)
    # - 판정 기록(entry)은 채팅 하네스 로그용 형식이라 여기서는 버림
    def _grounded(self, concept: Concept, sources: dict[str, Passage], render, fallback) -> str:
        question = f"{concept.title} 개념을 설명해 주세요. 학습 목표: {' '.join(concept.objectives)}"
        try:
            drafts = self.ground(question, {k: p.text for k, p in sources.items()}, [concept.title], [])
        except LLMUnavailable:
            return fallback()
        sentences = [_check_sentence(self, text, keys, sources, {}) for text, keys in drafts]
        if sum(s.status == 'needs_check' for s in sentences) * 2 > len(sentences):
            return fallback()
        return render(sentences)

    ## @brief S5·S6 서술 채점; 기존 판정에서 충족이어도 근거 구절이 학생 답에 글자 그대로 없으면 미충족으로 바꿈
    # - 대조는 공백·문장부호를 지운 부분 문자열(grading_evidence_in). 모델이 답에 없는 내용을 근거로 지어내 통과시키는 일을 막음
    def grade_explanation(self, item: Item, answer: str) -> list[bool]:
        return [c['met'] and grading_evidence_in(c['evidence'], answer) for c in self._grade_criteria(item, answer)]

    ## @brief S7 복귀 요약을 모델 없이 코드가 조립함(설계 5-8): 보충한 선수 개념, 통과한 개념, 아직 통과하지 못한 개념 순
    # - 보충 목록의 마지막은 막힌 개념 자신이라 첫 문장의 "보충한 개념"에서는 뺌. 선수 개념 없이 막힌 개념만 보충했으면 그것만 말함
    # - 확인 문항이 없어 확인하지 않은 개념(unchecked)은 통과로도 미통과로도 말하지 않고 따로 알림
    def summarize(self, state: StuckState, concept_map: ConceptMap) -> str:
        def titles(concepts):
            return ', '.join(self._title(c) for c in concepts)

        target = self._title(state.target)
        filled = [f.concept for f in state.fixes if f.concept != state.target]
        parts = [f"{with_particle(target, '을')} 이해하려고 {with_particle(titles(filled), '을')} 보충했어요." if filled
                 else f"{with_particle(target, '을')} 보충했어요."]
        groups = {outcome: [f.concept for f in state.fixes if f.outcome == outcome]
                  for outcome in ('passed', 'unresolved', 'unchecked')}
        if groups['passed']:
            parts.append(f"{with_particle(titles(groups['passed']), '은')} 이해 확인을 통과했어요.")
        if groups['unresolved']:
            parts.append(f"{with_particle(titles(groups['unresolved']), '은')} 아직 이해 확인을 통과하지 못했어요. 다시 살펴보세요.")
        if groups['unchecked']:
            parts.append(f"{with_particle(titles(groups['unchecked']), '은')} 확인 문항이 없어 이해 확인을 하지 않았어요.")
        return ' '.join(parts)
