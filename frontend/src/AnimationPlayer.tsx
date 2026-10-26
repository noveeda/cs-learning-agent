// 파일 역할: AI core가 계산한 애니메이션 프레임을 SVG로 그리고 이전·다음 단계로 넘기는 재생기; 막힘 보충 화면에서 사용
// - 새 라이브러리 없이 SVG + CSS transition만 씀; 상자 id가 같으면 위치가 바뀔 때 transition으로 움직임을 보여 줌
import { useId, useState, type KeyboardEvent } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import {
  CELL_HEIGHT,
  CELL_WIDTH,
  clampStep,
  fitViewBox,
  squeezesLabel,
  type AnimationData,
  type Box,
} from "./animation";
import "./animation.css";

// 칸 안에서 상자가 차지하는 크기; 칸과의 차이는 화살표가 지나갈 여백
// - node(연결 리스트·트리)는 옆 노드와 화살표로 이어지므로 폭을 줄여 화살표 선이 보이게 함
const BOX_WIDTH = 52;
const NODE_WIDTH = 44;
const BOX_HEIGHT = 40;

// 역할: 상자 중심에서 테두리까지의 반 폭·반 높이; label은 테두리가 없어 글자 크기만큼만 둠
const half = (box: Box) =>
  box.shape === "label"
    ? { w: 16, h: 10 }
    : {
        w: (box.shape === "node" ? NODE_WIDTH : BOX_WIDTH) / 2,
        h: BOX_HEIGHT / 2,
      };

// 역할: 상자 중심의 SVG 좌표
const center = (box: Box) => ({
  x: box.x * CELL_WIDTH + CELL_WIDTH / 2,
  y: box.y * CELL_HEIGHT + CELL_HEIGHT / 2,
});

// 역할: 두 상자 중심을 잇되 상자 테두리에서 시작·끝나는 화살표 선분 계산
// - 중심끼리 이으면 화살촉이 상자 안에 묻히므로, 방향을 따라 각 상자의 반 폭·반 높이만큼 안쪽에서 끊음
function arrowLine(source: Box, target: Box) {
  const from = center(source);
  const to = center(target);
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  // 중심에서 (dx, dy) 방향으로 나가 테두리에 닿는 지점까지의 비율
  const edge = (box: Box) => {
    const { w, h } = half(box);
    return Math.min(
      dx ? w / Math.abs(dx) : Infinity,
      dy ? h / Math.abs(dy) : Infinity,
    );
  };
  const start = edge(source);
  // 화살촉 끝이 테두리에 겹치지 않게 2px 떨어뜨림
  const end = edge(target) + 2 / Math.hypot(dx, dy);
  return {
    x1: from.x + dx * start,
    y1: from.y + dy * start,
    x2: to.x - dx * end,
    y2: to.y - dy * end,
  };
}

