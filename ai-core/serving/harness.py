## @file
# @brief 근거 검사 하네스의 순수 검증 규칙과 자료형; 모델 출력을 코드가 다시 확인하는 기준을 한곳에 둠
# - 생성 출력의 형식(문장 수·길이·근거 번호·한자 섞임)과 판정·채점 근거 구절의 글자 대조를 맡음
# - 모델 호출은 harness_llm.py가 맡고, 이 모듈의 검증 함수를 parse 단계에서 불러 실패하면 재시도하게 함
# - 단계 흐름(범위 → 검색 → 생성 → 판정 → 수정)은 아직 없음(계획 Task 6에서 추가)
import re
import string
from dataclasses import dataclass
from typing import Literal

# 한 답의 분량 상한: 학습자가 한 번에 읽을 분량이자 문장마다 판정 호출이 붙으므로 응답 시간을 묶는 운영 보호 장치
MAX_SENTENCES = 8
MAX_SENTENCE_CHARS = 200

# CJK 통합 한자 확장 A·기본·호환 범위(U+3400~4DBF, U+4E00~9FFF, U+F900~FAFF); 작은 모델이 한국어 문장에 중국어를 섞는 오류를 거르는 데 씀
# - 글자 대신 \u 이스케이프로 씀: 호환 한자 U+F900을 글자로 쓰면 편집기·도구의 NFC 정규화로 U+8C48이 되어
#   범위가 한글 음절(U+AC00~)까지 넓어지고 모든 한국어 문장이 한자로 걸림
HAN = re.compile('[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]')

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


## @brief 생성 출력 {sentences: [{text, sources}]}를 검사해 (문장, 근거 키 묶음) 목록으로 반환
# - 문장 1~8개, 각 200자 이하, 근거 키가 하나 이상이고 모두 source_keys 안에 있어야 하며 한자가 섞이면 안 됨
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
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_SENTENCE_CHARS:
            raise ValueError(f'문장은 비어 있지 않고 {MAX_SENTENCE_CHARS}자 이하여야 합니다: {text!r}')
        if not isinstance(sources, list) or not sources or not all(s in source_keys for s in sources):
            raise ValueError(f'근거 번호가 없거나 검색되지 않은 근거입니다: {sources!r}')
        if has_han(text):
            raise ValueError(f'한자가 섞인 문장입니다: {text!r}')
        result.append((text.strip(), tuple(sources)))
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
