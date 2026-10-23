# 파일 역할: 진단·확인 문항을 검수된 정답과 비교하는 결정적 채점기; LLM을 쓰지 않아 같은 답은 항상 같은 판정
"""Deterministic grading for multiple-choice and short-answer items."""
from serving.item_bank import Item, normalize_short_answer


# 역할: 학습자 답을 문항 정답과 비교해 맞았는지 반환
# - 선택형: 앞뒤 공백·대소문자만 무시하고 A·B·C·D 라벨 비교
# - 단답형: 정답 키와 같은 정규화 규칙(공백·대소문자·전각 쉼표 무시)을 적용해 비교
# - 서술형은 루브릭 채점 대상이라 여기서 판정하지 않음
def grade(item: Item, answer: str) -> bool:
    if item.format == 'multiple_choice':
        return str(answer).strip().upper() == item.answer
    if item.format == 'short_exact':
        return normalize_short_answer(answer) == item.answer
    raise ValueError(f'{item.id}: 서술 문항은 결정적 채점 대상이 아닙니다.')
