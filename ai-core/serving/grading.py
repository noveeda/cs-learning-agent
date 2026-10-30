## @file
# @brief 진단·확인 문항을 검수된 정답과 비교하는 결정적 채점기; LLM을 쓰지 않아 같은 답은 항상 같은 판정
"""Deterministic grading for multiple-choice and short-answer items."""
import re

from serving.item_bank import Item, normalize_short_answer

# 단답 값 사이 구분자로 인정하는 표기: 쉼표(전각 포함)·공백·가운뎃점·빗금·화살표(->, →, >)
# - 정답 키는 쉼표로만 저장되지만 학생은 순서를 공백이나 화살표로 쓰기도 함. 값과 순서가 같으면 같은 답으로 봄
_SEPARATORS = re.compile(r'(?:->|[,，、\s/·→>])+')


## @brief 단답을 구분자 기준으로 나눈 값 목록(소문자); 빈 조각은 버림
def _values(answer: str) -> list[str]:
    return [v for v in _SEPARATORS.split(str(answer).strip().lower()) if v]


## @brief 학습자 답을 문항 정답과 비교해 맞았는지 반환
# - 선택형: 앞뒤 공백·대소문자만 무시하고 A·B·C·D 라벨 비교
# - 단답형: 정답 키와 같은 정규화 규칙(공백·대소문자·전각 쉼표 무시)을 적용해 비교
#   - 값 목록이 같으면 구분자 표기가 달라도 정답(공백·화살표로 쓴 순서)
#   - 정답 값이 모두 한 글자면 구분자 없이 이어 쓴 답('321123')도 정답. 여러 자리 값은 끊는 위치를 알 수 없어 인정하지 않음
# - 서술형은 루브릭 채점 대상이라 여기서 판정하지 않음
def grade(item: Item, answer: str) -> bool:
    if item.format == 'multiple_choice':
        return str(answer).strip().upper() == item.answer
    if item.format == 'short_exact':
        if normalize_short_answer(answer) == item.answer:
            return True
        key, given = item.answer.split(','), _values(answer)
        if given == key:
            return True
        return len(given) == 1 and len(key) > 1 and all(len(v) == 1 for v in key) and list(given[0]) == key
    raise ValueError(f'{item.id}: 서술 문항은 결정적 채점 대상이 아닙니다.')
