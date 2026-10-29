/**
 * @file
 * @brief 진단 문항 지문 옆에 보여 줄 정지 도식을 SVG로 그림(도형·선·표만, 움직임 없음)
 * - 데이터는 AI core serving/figure.py 검사기를 통과한 figure; 좌표가 들어 있어 자동 배치 없이 그대로 그림
 * - 색은 공통 CSS 변수만 써서 밝은·어두운 테마 모두 따라감; 강조 색은 쓰지 않음(정답 표시 금지)
 */
import { useId } from "react";
import "./figure.css";

/** @brief 도형 하나; x·y는 반 칸 단위 격자 좌표이고 도형 중심이 칸 가운데에 옴 */
export type FigureNode = { id: string; label: string; shape: "box" | "circle" | "cylinder" | "cell" | "text"; x: number; y: number };
/** @brief 두 도형(또는 표)을 잇는 선; directed가 거짓이면 화살표 없이 그림 */
export type FigureEdge = { from: string; to: string; label?: string | null; directed?: boolean };
/** @brief 이름·열·행이 있는 표; keys 열은 밑줄로 표시 */
export type FigureTable = { id: string; name: string; columns: string[]; rows?: string[][]; keys?: string[]; x: number; y: number };
/** @brief 문항 하나의 도식(문항 은행 figure 필드) */
export type FigureData = { nodes?: FigureNode[]; edges?: FigureEdge[]; tables?: FigureTable[] };

// 아래 크기 상수는 ai-core/serving/figure.py와 값이 같아야 검사기가 확인한 배치(겹침·라벨 폭)와 실제 그림이 맞음
const CELL_W = 100;
const CELL_H = 72;
const SHAPES: Record<FigureNode["shape"], [number, number]> = {
  box: [88, 40], circle: [44, 44], cylinder: [72, 48], cell: [50, 36], text: [96, 28],
};
const TABLE_COL_W = 80;
const TABLE_ROW_H = 28;
const TABLE_NAME_H = 24;
// 그림 둘레 여백: 선 라벨과 테두리가 잘리지 않게 함
const MARGIN = 16;
// 좁은 화면에서 줄일 수 있는 최소 폭; 더 줄이면 글자가 읽히지 않으므로 그보다 좁으면 도식만 가로로 밀어 봄
const MIN_WIDTH = 400;
// 같은 두 도형을 반대 방향으로 잇는 선끼리 겹치지 않게 옆으로 비키는 거리
const PARALLEL_OFFSET = 7;

type Rect = { left: number; top: number; right: number; bottom: number; round: boolean };

/** @brief 도형·표의 화면 경계 상자 계산; 선을 도형 테두리에서 끊고 viewBox를 정하는 데 씀 */
function layout(figure: FigureData) {
  const rects = new Map<string, Rect>();
  for (const node of figure.nodes ?? []) {
    const [w, h] = SHAPES[node.shape];
    const cx = (node.x + 0.5) * CELL_W;
    const cy = (node.y + 0.5) * CELL_H;
    rects.set(node.id, { left: cx - w / 2, top: cy - h / 2, right: cx + w / 2, bottom: cy + h / 2, round: node.shape === "circle" });
  }
  for (const table of figure.tables ?? []) {
    const left = table.x * CELL_W;
    const top = table.y * CELL_H;
    const height = TABLE_NAME_H + TABLE_ROW_H * (1 + (table.rows?.length ?? 0));
    rects.set(table.id, { left, top, right: left + table.columns.length * TABLE_COL_W, bottom: top + height, round: false });
  }
  const all = [...rects.values()];
  const box = {
    left: Math.min(...all.map((r) => r.left)) - MARGIN,
    top: Math.min(...all.map((r) => r.top)) - MARGIN,
    right: Math.max(...all.map((r) => r.right)) + MARGIN,
    bottom: Math.max(...all.map((r) => r.bottom)) + MARGIN,
  };
  return { rects, box };
}

/** @brief 중심에서 (dx, dy) 방향으로 나갔을 때 도형 테두리까지의 거리; 원은 반지름, 사각형은 먼저 닿는 변 */
function reach(rect: Rect, dx: number, dy: number) {
  const hw = (rect.right - rect.left) / 2;
  const hh = (rect.bottom - rect.top) / 2;
  if (rect.round) return hw;
  return Math.min(dx ? hw / Math.abs(dx) : Infinity, dy ? hh / Math.abs(dy) : Infinity);
}

/** @brief 선 하나의 시작·끝 좌표와 라벨 위치; 반대 방향 선이 있으면 진행 방향 왼쪽으로 비켜 그림 */
function edgeLine(source: Rect, target: Rect, shifted: boolean) {
  const sx = (source.left + source.right) / 2;
  const sy = (source.top + source.bottom) / 2;
  const tx = (target.left + target.right) / 2;
  const ty = (target.top + target.bottom) / 2;
  const length = Math.hypot(tx - sx, ty - sy) || 1;
  const ux = (tx - sx) / length;
  const uy = (ty - sy) / length;
  // 진행 방향의 왼쪽 법선; 라벨도 이쪽에 두어 선과 글자가 겹치지 않게 함
  const nx = uy;
  const ny = -ux;
  const offset = shifted ? PARALLEL_OFFSET : 0;
  const start = reach(source, ux, uy) + 2;
  const end = reach(target, ux, uy) + 2;
  return {
    x1: sx + ux * start + nx * offset, y1: sy + uy * start + ny * offset,
    x2: tx - ux * end + nx * offset, y2: ty - uy * end + ny * offset,
    lx: (sx + tx) / 2 + nx * (offset + 11), ly: (sy + ty) / 2 + ny * (offset + 11),
  };
}

