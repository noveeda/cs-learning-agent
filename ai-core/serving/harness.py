## @file
# @brief 근거 검사 하네스의 순수 검증 규칙과 자료형; 모델 출력을 코드가 다시 확인하는 기준을 한곳에 둠
# - 생성 출력의 형식(문장 수·길이·근거 번호·한자 섞임)과 판정·채점 근거 구절의 글자 대조를 맡음
# - 모델 호출은 harness_llm.py가 맡고, 이 모듈의 검증 함수를 parse 단계에서 불러 실패하면 재시도하게 함
# - 단계 흐름 answer(범위 → 검색 → 생성 → 판정 → 수정 → 표시)도 여기 둠. 다음 단계는 모델이 아니라 이 코드가 정함
#   - 모델 잠금은 잡지 않음: 게이트웨이가 호출마다 잡으므로, 요청 전체를 막는 일은 채팅 연결(계획 Task 7)이 맡음
import re
import string
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Literal

from rag.search import Passage
from rag.sources import BOOKS
from serving.stuck import LLMUnavailable

# 한 답의 분량 상한: 학습자가 한 번에 읽을 분량이자 문장마다 판정 호출이 붙으므로 응답 시간을 묶는 운영 보호 장치
MAX_SENTENCES = 8
MAX_SENTENCE_CHARS = 200

# CJK 통합 한자 확장 A·기본·호환 범위(U+3400~4DBF, U+4E00~9FFF, U+F900~FAFF); 작은 모델이 한국어 문장에 중국어를 섞는 오류를 거르는 데 씀
# - 글자 대신 \u 이스케이프로 씀: 호환 한자 U+F900을 글자로 쓰면 편집기·도구의 NFC 정규화로 U+8C48이 되어
#   범위가 한글 음절(U+AC00~)까지 넓어지고 모든 한국어 문장이 한자로 걸림
HAN = re.compile('[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]')

# 화면 고정 문구(계획 Global Constraints): 모델이 만들지 않으므로 검증 없이 그대로 보여 줌
BLOCKED_TEXT = '이 도구는 자료구조·운영체제·데이터베이스·네트워크·컴퓨터구조의 43개 개념만 다룹니다. 로드맵에서 개념을 골라 학습해 주세요.'
NO_SOURCE_TEXT = '교재에서 이 질문의 근거를 찾지 못했습니다.'
FAILED_TEXT = '설명을 만들지 못했습니다. 아래 교재 원문을 확인해 주세요.'
NEEDS_CHECK_MARK = ' ⚠️ **확인 필요**'

# 문장으로 인정하는 최소 내용: 한글·영문·숫자 5자 이상이고 한글·영문 글자가 하나 이상
# - 실측에서 작은 모델이 ':' 같은 빈 문장을 냄. 이런 문장은 판정할 사실이 없어 화면만 흐리므로 형식 오류로 보고 재시도하게 함
MIN_SENTENCE_LETTERS = 5
_WORD_CHAR = re.compile('[가-힣A-Za-z0-9]')
_LETTER = re.compile('[가-힣A-Za-z]')

# 서술 채점 근거 대조 전에 지우는 문장부호; 영문 기호와 한국어 답에 자주 나오는 가운뎃점·말줄임표·따옴표·낫표
GRADING_PUNCTUATION = set(string.punctuation + '·…“”‘’「」『』')


## @brief 검사를 마친 답의 한 문장
# - sources: 근거 키 목록(문단 'p1'~'p3', 애니메이션 단계 'a1'~'aN')
# - status: 판정을 통과하면 verified, 수정 뒤에도 통과하지 못하면 needs_check(화면에 "확인 필요" 표시)
@dataclass(frozen=True)
class Sentence:
    text: str
    sources: tuple[str, ...]
    status: Literal['verified', 'needs_check']


## @brief 텍스트에 한자가 섞였는지 확인
def has_han(text: str) -> bool:
    return HAN.search(text) is not None


