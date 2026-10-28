## @file
# @brief 정렬(삽입·병합·퀵) 장면 요청을 실제로 실행하며 단계별 프레임을 만드는 시뮬레이터
# - 배열은 y=0 한 줄의 cell로 그림. 원소마다 처음 자리로 id를 붙여, 같은 값이 있어도 상자가 이동으로 이어지게 함
# - 교환·비교 하나마다 프레임을 만들면 원소 8개에서 MAX_FRAMES(16)를 넘으므로, 알고리즘의 큰 단계마다 프레임 하나로 묶음
#   - 삽입 정렬: 원소 하나를 앞쪽 정렬된 부분의 제자리에 넣을 때마다
#   - 병합 정렬: 두 구간을 하나로 합칠 때마다(하향식 재귀)
#   - 퀵 정렬: Lomuto 분할(피벗은 구간의 마지막 원소) 한 번마다
"""Insertion, merge and quick sort simulators."""
from .graph import fit
from .model import Animation, Box, Frame, SortRequest, build_animation, josa


## @brief 배열 한 줄 프레임. tones는 이번 단계에서 강조할 원소 id → tone
def sort_frame(items, caption, tones=None) -> Frame:
    tones = tones or {}
    boxes = [Box(id=i, label=str(v), x=x, y=0, shape='cell', tone=tones.get(i, 'normal')) for x, (i, v) in enumerate(items)]
    return Frame(boxes=boxes, arrows=[], caption=caption)


## @brief 값 목록을 캡션용 문자열로
def show(items) -> str:
    return '[' + ', '.join(str(v) for _, v in items) + ']'


## @brief 삽입 정렬. key보다 큰 앞 원소를 한 칸씩 밀고 빈자리에 key를 넣음(같은 값은 밀지 않아 안정 정렬)
def insertion(items, frames):
    for i in range(1, len(items)):
        key = items[i]
        j = i
        while j and items[j - 1][1] > key[1]:
            items[j] = items[j - 1]
            j -= 1
        items[j] = key
        shifted = [element for element, _ in items[j + 1:i + 1]]
        what = f'{key[1]}{josa(key[1], "을", "를")}'
        caption = (f'{what} 꺼내 더 큰 앞 원소 {len(shifted)}개를 한 칸씩 밀고 [{j}]에 넣습니다' if shifted
                   else f'{key[1]}: 앞 원소보다 작지 않아 제자리 [{i}]에 둡니다')
        frames.append(sort_frame(items, caption, {key[0]: 'new', **{e: 'active' for e in shifted}}))


## @brief 병합 정렬(하향식). [lo, hi) 구간을 반으로 나눠 각각 정렬한 뒤, 앞에서부터 작은 값을 골라 합침
# - 같은 값이면 왼쪽을 먼저 골라 안정 정렬
def merge(items, frames, lo, hi):
    if hi - lo < 2:
        return
    mid = (lo + hi) // 2
    merge(items, frames, lo, mid)
    merge(items, frames, mid, hi)
    left, right = items[lo:mid], items[mid:hi]
    merged, a, b = [], 0, 0
    while a < len(left) or b < len(right):
        if b == len(right) or (a < len(left) and left[a][1] <= right[b][1]):
            merged.append(left[a])
            a += 1
        else:
            merged.append(right[b])
            b += 1
    items[lo:hi] = merged
    caption = f'병합 [{lo}..{hi - 1}]: {show(left)} + {show(right)} → {show(merged)}'
    short = f'병합 [{lo}..{hi - 1}]: 왼쪽 {len(left)}개와 오른쪽 {len(right)}개를 작은 값부터 합칩니다'
    frames.append(sort_frame(items, fit(caption, short), {e: 'active' for e, _ in merged}))


## @brief 퀵 정렬. [lo, hi] 구간을 Lomuto 방식으로 분할(피벗 = items[hi], 피벗보다 작은 값을 앞으로 모음)한 뒤 양쪽을 재귀 정렬
# - 프레임은 분할이 끝난 상태. 피벗만 active로 표시해 피벗이 최종 자리에 놓인 것을 보여 줌
def quick(items, frames, lo, hi):
    if hi - lo < 1:
        return
    pivot = items[hi]
    i = lo
    for j in range(lo, hi):
        if items[j][1] < pivot[1]:
            items[i], items[j] = items[j], items[i]
            i += 1
    items[i], items[hi] = items[hi], items[i]
    smaller = i - lo
    caption = f'피벗 {pivot[1]}(구간 [{lo}..{hi}]의 마지막): 더 작은 값 {smaller}개를 앞으로 모으고 피벗을 [{i}]에 놓습니다'
    frames.append(sort_frame(items, fit(caption, f'피벗 {pivot[1]}: 더 작은 값 {smaller}개를 앞으로, 피벗은 [{i}]'), {pivot[0]: 'active'}))
    quick(items, frames, lo, i - 1)
    quick(items, frames, i + 1, hi)


ALGORITHMS = {'insertion': '삽입 정렬', 'merge': '병합 정렬', 'quick': '퀵 정렬(Lomuto 분할)'}


## @brief 정렬 장면 실행. 첫 프레임은 정렬 전, 마지막 프레임은 정렬 결과
def simulate_sort(req: SortRequest) -> Animation:
    items = [(f'e{i}', v) for i, v in enumerate(req.values)]
    frames = [sort_frame(items, f'정렬 전 배열 {show(items)}')]
    if req.algorithm == 'insertion':
        insertion(items, frames)
    elif req.algorithm == 'merge':
        merge(items, frames, 0, len(items))
    else:
        quick(items, frames, 0, len(items) - 1)
    frames.append(sort_frame(items, f'정렬 끝: {show(items)}'))
    return build_animation('sort', ALGORITHMS[req.algorithm], frames)
