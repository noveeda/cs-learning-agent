# 파일 역할: 시뮬레이션 학습자의 지식 상태(프로필)를 개념 지도에서 결정적으로 만들고, 정답 빈틈·recall 상한을 계산
# - 프로필 = "막힌 개념(target) + 아는 개념 집합(known)". 엔진이 찾아야 할 정답 빈틈은 여기서 정해짐
# - 기본 프로필은 엔진 가정(선수 개념을 알면 그 아래도 앎)을 지키고, 어긋난 프로필은 consistent=False로 따로 묶음(Review Focus 1)
# - 고정 프로필 파일은 만들지 않음: 생성 규칙과 seed만으로 같은 프로필을 다시 만들 수 있게 함
"""Knowledge-state profiles for the simulated learner."""
import random
from dataclasses import dataclass

from serving.concept_map import ConceptMap
from serving.stuck import MAX_DEPTH, MAX_DIAGNOSIS_ITEMS


# 역할: 시뮬레이션 학습자 한 명의 지식 상태
# - known: 아는 개념 ID 전체. target은 늘 모름(막힌 개념이므로)
# - slip: 아는 개념 문항을 실수로 틀릴 확률, guess: 모르는 개념의 선택형을 찍어서 맞힐 확률
# - consistent: known이 선수 관계에 대해 닫혀 있는지(엔진 가정을 지키는지)
@dataclass(frozen=True)
class Profile:
    id: str
    target: str
    known: frozenset[str]
    slip: float
    guess: float
    consistent: bool


# 역할: 개념의 선수 개념들을 너비 우선으로 따라가 (개념 → 가장 짧은 깊이) 반환. max_depth=None이면 끝까지
# - 엔진 진단도 너비 우선으로 내려가므로, 여러 경로로 닿는 개념은 가장 짧은 깊이로 셈
def prerequisite_depths(concept_map: ConceptMap, concept_id: str, max_depth: int | None = MAX_DEPTH) -> dict[str, int]:
    prerequisites = _prerequisites(concept_map)
    depths, frontier = {}, [(p, 1) for p in prerequisites[concept_id]]
    while frontier:
        concept, depth = frontier.pop(0)
        if concept in depths or concept == concept_id:
            continue
        depths[concept] = depth
        if max_depth is None or depth < max_depth:
            frontier += [(p, depth + 1) for p in prerequisites[concept]]
    return depths


# 역할: 진단 대상(막힌 개념)으로 쓸 개념을 결정적으로 고름
# - 조건: 깊이 3 안에 선수 개념이 있는 개념(최대 진단 깊이까지 내려갈 거리가 있어야 깊은 빈틈을 시험할 수 있음)
# - 순서: 깊이 3 안 선수 개념이 많은 개념부터(빈틈 조합이 다양하고, 문항 상한 5개를 넘는 빈틈도 만들 수 있음), 같으면 ID 순
def select_targets(concept_map: ConceptMap, count: int = 10) -> list[str]:
    candidates = []
    for concept in concept_map.concepts:
        depths = prerequisite_depths(concept_map, concept.id)
        if MAX_DEPTH in depths.values():
            candidates.append((-len(depths), concept.id))
    return [concept_id for _, concept_id in sorted(candidates)[:count]]


