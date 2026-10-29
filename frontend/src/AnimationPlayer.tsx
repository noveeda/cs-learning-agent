/**
 * @file
 * @brief AI core 프레임을 SVG로 그리는 공통 재생기; 채팅 artifact·막힘 보충에서 모션과 자동 재생 제공
 * - 계산된 상태는 그대로 두고 표시용 key·등장·퇴장만 React와 CSS로 관리
 */
import {
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useState,
  type CSSProperties,
  type KeyboardEvent,
} from "react";
import { ChevronLeft, ChevronRight, Pause, Play } from "lucide-react";
import {
  CELL_HEIGHT,
  CELL_WIDTH,
  clampStep,
  compactRows,
  fitViewBox,
  squeezesLabel,
  trackBoxes,
  transitionBoxes,
  type TrackedBox,
  type TransitionBox,
  type AnimationData,
  type Box,
} from "./animation";
import "./animation.css";

// 칸 안에서 상자가 차지하는 크기; 칸과의 차이는 화살표가 지나갈 여백
// - node(연결 리스트·트리)는 옆 노드와 화살표로 이어지므로 폭을 줄여 화살표 선이 보이게 함
const BOX_WIDTH = 52;
const NODE_WIDTH = 44;
const BOX_HEIGHT = 40;
// 시각적 위치 추적을 위한 짧은 전환 시간; 교육적 단계 수나 서버 자원 제한과 무관
const MOTION_MS = 360;
// 설명을 읽으며 따라갈 수 있는 자동 진행 간격; 마지막 단계에서는 반복하지 않음
const PLAY_INTERVAL_MS = 1500;

