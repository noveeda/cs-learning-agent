// 파일 역할: AI core가 계산한 단계별 애니메이션 프레임의 화면용 타입과 재생기의 순수 계산(화면 크기·단계 범위·보충 화면 분기)
// - 프레임 상태는 AI core 코드가 결정적으로 만들고, 화면은 그대로 그리기만 함(모델이 상태를 지어내지 않음)
// - UI 없이 node 테스트로 검증할 수 있게 React 코드와 분리

// 역할: 상자 하나; 같은 원소는 프레임이 바뀌어도 id가 같아 재생기가 이 id로 이동을 그림
// - x 0..11, y 0..7은 격자 칸 좌표(픽셀 아님)
// - shape: cell(배열 칸·스택 원소), node(연결 리스트·트리 노드), slot(큐 자리), label(top·front 같은 표시 글자)
// - tone: 이번 단계에서 강조(active)·새로 생김(new)·빠짐(removed)
export type Box = {
  id: string;
  label: string;
  x: number;
  y: number;
  shape: "cell" | "node" | "slot" | "label";
  tone: "normal" | "active" | "new" | "removed";
};

// 역할: 두 상자를 잇는 화살표(연결 리스트의 next, 포인터 등); 상자 id로 양 끝을 가리킴
export type Arrow = { source: string; target: string; label?: string | null };

// 역할: 한 단계의 화면과 한 줄 설명
export type Frame = { boxes: Box[]; arrows: Arrow[]; caption: string };

// 역할: AI core Animation dump; 막힘 보충 view.animation으로 받음
export type AnimationData = { kind: string; title: string; frames: Frame[] };

// 격자 한 칸의 SVG 크기; 상자는 칸 안에 여백을 두고 그려 화살표가 지나갈 틈을 남김
export const CELL_WIDTH = 64;
export const CELL_HEIGHT = 56;

// 역할: 모든 프레임에서 가장 큰 x·y를 덮는 고정 SVG 크기 계산
// - 단계마다 크기를 다시 재면 축소 비율이 바뀌어 화면이 출렁이므로 전체 프레임 기준 하나로 고정
// - 상자가 없는 장면도 칸 하나 크기를 둬 0 크기 SVG를 막음
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

// 역할: 라벨이 상자 폭(글자 26 viewBox 단위 기준)을 넘어 글자 폭을 줄여야 하는지 판단
// - cell·slot은 세 자리 숫자(-99..999)까지 들어가고, 더 좁은 node는 두 자리까지 들어감
// - label(head·front·rear)은 테두리 없이 칸 폭 전체를 쓸 수 있어 줄이지 않음
export function squeezesLabel(box: Box): boolean {
  if (box.shape === "label") return false;
  return box.label.length > (box.shape === "node" ? 2 : 3);
}

// 역할: 이전·다음·키보드 이동 결과를 0..total-1 안으로 제한
export function clampStep(step: number, total: number): number {
  return Math.min(Math.max(step, 0), Math.max(total - 1, 0));
}

// 역할: 보충 화면을 설명 텍스트와 선택적 애니메이션으로 나눔
// - animation 필드가 없던 시절에 저장된 세션이나 장면 생성에 실패한 보충은 null로 두고 텍스트만 보여 줌
export function remediationParts(view: {
  text: string;
  animation?: AnimationData | null;
}): {
  text: string;
  animation: AnimationData | null;
} {
  return { text: view.text, animation: view.animation ?? null };
}
