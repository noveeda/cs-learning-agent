## @file
# @brief 진단의 기준인 개념 지도(개념·직접 선수 관계)를 불러오고 구조 규칙을 검증
# - 과목마다 content/<과목>/concept-map.json을 두고, 로더가 하나의 연결 지도로 합쳐 과목 사이 선수 관계를 허용
"""Concept map schema and loader; the reviewed map is the ground truth for prerequisite diagnosis."""
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTENT_DIR = Path(__file__).resolve().parents[1] / 'content'
# 과목 폴더 이름과 합칠 때의 순서; 존재하는 폴더만 읽어 아직 내용이 없는 과목은 건너뜀
SUBJECT_ORDER = ('dsa', 'os', 'db', 'network')


## @brief 한 개념이 직접 의존하는 선수 개념과 그 근거; 근거는 검수자가 관계의 타당성을 판단하는 자료
class Prerequisite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    reason: str = Field(min_length=1, max_length=200)


## @brief 지도의 개념 하나; 학습 목표는 이후 진단·확인 문항을 붙이는 기준
class Concept(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-z][a-z0-9-]{1,40}$')
    title: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=200)
    # 1~3개로 제한: 개념마다 확인 문항을 붙일 수 있는 분량을 유지
    objectives: list[str] = Field(min_length=1, max_length=3)
    prerequisites: list[Prerequisite] = Field(default_factory=list)
    # 과목 ID; 지도 파일에는 쓰지 않고 로더가 폴더 이름으로 채움(화면의 과목 구분용)
    subject: str | None = None

    # 학습 목표를 "~할 수 있다."로 강제: 설명·구분·적용처럼 확인 가능한 행동으로 쓰게 하기 위함
    @model_validator(mode='after')
    def measurable_objectives(self):
        for objective in self.objectives:
            if not objective.endswith('수 있다.'):
                raise ValueError(f'{self.id}: 학습 목표는 "~할 수 있다."로 끝나야 합니다: {objective}')
        ids = [p.id for p in self.prerequisites]
        if len(set(ids)) != len(ids):
            raise ValueError(f'{self.id}: 같은 선수 개념이 중복됩니다.')
        if self.id in ids:
            raise ValueError(f'{self.id}: 자기 자신을 선수 개념으로 둘 수 없습니다.')
        return self