// 역할: 단계별 애니메이션 재생기; animation을 받아 현재 단계 프레임·한 줄 설명·이전/다음 버튼을 그림
// - 단계 상태는 이 컴포넌트 안에만 있음; 다른 애니메이션으로 바뀔 때는 부모가 key를 바꿔 첫 단계부터 다시 보여 줌
// - 키보드 ←·→는 재생기에 초점이 있을 때만 받음(보충 화면의 답 입력창 커서 이동과 겹치지 않게 함)
export function AnimationPlayer({ animation }: { animation: AnimationData }) {
  const [step, setStep] = useState(0);
  const markerId = useId();
  const total = animation.frames.length;
  const current = clampStep(step, total);
  const frame = animation.frames[current];
  const { width, height } = fitViewBox(animation.frames);
  const boxes = new Map(frame.boxes.map((box) => [box.id, box]));
  const go = (next: number) => setStep(clampStep(next, total));
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "ArrowLeft") go(current - 1);
    else if (event.key === "ArrowRight") go(current + 1);
    else return;
    event.preventDefault();
  };
  return (
    <figure
      className="animation-player"
      aria-label={`${animation.title} 단계별 그림, 좌우 화살표 키로 단계 이동`}
      tabIndex={0}
      onKeyDown={onKeyDown}
    >
      <figcaption className="animation-title">{animation.title}</figcaption>
      <svg
        className="animation-canvas"
        viewBox={`0 0 ${width} ${height}`}
        // 화면 폭이 좁으면 비율을 유지한 채 줄이고, 넓어도 원래 크기 이상으로 키우지 않음
        style={{ maxWidth: width }}
        role="img"
        aria-label={frame.caption}
      >
        <defs>
          <marker
            id={markerId}
            viewBox="0 0 8 8"
            refX="8"
            refY="4"
            // 선 굵기와 상관없이 같은 크기로 그려 짧은 화살표에서도 선이 보이게 함
            markerUnits="userSpaceOnUse"
            markerWidth="8"
            markerHeight="8"
            orient="auto-start-reverse"
          >
            <path d="M0 0 L8 4 L0 8 z" className="animation-arrowhead" />
          </marker>
        </defs>
        {/* 화살표는 단계마다 새로 그려 옅게 나타나게 함; 상자는 아래에서 id로 유지해 이동을 보여 줌 */}
        <g key={current} className="animation-arrows">
          {frame.arrows.map((arrow) => {
            const source = boxes.get(arrow.source);
            const target = boxes.get(arrow.target);
            // 양 끝 중 하나라도 이번 프레임에 없으면 가리킬 곳이 없으므로 그리지 않음
            if (!source || !target) return null;
            const line = arrowLine(source, target);
            return (
              <g key={`${arrow.source}-${arrow.target}`}>
                <line {...line} markerEnd={`url(#${markerId})`} />
                {arrow.label && (
                  <text
                    x={(line.x1 + line.x2) / 2}
                    y={(line.y1 + line.y2) / 2 - 6}
                    className="animation-arrow-label"
                  >
                    {arrow.label}
                  </text>
                )}
              </g>
            );
          })}
        </g>
        {frame.boxes.map((box) => (
          <g
            key={box.id}
            className={`animation-box shape-${box.shape} tone-${box.tone}`}
            // CSS transform이어야 transition이 적용됨(SVG transform 속성은 애니메이션되지 않음)
            style={{
              transform: `translate(${box.x * CELL_WIDTH}px, ${box.y * CELL_HEIGHT}px)`,
            }}
          >
            {box.shape !== "label" && (
              <rect
                x={CELL_WIDTH / 2 - half(box).w}
                y={(CELL_HEIGHT - BOX_HEIGHT) / 2}
                width={half(box).w * 2}
                height={BOX_HEIGHT}
                rx={box.shape === "node" ? BOX_HEIGHT / 2 : 6}
              />
            )}
            <text
              x={CELL_WIDTH / 2}
              y={CELL_HEIGHT / 2}
              // 라벨은 16자까지 올 수 있어 상자보다 길면 상자 폭에 맞춰 글자 간격·폭을 줄임
              {...(squeezesLabel(box) && {
                textLength: half(box).w * 2 - 6,
                lengthAdjust: "spacingAndGlyphs",
              })}
            >
              {box.label}
            </text>
          </g>
        ))}
      </svg>
      <p className="animation-caption" aria-live="polite">
        {frame.caption}
      </p>
      <div className="animation-controls">
        <button
          type="button"
          className="button secondary"
          aria-label="이전 단계"
          disabled={current === 0}
          onClick={() => go(current - 1)}
        >
          <ChevronLeft size={16} aria-hidden="true" /> 이전
        </button>
        <span className="animation-step">
          {current + 1} / {total}
        </span>
        <button
          type="button"
          className="button secondary"
          aria-label="다음 단계"
          disabled={current === total - 1}
          onClick={() => go(current + 1)}
        >
          다음 <ChevronRight size={16} aria-hidden="true" />
        </button>
      </div>
    </figure>
  );
}