## @brief 모델이 본문에 직접 쓴 근거 키 표기('(p3)', '[p1]', '[^p1]', '(p1, p2)', 단독 'p2')를 지움
# - 근거 키는 sources 필드로 받고 화면 각주([^p1])는 코드가 붙이므로, 본문 표기는 각주와 겹치기만 함
# - 실제 근거 키(source_keys)만 지움. 낱말에 붙은 'p3에'나 근거 키가 아닌 '(p9)'는 본문 내용일 수 있어 남김
# - 지운 자리의 공백을 하나로 줄이고 문장부호 앞 공백을 없앰('꺼낸다 (p1).' → '꺼낸다.')
def _strip_source_keys(text: str, source_keys: set[str]) -> str:
    key = r'\^?(?:' + '|'.join(re.escape(k) for k in sorted(source_keys, key=len, reverse=True)) + ')'
    group = rf'{key}(?:\s*[,，]\s*{key})*'
    marker = re.compile(rf'[(\[（]\s*{group}\s*[)\]）]|(?<!\w){group}(?!\w)')
    return re.sub(r'\s+([.,!?;:])', r'\1', ' '.join(marker.sub(' ', text).split()))


## @brief 생성 출력 {sentences: [{text, sources}]}를 검사해 (문장, 근거 키 묶음) 목록으로 반환
# - 문장 1~8개, 각 200자 이하, 근거 키가 하나 이상이고 모두 source_keys 안에 있어야 하며 한자가 섞이면 안 됨
# - 본문의 근거 키 표기는 지운 뒤 검사하고, 지운 문장을 돌려줌. 지운 뒤 내용이 MIN_SENTENCE_LETTERS 미만이면 거부
# - 어기면 ValueError: 게이트웨이의 _call이 이를 잘못된 출력으로 보고 재시도함
# - 모양이 다른 JSON(목록 대신 객체 등)도 ValueError로 바꿔, 호출한 쪽이 한 가지 예외만 다루게 함
def validate_sentences(raw: dict, source_keys: set[str]) -> list[tuple[str, tuple[str, ...]]]:
    items = raw.get('sentences') if isinstance(raw, dict) else None
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_SENTENCES:
        raise ValueError(f'문장은 1~{MAX_SENTENCES}개여야 합니다.')
    result = []
    for item in items:
        text = item.get('text') if isinstance(item, dict) else None
        sources = item.get('sources') if isinstance(item, dict) else None
        if not isinstance(text, str) or len(text) > MAX_SENTENCE_CHARS:
            raise ValueError(f'문장은 {MAX_SENTENCE_CHARS}자 이하의 글이어야 합니다: {text!r}')
        if not isinstance(sources, list) or not sources or not all(s in source_keys for s in sources):
            raise ValueError(f'근거 번호가 없거나 검색되지 않은 근거입니다: {sources!r}')
        text = _strip_source_keys(text, source_keys)
        if len(_WORD_CHAR.findall(text)) < MIN_SENTENCE_LETTERS or not _LETTER.search(text):
            raise ValueError(f'내용이 없거나 너무 짧은 문장입니다: {text!r}')
        if has_han(text):
            raise ValueError(f'한자가 섞인 문장입니다: {text!r}')
        result.append((text, tuple(sources)))
    return result


## @brief 판정 근거 구절이 문단에 글자 그대로 있는지 확인; 줄바꿈·연속 공백 차이만 허용
# - 문단 원문(PDF·HTML 추출)의 줄바꿈과 모델이 옮긴 구절의 공백이 달라 생기는 거짓 실패를 막기 위해 공백을 한 칸으로 정규화
# - 빈 구절은 근거가 아니므로 거짓
def evidence_in(evidence: str, text: str) -> bool:
    needle = ' '.join(evidence.split())
    return bool(needle) and needle in ' '.join(text.split())