## @brief 합친 지도에 들어 있는 과목 하나; 화면이 과목 이름을 표시할 때 사용
class Subject(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    title: str


## @brief 과목 지도 파일 하나의 형태; 그래프 규칙은 과목 사이 관계 때문에 합친 지도에서만 검증
class _SubjectFile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    title: str
    version: int = Field(ge=1)
    status: Literal['draft', 'reviewed']
    concepts: list[Concept] = Field(min_length=1)


## @brief 개념 지도(과목 하나 또는 여러 과목을 합친 것); status는 사람이 검수를 마쳤는지 표시
class ConceptMap(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    title: str
    version: int = Field(ge=1)
    status: Literal['draft', 'reviewed']
    concepts: list[Concept] = Field(min_length=1)
    subjects: list[Subject] = Field(default_factory=list)

    ## @brief 진단 탐색이 의존하는 그래프 규칙을 한 번에 검증
    @model_validator(mode='after')
    def validate_graph(self):
        ids = [c.id for c in self.concepts]
        if len(set(ids)) != len(ids):
            raise ValueError('개념 ID가 중복됩니다.')
        known = set(ids)
        for concept in self.concepts:
            for p in concept.prerequisites:
                if p.id not in known:
                    raise ValueError(f'{concept.id}: 없는 선수 개념 {p.id}를 가리킵니다.')
        direct = {c.id: [p.id for p in c.prerequisites] for c in self.concepts}
        _reject_cycles(direct)
        # 고립 개념 금지: 어떤 관계에도 없는 개념은 진단에서 찾을 수 없고 지도 범위 밖일 가능성이 큼
        if len(ids) > 1:
            linked = {i for i, ps in direct.items() if ps} | {p for ps in direct.values() for p in ps}
            isolated = sorted(known - linked)
            if isolated:
                raise ValueError(f'선수 관계가 전혀 없는 개념이 있습니다: {isolated}')
        _reject_redundant_edges(direct)
        return self


## @brief 선수 관계에 순환이 있으면 거부; 순환이 있으면 "아래부터 보충" 순서를 정할 수 없음
def _reject_cycles(direct):
    state = {}

    def visit(node, path):
        if state.get(node) == 'done':
            return
        if state.get(node) == 'active':
            raise ValueError(f'선수 관계에 순환이 있습니다: {" → ".join(path + [node])}')
        state[node] = 'active'
        for prerequisite in direct[node]:
            visit(prerequisite, path + [node])
        state[node] = 'done'

    for node in direct:
        visit(node, [])


## @brief 다른 경로로 이미 이어지는 간접 관계를 직접 관계로 두는 것을 거부
# - 진단은 "선수 개념을 알면 그 아래 개념도 안다"고 보고 내려가지 않음(가지치기)
# - A→B→C가 있는데 A→C도 직접 두면 B를 통과해도 C를 따로 묻게 되어 가지치기가 일관되지 않고 문항이 낭비됨
def _reject_redundant_edges(direct):
    def reachable(start, skip_edge):
        seen, stack = set(), [p for p in direct[start] if p != skip_edge]
        while stack:
            node = stack.pop()
            if node not in seen:
                seen.add(node)
                stack.extend(direct[node])
        return seen

    for concept, prerequisites in direct.items():
        for prerequisite in prerequisites:
            if prerequisite in reachable(concept, prerequisite):
                raise ValueError(f'{concept} → {prerequisite}는 다른 선수 개념을 거쳐 이미 이어지는 간접 관계입니다.')


## @brief 존재하는 과목 폴더를 SUBJECT_ORDER 순서로 반환; 개념 지도와 문항 은행 로더가 같은 과목 목록을 쓰게 함
def subject_dirs() -> list[Path]:
    return [CONTENT_DIR / subject for subject in SUBJECT_ORDER if (CONTENT_DIR / subject).is_dir()]


## @brief 과목은 폴더 이름 하나에서만 정해지도록, 파일에 subject가 적힌 개념을 거부
def _reject_written_subject(concepts, path):
    written = [c.id for c in concepts if c.subject is not None]
    if written:
        raise ValueError(f'{path}: 개념 지도 파일에는 subject를 쓰지 않습니다(로더가 채움): {written}')


## @brief 검증된 개념 지도를 반환; 규칙을 어기면 ValueError(pydantic ValidationError 포함)
# - path가 있으면: 지도 파일 하나만 읽음(기존 단일 지도 테스트 호환)
# - path가 없으면: 모든 과목 지도를 읽어 개념마다 subject를 채우고 하나의 지도로 합침
#   - 고립·간접 관계·없는 선수 개념 규칙은 합친 지도에 적용해, 과목 사이 선수 관계로 이어지는 개념을 허용
#   - version은 과목 version의 합이라 어느 과목이 바뀌어도 값이 달라짐
#   - status는 모든 과목이 reviewed일 때만 reviewed(막힘 엔진이 검수 전 지도를 쓰지 않게 함)
def load_concept_map(path: Path | None = None) -> ConceptMap:
    if path is not None:
        concept_map = ConceptMap.model_validate_json(path.read_text(encoding='utf-8'))
        _reject_written_subject(concept_map.concepts, path)
        return concept_map
    files, concepts = [], []
    for folder in subject_dirs():
        path = folder / 'concept-map.json'
        subject = _SubjectFile.model_validate_json(path.read_text(encoding='utf-8'))
        _reject_written_subject(subject.concepts, path)
        if subject.id != folder.name:
            raise ValueError(f'{path}: 지도 id {subject.id}가 과목 폴더 이름 {folder.name}과 다릅니다.')
        files.append(subject)
        concepts += [c.model_copy(update={'subject': folder.name}) for c in subject.concepts]
    return ConceptMap(
        id='cs', title='CS 전공', version=sum(f.version for f in files),
        status='reviewed' if all(f.status == 'reviewed' for f in files) else 'draft',
        concepts=concepts, subjects=[Subject(id=f.id, title=f.title) for f in files])
