/**
 * @file
 * @brief AI core가 계산한 단계별 애니메이션 프레임의 화면용 타입과 재생기의 순수 계산(화면 크기·단계 범위·보충 화면 분기)
 * - 프레임 상태는 AI core 코드가 결정적으로 만들고, 화면은 그대로 그리기만 함(모델이 상태를 지어내지 않음)
 * - UI 없이 node 테스트로 검증할 수 있게 React 코드와 분리
 */

/**
 * @brief 상자 하나; 원소 id를 주로 쓰되 BFS 큐처럼 자리 기준 id도 있어 값·모양을 함께 확인
 * - x 0..11, y 0..7은 격자 칸 좌표(픽셀 아님)
 * - shape: cell(배열 칸·스택 원소), node(연결 리스트·트리 노드), slot(큐 자리), label(top·front 같은 표시 글자)
 * - tone: 이번 단계에서 강조(active)·새로 생김(new)·빠짐(removed)
 */
export type Box = {
  id: string;
  label: string;
  x: number;
  y: number;
  shape: "cell" | "node" | "slot" | "label";
  tone: "normal" | "active" | "new" | "removed";
};

/**
 * @brief 두 상자를 잇는 화살표(연결 리스트의 next, 포인터 등); 상자 id로 양 끝을 가리킴
 * - head가 false면 화살촉 없는 선(무방향 그래프 간선); 필드가 없던 기존 장면은 화살촉을 그림
 */
export type Arrow = {
  source: string;
  target: string;
  label?: string | null;
  head?: boolean;
};

/** @brief 한 단계의 화면과 한 줄 설명 */
export type Frame = { boxes: Box[]; arrows: Arrow[]; caption: string };

/** @brief AI core Animation dump; 막힘 보충 view.animation으로 받음 */
export type AnimationData = { kind: string; title: string; frames: Frame[] };

// 격자 한 칸의 SVG 크기; 상자는 칸 안에 여백을 두고 그려 화살표가 지나갈 틈을 남김
export const CELL_WIDTH = 64;
export const CELL_HEIGHT = 56;

/**
 * @brief 모든 프레임에서 가장 큰 x·y를 덮는 고정 SVG 크기 계산
 * - 단계마다 크기를 다시 재면 축소 비율이 바뀌어 화면이 출렁이므로 전체 프레임 기준 하나로 고정
 * - 상자가 없는 장면도 칸 하나 크기를 둬 0 크기 SVG를 막음
 */
export function fitViewBox(frames: Frame[]): { width: number; height: number } {
  let maxX = 0;
  let maxY = 0;
  for (const frame of frames)
    for (const box of frame.boxes) {
      maxX = Math.max(maxX, box.x);
      maxY = Math.max(maxY, box.y);
    }
  return { width: (maxX + 1) * CELL_WIDTH, height: (maxY + 1) * CELL_HEIGHT };
}

/**
 * @brief 라벨이 상자 폭(글자 26 viewBox 단위 기준)을 넘어 글자 폭을 줄여야 하는지 판단
 * - cell·slot은 세 자리 숫자(-99..999)까지 들어가고, 더 좁은 node는 두 자리까지 들어감
 * - label(head·front·rear)은 테두리 없이 칸 폭 전체를 쓸 수 있어 줄이지 않음
 */
export function squeezesLabel(box: Box): boolean {
  if (box.shape === "label") return false;
  return box.label.length > (box.shape === "node" ? 2 : 3);
}

/** @brief 이전·다음·키보드 이동 결과를 0..total-1 안으로 제한 */
export function clampStep(step: number, total: number): number {
  return Math.min(Math.max(step, 0), Math.max(total - 1, 0));
}

/**
 * @brief 보충 화면을 설명 텍스트와 선택적 애니메이션으로 나눔
 * - animation 필드가 없던 시절에 저장된 세션이나 장면 생성에 실패한 보충은 null로 두고 텍스트만 보여 줌
 */
export function remediationParts(view: {
  text: string;
  animation?: AnimationData | null;
}): {
  text: string;
  animation: AnimationData | null;
} {
  return { text: view.text, animation: view.animation ?? null };
}


/** @brief 원본 상자와 표시용 key; 프레임의 id·좌표·tone은 수정하지 않고 React의 원소 연결에만 사용 */
export type TrackedBox = { key: string; box: Box };

/** @brief 전환 동안 남길 상자와 등장·유지·퇴장 상태; 퇴장은 시각 효과가 끝난 뒤 제거 */
export type TransitionBox = TrackedBox & { phase: "enter" | "present" | "exit" };

/** @brief 값과 모양이 같은지 확인; 같은 숫자의 트리 노드와 배열 칸을 서로 연결하지 않음 */
const sameValue = (a: Box, b: Box) => a.label === b.label && a.shape === b.shape;

/**
 * @brief 전체 프레임에 표시용 key를 미리 붙여 이전·다음 이동에서 같은 원소를 같은 DOM으로 연결
 * - id·값·모양이 모두 같은 짝을 먼저 예약해, 뒤에 등장한 같은 값의 사본이 원본 key를 가져가지 않게 함
 * - BFS 큐처럼 자리 id가 재사용되면 남은 값·모양이 양쪽에서 유일할 때만 연결
 * - 중복 값의 id까지 달라져 정체성이 모호하면 새 key로 등장시켜 잘못된 이동을 지어내지 않음
 */
export function trackBoxes(frames: readonly Frame[]): TrackedBox[][] {
  let serial = 0;
  let previous: TrackedBox[] = [];
  return frames.map((frame) => {
    const keys = new Map<Box, string>();
    const used = new Set<string>();
    for (const box of frame.boxes) {
      const exact = previous.find((item) => !used.has(item.key) && item.box.id === box.id && sameValue(item.box, box));
      if (exact) {
        keys.set(box, exact.key);
        used.add(exact.key);
      }
    }
    // 두 집합을 고정해, 모호한 중복값 하나를 처리한 뒤 남은 하나를 유일한 짝으로 오인하지 않게 함
    const unmatched = frame.boxes.filter((box) => !keys.has(box));
    const available = previous.filter((item) => !used.has(item.key));
    for (const box of unmatched) {
      const candidates = available.filter((item) => sameValue(item.box, box));
      const unique = unmatched.filter((other) => sameValue(other, box)).length === 1;
      keys.set(box, unique && candidates.length === 1 ? candidates[0].key : `box-${serial++}`);
    }
    previous = frame.boxes.map((box) => ({ key: keys.get(box)!, box }));
    return previous;
  });
}

/**
 * @brief 다음 프레임 상자를 먼저 두고 사라진 상자는 퇴장 상태로 잠시 보존
 * - 퇴장 중 빠르게 되돌아온 key도 다시 유지 상태로 써 중복 원소와 오래된 타이머의 삭제를 방지
 */
export function transitionBoxes(previous: readonly TrackedBox[], next: readonly TrackedBox[]): TransitionBox[] {
  const before = new Set(previous.map((item) => item.key));
  const after = new Set(next.map((item) => item.key));
  return [
    ...next.map((item): TransitionBox => ({ ...item, phase: before.has(item.key) ? "present" : "enter" })),
    ...previous.filter((item) => !after.has(item.key)).map((item): TransitionBox => ({ ...item, phase: "exit" })),
  ];
}