## @brief 서술 채점 근거가 학생 답에 있는지 확인; 공백과 문장부호를 모두 지운 뒤 부분 문자열로 대조
# - 띄어쓰기·마침표 차이로 맞는 답을 떨어뜨리지 않기 위함. 오타 허용 같은 느슨한 일치는 하지 않음
# - 지운 뒤 비면(빈 근거) 거짓
def grading_evidence_in(evidence: str, answer: str) -> bool:
    def squeeze(text):
        return ''.join(ch for ch in text if not ch.isspace() and ch not in GRADING_PUNCTUATION)

    needle = squeeze(evidence)
    return bool(needle) and needle in squeeze(answer)


## @brief 다음 턴의 후속 질문이 이어받는 근거 기록; artifact 이벤트 {'kind': 'grounding'}로 Spring 이력에 저장됨
# - concepts: 이번 답의 개념 ID, passage_ids: 근거 문단 id(p1부터 순서대로), verified: 판정을 통과해 화면에 표시 없이 나간 문장
@dataclass(frozen=True)
class Grounding:
    concepts: tuple[str, ...]
    passage_ids: tuple[str, ...]
    verified: tuple[str, ...]


## @brief 문장 하나를 화면 마크다운으로: 본문 + 근거 각주([^p1]) + 확인 필요 표시
def _sentence_markdown(sentence: Sentence) -> str:
    notes = ''.join(f'[^{key}]' for key in sentence.sources)
    return sentence.text + notes + (NEEDS_CHECK_MARK if sentence.status == 'needs_check' else '')


## @brief 문단 하나의 출처 문구: 교재명, 저자, 절 번호와 제목, 라이선스(링크), 발췌 (spec 5-6)
def _citation(passage, books) -> str:
    book = books[passage.book]
    section = f"{passage.section_id.rsplit('#', 1)[-1]} {passage.title}"
    return f'{book.title}, {book.authors}, {section}, [{book.license}]({book.license_url}), 발췌'


## @brief 문단 원문을 인용 블록 줄로 바꿈; 문단 사이 빈 줄은 빈 인용 줄로, 문단 안 줄바꿈·연속 공백은 한 칸으로
def _quote(text: str, indent: str = '') -> str:
    paragraphs = [' '.join(p.split()) for p in text.split('\n\n') if p.strip()]
    return f'\n{indent}>\n{indent}'.join(f'> {p}' for p in paragraphs)


## @brief 출처 목록 블록: 같은 절의 문단은 한 줄로 합침
def _sources_block(passages, books) -> str:
    lines = dict.fromkeys(f'- {_citation(p, books)}' for p in passages)
    return '**출처**\n\n' + '\n'.join(lines)


## @brief 검사를 마친 문장 목록을 최종 답 마크다운으로 조립
# - 본문은 text_delta로 보낸 문장을 이은 것과 같음. 그래서 스트림 화면과 저장된 답이 같은 글이 됨
# - 각주 정의는 문장이 실제로 가리킨 문단만 원문 인용 + 출처로 씀. 원문은 번역하지 않음(번역도 검증이 필요한 생성이므로)
def render_answer(sentences: list[Sentence], passages: dict[str, Passage], books) -> str:
    body = ' '.join(_sentence_markdown(s) for s in sentences)
    keys = [k for k in dict.fromkeys(key for s in sentences for key in s.sources) if k in passages]
    notes = '\n\n'.join(f'[^{k}]: {_quote(passages[k].text, "    ")}\n\n    {_citation(passages[k], books)}'
                          for k in keys)
    return f'{body}\n\n{notes}\n\n{_sources_block([passages[k] for k in keys], books)}'


## @brief 생성 실패·확인 필요 과반일 때의 답: 고정 실패 문구 + 검색된 문단 원문 인용 + 출처
# - 모델 문장을 하나도 넣지 않음. 검증되지 않은 설명 대신 교재 원문만 보여 주기 위함
def fallback_answer(passages: dict[str, Passage], books) -> str:
    quotes = '\n\n'.join(_quote(p.text) for p in passages.values())
    return f'{FAILED_TEXT}\n\n{quotes}\n\n{_sources_block(passages.values(), books)}'