# 역할: 대상마다 기본 프로필 per_target개(빈틈 0개 조합 포함)와 어긋난 프로필 1개를 만들어 반환(기본 묶음 다음에 어긋난 묶음)
# - 빈틈 조합은 "씨앗" 개념을 골라 정함. 씨앗을 모르면 씨앗에 기대는 위쪽 개념도 모두 모름 → known이 선수 관계에 대해 닫힘
# - 조합 순서: 빈틈 0개 → 깊이 1 씨앗 → 깊이 2 씨앗 → 깊이 3 씨앗 → 씨앗 2개(빈틈이 많아 상한 효과를 볼 수 있음)
#   해당 깊이가 없거나 앞 조합과 같아지면 무작위 씨앗(1~2개)으로 채움
# - 어긋난 프로필: 가장 깊은 선수 개념 하나만 모르고 그 위쪽은 앎(엔진이 위를 맞힌 뒤 가지치기해 놓치는 경우)
def generate_profiles(concept_map: ConceptMap, targets: list[str], per_target: int, seed: int,
                      slip: float = 0.05, guess: float = 0.25) -> list[Profile]:
    if not 3 <= per_target <= 5:
        raise ValueError('per_target은 3~5여야 합니다.')
    everything = frozenset(c.id for c in concept_map.concepts)
    consistent, inconsistent = [], []
    for target in targets:
        rng = random.Random(f'{seed}:{target}')
        depths = prerequisite_depths(concept_map, target)
        by_depth = {d: sorted(c for c, k in depths.items() if k == d) for d in range(1, MAX_DEPTH + 1)}
        plans = [[]] + [[rng.choice(by_depth[d])] for d in range(1, MAX_DEPTH + 1) if by_depth[d]]
        plans.append(rng.sample(sorted(depths), 2) if len(depths) >= 2 else [])
        unknowns = []
        for attempt in range(100):
            plan = plans[attempt] if attempt < len(plans) else rng.sample(sorted(depths), rng.randint(1, min(2, len(depths))))
            unknown = _dependents_closure(concept_map, set(plan) | {target})
            if unknown not in unknowns:
                unknowns.append(unknown)
            if len(unknowns) == per_target:
                break
        for k, unknown in enumerate(unknowns):
            consistent.append(Profile(f'{target}-{k}', target, everything - unknown, slip, guess, True))
        deepest = max(depths, key=lambda c: (depths[c], c))
        unknown = _dependents_closure(concept_map, {target}) | {deepest}
        inconsistent.append(Profile(f'{target}-x', target, everything - unknown, slip, guess, False))
    return consistent + inconsistent


# 역할: 정답 빈틈 = 대상 개념과 깊이 max_depth 안 선수 개념 중 모르는 것
# - 더 깊은 빈틈은 엔진 진단 깊이(3) 밖이라 엔진 평가에 넣지 않음
def true_gaps(profile: Profile, concept_map: ConceptMap, max_depth: int = MAX_DEPTH) -> set[str]:
    candidates = [profile.target, *prerequisite_depths(concept_map, profile.target, max_depth)]
    return {c for c in candidates if c not in profile.known}


# 역할: 진단 문항 상한(5개) 때문에 엔진이 이론상 찾을 수 있는 최대 recall(Review Focus 2)
# - 문항 하나로 개념 하나를 확인하므로 선수 빈틈이 5개를 넘으면 5/빈틈 수가 상한. 빈틈이 없으면 놓칠 것이 없어 1
# - 대상 개념은 진단하지 않고 늘 마지막에 보충하므로 빈틈 수에서 뺌(M1도 선수 빈틈만 비교)
def recall_ceiling(profile: Profile, concept_map: ConceptMap) -> float:
    gaps = true_gaps(profile, concept_map) - {profile.target}
    return min(1.0, MAX_DIAGNOSIS_ITEMS / len(gaps)) if gaps else 1.0


# 역할: 개념 ID → 직접 선수 개념 ID 목록
def _prerequisites(concept_map: ConceptMap) -> dict[str, list[str]]:
    return {c.id: [p.id for p in c.prerequisites] for c in concept_map.concepts}


# 역할: 씨앗 개념들과, 씨앗에 (직간접으로) 기대는 모든 개념의 집합 = 모르는 개념 집합
# - 이 집합 밖(known)은 선수 관계에 대해 닫힘: 아는 개념의 선수 개념이 모르는 집합에 있으면 그 개념도 이 집합에 들어오기 때문
def _dependents_closure(concept_map: ConceptMap, seeds: set[str]) -> frozenset[str]:
    dependents = {c.id: [] for c in concept_map.concepts}
    for concept, prerequisites in _prerequisites(concept_map).items():
        for p in prerequisites:
            dependents[p].append(concept)
    unknown, stack = set(seeds), list(seeds)
    while stack:
        for upper in dependents[stack.pop()]:
            if upper not in unknown:
                unknown.add(upper)
                stack.append(upper)
    return frozenset(unknown)
