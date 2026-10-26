# 파일 역할: 개념별 진단·확인 문항과 서술 루브릭을 불러오고, 개념 지도와 맞물리는지 검증
"""Item bank schema; diagnosis/check items are graded deterministically, explanations by rubric."""
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from serving.concept_map import ConceptMap, load_concept_map, subject_dirs

CHOICE_LABELS = ('A', 'B', 'C', 'D')


# 역할: 단답 정답 비교 전 표기 차이만 없앰; 공백·대소문자·전각 쉼표가 달라도 같은 답으로 취급
def normalize_short_answer(value: str) -> str:
    return ''.join(str(value).split()).lower().replace('，', ',')


# 역할: 문항 하나
# - diagnosis: 막힌 개념의 선수 개념을 짧게 확인해 빈틈을 찾는 문항(S3)
# - check: 보충 뒤 이해를 확인하는 적용 문항(S5·S6), 진단 문항과 다른 상황을 씀
# - explanation: 자기 말 설명 질문과 루브릭(S5·S6), 채점은 LLM이 루브릭 항목별로 판정
class Item(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-z][a-z0-9-]*-[dce][0-9]+$')
    concept: str
    purpose: Literal['diagnosis', 'check', 'explanation']
    # 개념 지도의 학습 목표 순번(0부터); 문항이 어떤 목표를 확인하는지 추적하기 위함
    objective: int = Field(ge=0)
    format: Literal['multiple_choice', 'short_exact', 'explanation']
    question: str = Field(min_length=1, max_length=500)
    options: dict[str, str] | None = None
    answer: str | None = None
    # 정답 근거 또는 모범 설명; 학습자 피드백과 검수 자료로 사용하며 채점에는 쓰지 않음
    rationale: str = Field(min_length=1, max_length=400)
    rubric: list[str] | None = None

    # 역할: 목적·형식·정답 필드의 조합을 검증해 결정적 채점이 가능한 문항만 남김
    @model_validator(mode='after')
    def consistent_shape(self):
        prefix, kind = self.id.rsplit('-', 1)[0], self.id.rsplit('-', 1)[1][0]
        if prefix != self.concept or kind != self.purpose[0]:
            raise ValueError(f'{self.id}: ID는 "<개념>-<d|c|e><번호>" 형식으로 개념·목적과 맞아야 합니다.')
        if (self.purpose == 'explanation') != (self.format == 'explanation'):
            raise ValueError(f'{self.id}: 서술 문항만 explanation 형식을 씁니다.')
        if self.format == 'multiple_choice':
            if not self.options or tuple(self.options) != CHOICE_LABELS:
                raise ValueError(f'{self.id}: 선택지는 A·B·C·D 네 개여야 합니다.')
            if len({v.strip() for v in self.options.values()}) != 4 or not all(v.strip() for v in self.options.values()):
                raise ValueError(f'{self.id}: 선택지는 서로 다르고 비어 있지 않아야 합니다.')
            if self.answer not in CHOICE_LABELS:
                raise ValueError(f'{self.id}: 선택형 정답은 A·B·C·D 중 하나여야 합니다.')
        elif self.format == 'short_exact':
            # 정답 키를 정규화된 형태로 저장해 채점 시 비교 기준이 하나뿐이게 함
            if not self.answer or self.answer != normalize_short_answer(self.answer) or self.options:
                raise ValueError(f'{self.id}: 단답 정답은 공백 없는 정규화 형태여야 하고 선택지가 없어야 합니다.')
        if self.format == 'explanation':
            if self.answer or self.options or not self.rubric or not 2 <= len(self.rubric) <= 4:
                raise ValueError(f'{self.id}: 서술 문항은 정답·선택지 없이 루브릭 2~4개를 가져야 합니다.')
        elif self.rubric:
            raise ValueError(f'{self.id}: 루브릭은 서술 문항에만 둡니다.')
        return self


# 역할: 문항들이 개념 지도와 맞물리는지 검증; 없는 개념·목표를 가리키거나 ID가 겹치면 거부
def validate_against_map(items: list[Item], concept_map: ConceptMap) -> None:
    ids = [item.id for item in items]
    if len(set(ids)) != len(ids):
        raise ValueError('문항 ID가 중복됩니다.')
    concepts = {c.id: c for c in concept_map.concepts}
    for item in items:
        if item.concept not in concepts:
            raise ValueError(f'{item.id}: 개념 지도에 없는 개념 {item.concept}입니다.')
        if item.objective >= len(concepts[item.concept].objectives):
            raise ValueError(f'{item.id}: {item.concept}에 없는 학습 목표 순번 {item.objective}입니다.')


# 역할: 개념 묶음별 JSONL 파일(items-*.jsonl)을 모두 읽어 검증된 문항 목록 반환
# - directory가 없으면 모든 과목 폴더를 읽고, 기본 지도(모든 과목을 합친 지도)와 맞물리는지 검증
def load_item_bank(directory: Path | None = None, concept_map: ConceptMap | None = None) -> list[Item]:
    items = []
    folders = [directory] if directory is not None else subject_dirs()
    for path in (p for folder in folders for p in sorted(folder.glob('items-*.jsonl'))):
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if line.strip():
                try:
                    items.append(Item.model_validate(json.loads(line)))
                except ValueError as exc:
                    raise ValueError(f'{path.name}:{number}: {exc}') from exc
    validate_against_map(items, concept_map or load_concept_map())
    return items