## @brief 문장 하나를 그 문장이 가리킨 문단으로 판정; True(통과)·False(틀림)·None(판정 호출 실패)
# - "맞음"이어도 근거 구절이 가리킨 문단 어디에도 글자 그대로 없으면 틀림. 모델이 근거를 지어내는 경우를 막음
# - 판정 실패는 예외 종류와 상관없이 None: 어떤 오류 경로로도 검증 안 된 문장이 통과로 처리되지 않게 함
def _judged(gw, text, keys, passages, judgments):
    try:
        verdict, evidence = gw.judge(text, '\n\n'.join(passages[k].text for k in keys))
    except Exception as exc:
        judgments.append({'error': f'{type(exc).__name__}: {exc}'})
        return None
    found = any(evidence_in(evidence, passages[k].text) for k in keys)
    judgments.append({'verdict': verdict, 'evidence': evidence, 'evidence_found': found})
    return verdict == 'supported' and found


## @brief 판정 → (틀리면) 수정 1회 → 재판정으로 문장 하나의 최종 상태를 정하고 기록(entry)을 채움
# - 판정 호출 자체가 실패하면 수정하지 않고 needs_check. 고칠 근거(판정)가 없기 때문
# - 수정은 첫 근거 문단 하나로 함. 수정 문장도 그 문단 하나로 다시 판정하므로 근거 키도 그 하나로 바뀜
def _check_sentence(gw, text, keys, passages, entry) -> Sentence:
    judgments = entry['judgments'] = []
    first = _judged(gw, text, keys, passages, judgments)
    if first is not None and not first:
        try:
            text, keys = gw.repair(text, passages[keys[0]].text, keys[0])
            entry['repairs'] = 1
            first = _judged(gw, text, keys, passages, judgments)
        except Exception as exc:
            entry['repair_error'] = f'{type(exc).__name__}: {exc}'
    entry.update(text=text, sources=list(keys), status='verified' if first else 'needs_check')
    return Sentence(text, tuple(keys), entry['status'])


## @brief 답 하나를 끝내는 이벤트: 화면 초기화(text_start) → 본문 → 그림·근거 기록 artifact → answer → done
# - text_start는 이미 보낸 문장이 있을 때 화면을 비우는 신호이기도 함(확인 필요 과반으로 원문 대체할 때)
def _finish(text, artifacts=()):
    yield {'type': 'text_start'}
    yield {'type': 'text_delta', 'text': text}
    yield from artifacts
    yield {'type': 'answer', 'text': text}
    yield {'type': 'done'}


def _grounding_event(grounding: Grounding) -> dict:
    return {'type': 'artifact', 'kind': 'grounding', 'artifact_id': uuid.uuid4().hex,
            'grounding': {'concepts': list(grounding.concepts), 'passage_ids': list(grounding.passage_ids),
                          'verified': list(grounding.verified)}}


## @brief 교재 그림 artifact; path는 서버 안 PNG 경로라 채팅 연결(Task 7)이 공개 위치로 복사한 뒤 웹 이벤트에서 뺌
def _figure_event(figure, books) -> dict:
    book = books[figure.book]
    section = figure.section_id.rsplit('#', 1)[-1]
    return {'type': 'artifact', 'kind': 'figure', 'artifact_id': uuid.uuid4().hex, 'path': str(figure.path),
            'figure': {'title': f'{book.title} · {section}', 'caption': figure.caption,
                       'source': f'{book.title}, {book.authors}, {section}, {book.license}, 발췌'}}