/** @brief 모양별 도형 하나; 원기둥은 위·아래 타원을 이은 경로로 저장소(DB·디스크)를 나타냄 */
function Shape({ node, rect }: { node: FigureNode; rect: Rect }) {
  const cx = (rect.left + rect.right) / 2;
  const cy = (rect.top + rect.bottom) / 2;
  const w = rect.right - rect.left;
  const h = rect.bottom - rect.top;
  const ry = 7;
  return (
    <g className={`figure-node shape-${node.shape}`} data-node-id={node.id}>
      {node.shape === "circle" && <circle cx={cx} cy={cy} r={w / 2} />}
      {(node.shape === "box" || node.shape === "cell") && (
        <rect x={rect.left} y={rect.top} width={w} height={h} rx={node.shape === "box" ? 6 : 0} />
      )}
      {node.shape === "cylinder" && (
        <>
          <path d={`M${rect.left} ${rect.top + ry} a${w / 2} ${ry} 0 0 0 ${w} 0 v${h - 2 * ry} a${w / 2} ${ry} 0 0 1 ${-w} 0 z`} />
          <ellipse cx={cx} cy={rect.top + ry} rx={w / 2} ry={ry} />
        </>
      )}
      <text x={cx} y={node.shape === "cylinder" ? cy + ry / 2 : cy}>{node.label}</text>
    </g>
  );
}

/** @brief 표 하나: 이름 줄, 열 이름 줄(키 열은 밑줄), 행 값 칸 순서로 그림 */
function Table({ table }: { table: FigureTable }) {
  const left = table.x * CELL_W;
  const top = table.y * CELL_H + TABLE_NAME_H;
  const rows = table.rows ?? [];
  return (
    <g className="figure-table" data-table-id={table.id}>
      <text className="figure-table-name" x={left} y={top - 8}>{table.name}</text>
      {[table.columns, ...rows].map((cells, row) => cells.map((value, col) => (
        <g key={`${row}-${col}`}>
          <rect className={row === 0 ? "figure-table-head" : undefined} x={left + col * TABLE_COL_W} y={top + row * TABLE_ROW_H} width={TABLE_COL_W} height={TABLE_ROW_H} />
          <text
            x={left + (col + 0.5) * TABLE_COL_W}
            y={top + (row + 0.5) * TABLE_ROW_H}
            // 키 밑줄은 글자 기준선에 맞아야 하므로 중앙 정렬 대신 기준선 + dy로 세로 가운데를 맞춤
            {...(row === 0 && table.keys?.includes(value) && { className: "figure-key", dy: "0.35em" })}
          >
            {value}
          </text>
        </g>
      )))}
    </g>
  );
}

/**
 * @brief 문항 도식 컴포넌트; figure를 받아 정지 SVG 한 장을 그림
 * - 선을 먼저, 도형·표를 나중에 그려 선 끝이 도형 위로 올라오지 않게 함
 * - label은 화면 낭독기용 설명; 도식 글은 짧은 라벨뿐이라 지문 자체가 대체 설명 역할을 함
 */
export function ItemFigure({ figure, label = "문항 도식" }: { figure: FigureData; label?: string }) {
  const markerId = useId();
  const { rects, box } = layout(figure);
  const width = box.right - box.left;
  const height = box.bottom - box.top;
  const edges = figure.edges ?? [];
  const pairs = new Set(edges.map((edge) => `${edge.from}>${edge.to}`));
  return (
    <figure className="item-figure">
      <svg
        viewBox={`${box.left} ${box.top} ${width} ${height}`}
        // 넓은 화면에서는 원래 크기 이상 키우지 않고, 좁은 화면에서는 MIN_WIDTH까지만 줄임
        style={{ maxWidth: width, minWidth: Math.min(width, MIN_WIDTH) }}
        role="img"
        aria-label={label}
      >
        <defs>
          <marker id={markerId} viewBox="0 0 8 8" refX="8" refY="4" markerUnits="userSpaceOnUse"
            markerWidth="8" markerHeight="8" orient="auto-start-reverse">
            <path d="M0 0 L8 4 L0 8 z" className="figure-arrowhead" />
          </marker>
        </defs>
        {edges.map((edge) => {
          const source = rects.get(edge.from);
          const target = rects.get(edge.to);
          if (!source || !target) return null;
          const line = edgeLine(source, target, pairs.has(`${edge.to}>${edge.from}`));
          return (
            <g key={`${edge.from}>${edge.to}`} className="figure-edge">
              <line x1={line.x1} y1={line.y1} x2={line.x2} y2={line.y2}
                markerEnd={edge.directed === false ? undefined : `url(#${markerId})`} />
              {edge.label && <text x={line.lx} y={line.ly}>{edge.label}</text>}
            </g>
          );
        })}
        {(figure.tables ?? []).map((table) => <Table key={table.id} table={table} />)}
        {(figure.nodes ?? []).map((node) => <Shape key={node.id} node={node} rect={rects.get(node.id)!} />)}
      </svg>
    </figure>
  );
}
