## @file
# @brief 평가 도구들이 함께 쓰는 지표 계산(채점 일치도 kappa, 비율의 Wilson 신뢰 구간, 점수형 가중 kappa, 빈틈 recall·precision)
# - M1·M2(시뮬레이션 학습자), Mohler 개발 검증, 한국어 채점 일치도, 품질 하한이 같은 함수로 같은 정의의 수치를 내게 함
# - 표준 라이브러리만 씀(새 의존성 금지)
# - 경계 입력 규칙
#   - 빈 입력·길이가 다른 입력: ValueError. 숫자를 지어내지 않고, 호출하는 쪽이 뺀 답과 남은 개수를 함께 보고하게 함
#   - 두 채점자가 모두 한 범주만 쓴 경우(우연 일치 1 → 0/0): 두 판정이 모두 같을 수밖에 없으므로 1.0
#   - 표본 0개의 Wilson 구간: 정보가 없으므로 (0.0, 1.0)
"""Shared agreement and proportion metrics for the evaluation tools."""
import math


## @brief 두 채점자의 통과·미통과 판정 사이 Cohen's kappa(우연 일치를 뺀 일치도)를 반환
# - kappa = (관측 일치율 - 우연 일치율) / (1 - 우연 일치율). 1은 완전 일치, 0은 우연 수준, 음수는 우연보다 나쁨
# - 우연 일치율은 각 채점자의 통과 비율이 서로 독립이라고 볼 때 같은 판정이 나올 확률
def cohen_kappa(a: list[bool], b: list[bool]) -> float:
    _check_pairs(a, b)
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    expected = pa * pb + (1 - pa) * (1 - pb)
    if expected == 1:
        # 두 채점자가 같은 한 범주만 썼으면 모든 판정이 같음. 0/0 대신 완전 일치로 정함
        return 1.0
    return (observed - expected) / (1 - expected)


## @brief n번 중 k번 일어난 비율의 Wilson 점수 신뢰 구간 (하한, 상한)을 반환(z=1.96이면 95%)
# - 표본이 작거나 비율이 0·1에 가까워도 구간이 [0, 1] 밖으로 나가지 않아, 거짓 통과율처럼 작은 비율에 씀
# - 예: 50개 중 1개(2%)면 상한 약 10.5% → "거짓 통과율 10% 이하"를 이 표본으로는 보장하지 못함을 보여 줌
def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n < 0 or not 0 <= k <= n:
        raise ValueError(f'0 <= k <= n이어야 함: k={k}, n={n}')
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


## @brief 점수형 채점(예: Mohler 0~5점) 두 벌 사이의 이차 가중 kappa를 반환
# - 점수 차가 클수록 불일치를 (차이/최대 차이)^2 만큼 무겁게 셈. 한 칸 차이는 가볍게, 끝과 끝은 가장 무겁게
# - labels: 가능한 점수 전체(오름차순). 실제로 안 나온 점수도 넣어야 최대 차이가 바르게 잡힘
def quadratic_weighted_kappa(a: list[int], b: list[int], labels: list[int]) -> float:
    _check_pairs(a, b)
    index = {label: i for i, label in enumerate(sorted(labels))}
    unknown = sorted({x for x in a + b if x not in index})
    if unknown:
        raise ValueError(f'labels에 없는 점수: {unknown}')
    k, n = len(index), len(a)
    if k < 2:
        # 가능한 점수가 하나뿐이면 모든 판정이 같음
        return 1.0
    hist_a, hist_b = [0] * k, [0] * k
    observed = 0.0
    for x, y in zip(a, b):
        i, j = index[x], index[y]
        hist_a[i] += 1
        hist_b[j] += 1
        observed += ((i - j) / (k - 1)) ** 2
    observed /= n
    # 기대 가중 불일치: 두 채점자의 점수 분포가 독립일 때의 평균 가중 불일치
    expected = sum(hist_a[i] * hist_b[j] * ((i - j) / (k - 1)) ** 2 for i in range(k) for j in range(k)) / (n * n)
    if expected == 0:
        # 두 채점자가 같은 한 점수만 썼으면 관측 불일치도 0. cohen_kappa와 같은 규칙으로 완전 일치
        return 1.0
    return 1 - observed / expected


## @brief 시스템이 찾은 집합(predicted)과 정답 집합(truth)의 (recall, precision)을 반환
# - recall: 정답 중 찾은 비율. 찾을 것이 없으면(truth 빈 집합) 놓친 것이 없으므로 1.0
# - precision: 찾은 것 중 정답 비율. 찾은 것이 없으면(predicted 빈 집합) 틀리게 말한 것이 없으므로 1.0
# - M1에서 빈틈 0개 프로필에 엔진이 빈틈을 말하지 않으면 둘 다 1.0, 하나라도 말하면 precision 0.0
def recall_precision(predicted: set, truth: set) -> tuple[float, float]:
    hit = len(predicted & truth)
    recall = hit / len(truth) if truth else 1.0
    precision = hit / len(predicted) if predicted else 1.0
    return recall, precision


## @brief 두 채점 목록이 비어 있지 않고 길이가 같은지 확인(같은 답을 같은 순서로 채점한 쌍이어야 함)
def _check_pairs(a, b):
    if len(a) != len(b):
        raise ValueError(f'두 채점 목록의 길이가 다름: {len(a)} != {len(b)}')
    if not a:
        raise ValueError('빈 입력으로는 일치도를 계산할 수 없음')