## @brief 질문 하나를 하네스 다섯 단계로 처리해 채팅 NDJSON 이벤트를 차례로 내보냄
# - previous: 직전 답의 근거 기록. 범위 결정이 후속 질문이라고 보면 그 개념·문단·검증 문장을 이어 씀
#   - 기록이 없으면(스위치를 켜기 전 대화) 후속 판정이어도 이번 범위 결정 결과로 검색함
# - 문장 순서를 지킴: 앞 문장의 판정·수정이 끝나야 다음 문장을 판정하고 text_delta로 보냄
# - 확인 필요 문장이 전체의 절반을 넘는 순간 남은 판정을 멈추고 원문 대체 답으로 바꿈(모델 호출 절약)
# - 범위 결정·검색의 예외는 그대로 올려 보냄(채팅 연결이 error 이벤트로 바꿈). 어떤 경우에도 기록은 남김
def answer(question: str, previous: Grounding | None, gw, retriever, concept_map, log) -> Iterator[dict]:
    # outcome은 끝까지 가지 못하면(클라이언트가 연결을 끊는 등) incomplete로 남음
    record = {'time': datetime.now().isoformat(timespec='seconds'), 'question': question, 'outcome': 'incomplete',
              'timings': {}, 'sentences': []}
    timings = record['timings']

    def timed(stage, call, *args):
        start = time.monotonic()
        try:
            return call(*args)
        finally:
            timings[stage] = round(timings.get(stage, 0.0) + time.monotonic() - start, 3)

    try:
        yield {'type': 'status', 'stage': 'scope'}
        concepts, follow_up = timed('scope', gw.scope, question, list(previous.concepts) if previous else [], concept_map)
        reuse = bool(follow_up and previous and previous.concepts)
        if reuse:
            concepts = list(previous.concepts)
        record.update(concepts=concepts, follow_up=follow_up, reused=reuse)
        if not concepts:
            record['outcome'] = 'blocked'
            yield from _finish(BLOCKED_TEXT)
            return

        yield {'type': 'status', 'stage': 'search'}

        def search():
            found = retriever.by_ids(list(previous.passage_ids)) if reuse else []
            found = found or retriever.passages(concepts, question)
            return found, retriever.figure(concepts, question, compatible_with=found) if found else None

        found, figure = timed('search', search)
        passages = {f'p{n}': p for n, p in enumerate(found, 1)}
        record['passages'] = [{'key': k, 'id': p.id, 'score': round(p.score, 4)} for k, p in passages.items()]
        record['figure'] = figure.id if figure else None
        figure_events = [_figure_event(figure, BOOKS)] if figure else []
        concept_ids, passage_ids = tuple(concepts), tuple(p.id for p in found)
        if not passages:
            record['outcome'] = 'no_source'
            yield from _finish(NO_SOURCE_TEXT, [_grounding_event(Grounding(concept_ids, (), ()))])
            return

        fallback = [*figure_events, _grounding_event(Grounding(concept_ids, passage_ids, ()))]
        yield {'type': 'status', 'stage': 'write'}
        titles = {c.id: c.title for c in concept_map.concepts}
        try:
            drafts = timed('write', gw.ground, question, {k: p.text for k, p in passages.items()},
                           [titles.get(c, c) for c in concepts], list(previous.verified) if reuse else [])
        except LLMUnavailable as exc:
            record.update(outcome='generation_failed', error=str(exc))
            yield from _finish(fallback_answer(passages, BOOKS), fallback)
            return

        sentences, needs_check = [], 0
        for index, (text, keys) in enumerate(drafts, 1):
            yield {'type': 'status', 'stage': 'check', 'index': index, 'total': len(drafts)}
            entry = {'draft': text, 'repairs': 0}
            record['sentences'].append(entry)
            sentence = timed('check', _check_sentence, gw, text, keys, passages, entry)
            needs_check += sentence.status == 'needs_check'
            if needs_check * 2 > len(drafts):
                record['outcome'] = 'fallback'
                yield from _finish(fallback_answer(passages, BOOKS), fallback)
                return
            if not sentences:
                yield {'type': 'text_start'}
            yield {'type': 'text_delta', 'text': (' ' if sentences else '') + _sentence_markdown(sentence)}
            sentences.append(sentence)

        record['outcome'] = 'answered'
        verified = tuple(s.text for s in sentences if s.status == 'verified')
        yield from figure_events
        yield _grounding_event(Grounding(concept_ids, passage_ids, verified))
        yield {'type': 'answer', 'text': render_answer(sentences, passages, BOOKS)}
        yield {'type': 'done'}
    except Exception as exc:
        record.update(outcome='error', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        log.write(record)