/** @brief OS의 움직임 줄이기 설정을 구독; 실행 중 변경해도 퇴장 잔상을 바로 제거 */
function useReducedMotion() {
  const [reduced, setReduced] = useState(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return reduced;
}

/**
 * @brief 현재 상자에 퇴장 중인 상자를 합쳐 그림; 완료되면 현재 프레임만 남김
 * - layout effect로 브라우저가 그리기 전에 전환 상태를 맞춰 한 프레임 이전 그림이 튀어나오는 현상 방지
 * - 빠른 단계 이동·언마운트 때 정리 타이머를 취소해, 되살아난 원소를 이전 타이머가 지우지 않게 함
 */
function useTransitionBoxes(next: TrackedBox[], reduced: boolean) {
  /** @brief 모션 완료 시 현재 프레임의 상자만 유지 */
  const settled = (): TransitionBox[] =>
    next.map((item) => ({ ...item, phase: "present" }));
  const [visible, setVisible] = useState<TransitionBox[]>(settled);
  useLayoutEffect(() => {
    setVisible((previous) =>
      reduced ? settled() : transitionBoxes(previous, next),
    );
    if (reduced) return;
    const timer = window.setTimeout(() => setVisible(settled()), MOTION_MS);
    return () => window.clearTimeout(timer);
  }, [next, reduced]);
  return visible;
}

/** @brief 상자 중심에서 테두리까지의 반 폭·반 높이; label은 테두리가 없어 글자 크기만큼만 둠 */
const half = (box: Box) =>
  box.shape === "label"
    ? { w: 16, h: 10 }
    : {
        w: (box.shape === "node" ? NODE_WIDTH : BOX_WIDTH) / 2,
        h: BOX_HEIGHT / 2,
      };

/** @brief 상자 중심의 SVG 좌표 */
const center = (box: Box) => ({
  x: box.x * CELL_WIDTH + CELL_WIDTH / 2,
  y: box.y * CELL_HEIGHT + CELL_HEIGHT / 2,
});

/**
 * @brief 두 상자 중심을 잇되 상자 테두리에서 시작·끝나는 화살표 선분 계산
 * - 중심끼리 이으면 화살촉이 상자 안에 묻히므로, 방향을 따라 각 상자의 반 폭·반 높이만큼 안쪽에서 끊음
 */
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

/**
 * @brief 단계별 애니메이션 재생기; animation을 받아 현재 프레임·설명·단계 제어와 재생/일시정지를 그림
 * - 단계 상태는 이 컴포넌트 안에만 있음; 다른 애니메이션으로 바뀔 때는 부모가 key를 바꿔 첫 단계부터 다시 보여 줌
 * - 키보드 ←·→는 재생기에 초점이 있을 때만 받음(보충 화면의 답 입력창 커서 이동과 겹치지 않게 함)
 */
export function AnimationPlayer({ animation }: { animation: AnimationData }) {
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  const reduced = useReducedMotion();
  const markerId = useId();
  // 빈 행을 접은 표시용 프레임; 이후 그리기·크기 계산은 모두 이 좌표를 씀(원본 animation은 그대로)
  const frames = useMemo(() => compactRows(animation.frames), [animation]);
  const total = frames.length;
  const current = clampStep(step, total);
  const frame = frames[current];
  const tracked = useMemo(() => trackBoxes(frames), [frames]);
  const visible = useTransitionBoxes(tracked[current], reduced);
  const { y, width, height } = fitViewBox(frames);
  const boxes = new Map(frame.boxes.map((box) => [box.id, box]));
  /** @brief 상대 단계로 이동하고 자동 재생 정지; 읽던 단계가 남은 타이머에 의해 바뀌는 현상 방지 */
  const go = (offset: number) => {
    setPlaying(false);
    setStep((previous) => clampStep(previous + offset, total));
  };
  useEffect(() => {
    if (!playing || current >= total - 1) return;
    const timer = window.setTimeout(() => {
      setStep(current + 1);
      if (current + 1 === total - 1) setPlaying(false);
    }, PLAY_INTERVAL_MS);
    return () => window.clearTimeout(timer);
  }, [playing, current, total]);
  /** @brief 재생·일시정지 전환; 마지막 단계에서 다시 누르면 처음부터 재생 */
  const togglePlayback = () => {
    if (!playing && current === total - 1) setStep(0);
    setPlaying((previous) => !previous);
  };
  /** @brief 재생기에 초점이 있을 때 좌우 키를 수동 단계 이동으로 처리 */
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "ArrowLeft") go(-1);
    else if (event.key === "ArrowRight") go(1);
    else return;
    event.preventDefault();
  };
  return (
    <figure
      className="animation-player"
      style={{ "--animation-duration": `${MOTION_MS}ms` } as CSSProperties}
      aria-label={`${animation.title} 단계별 그림, 좌우 화살표 키로 단계 이동`}
      tabIndex={0}
      onKeyDown={onKeyDown}
    >
      <figcaption className="animation-title">{animation.title}</figcaption>
      <svg
        className="animation-canvas"
        viewBox={`0 ${y} ${width} ${height}`}
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
        {/* 간선은 목표 프레임의 연결을 그리며 페이드로 표시; 계산된 연결 구조는 바꾸지 않음 */}
        <g key={current} className="animation-arrows">
          {frame.arrows.map((arrow) => {
            const source = boxes.get(arrow.source);
            const target = boxes.get(arrow.target);
            // 양 끝 중 하나라도 이번 프레임에 없으면 가리킬 곳이 없으므로 그리지 않음
            if (!source || !target) return null;
            const line = arrowLine(source, target);
            return (
              <g key={`${arrow.source}-${arrow.target}`}>
                <line
                  {...line}
                  markerEnd={
                    arrow.head === false ? undefined : `url(#${markerId})`
                  }
                />
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
        {visible.map(({ key, box, phase }) => (
          <g
            key={key}
            data-box-id={box.id}
            aria-hidden={phase === "exit" ? true : undefined}
            className={`animation-box shape-${box.shape} tone-${box.tone} motion-${phase}`}
            // CSS transform이어야 transition이 적용됨(SVG transform 속성은 애니메이션되지 않음)
            style={{
              transform: `translate(${box.x * CELL_WIDTH}px, ${box.y * CELL_HEIGHT}px)`,
            }}
          >
            {/* 위치 이동과 등장/퇴장 scale을 서로 다른 g에 적용해 transform 충돌 방지 */}
            <g className="animation-box-content">
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
          onClick={() => go(-1)}
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
          onClick={() => go(1)}
        >
          다음 <ChevronRight size={16} aria-hidden="true" />
        </button>
        <button
          type="button"
          className="button secondary animation-play"
          aria-label={playing ? "자동 재생 일시정지" : current === total - 1 && total > 1 ? "처음부터 자동 재생" : "자동 재생"}
          aria-pressed={playing}
          disabled={total < 2}
          onClick={togglePlayback}
        >
          {playing ? <Pause size={16} aria-hidden="true" /> : <Play size={16} aria-hidden="true" />}
          {playing ? "일시정지" : current === total - 1 && total > 1 ? "다시 재생" : "자동 재생"}
        </button>
      </div>
    </figure>
  );
}
