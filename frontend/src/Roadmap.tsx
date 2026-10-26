// 파일 역할: 개념 지도를 선수 관계 단계(위→아래)로 그리고 과목 구분·필터, 관계 강조·상세 선택·학습 진입 제공
import { useMemo, useState, type CSSProperties } from "react";
import { ArrowUpRight, BookOpen, RotateCcw } from "lucide-react";
import {
  filterBySubject,
  layoutConcepts,
  listSubjects,
  statusLabels,
  type Concept,
  type LearningStatus,
  type RoadmapState,
} from "./roadmap";
import { StuckButton } from "./StuckPanel";

// 데스크톱 지도 격자(px): 노드 100px·간격 8px로 7개가 한 줄이면 1440px 화면의 지도 카드에 가로 스크롤 없이 들어감
const column = 108;
const row = 88;
const nodeWidth = 100;
const nodeHeight = 60;
// 왼쪽 단계 이름(1단계…) 자리
const gutter = 48;

// 역할: 개념 지도를 불러오는 중이거나 실패했을 때 로드맵 자리에 안내와 다시 시도 버튼 표시
export function ConceptMapStatus({
  error,
  onRetry,
}: {
  error?: string;
  onRetry: () => void;
}) {
  return (
    <main className="roadmap-layout roadmap-status">
      {error ? (
        <section className="card concept-map-status" role="alert">
          <h2>개념 지도를 불러오지 못했어요</h2>
          <p>{error}</p>
          <button className="button secondary" onClick={onRetry}>
            <RotateCcw size={16} aria-hidden="true" />
            다시 시도
          </button>
        </section>
      ) : (
        <section className="card concept-map-status" role="status">
          <p>개념 지도를 불러오는 중이에요…</p>
        </section>
      )}
    </main>
  );
}

// 역할: 선택 상태와 일시적인 호버·초점을 분리해 지도와 상세 패널 렌더링
export function Roadmap({
  concepts,
  state,
  onSelect,
  onStatusChange,
  onStart,
  stuckSessions,
  onStuck,
}: {
  concepts: Concept[];
  state: RoadmapState;
  onSelect: (id: string) => void;
  onStatusChange: (id: string, status: LearningStatus) => void;
  onStart: (concept: Concept) => void;
  // 진행 중 막힘 세션(키 roadmap:<개념 ID>); 버튼 문구를 "이어서 풀기"로 바꾸는 데 사용
  stuckSessions: Record<string, string>;
  onStuck: (concept: Concept) => void;
}) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  // 과목 필터("all" 또는 과목 ID); 저장하지 않는 화면 상태라 새로고침하면 전체로 돌아감
  const [subjectFilter, setSubjectFilter] = useState("all");
  const subjects = useMemo(() => listSubjects(concepts), [concepts]);
  // 과목이 둘 이상일 때만 과목 표식·필터를 보여 줌; 자료구조 지도만 있을 때는 기존 화면과 같음
  const multiSubject = subjects.length > 1;
  // 지도가 바뀌어 고른 과목이 없어졌으면 전체로 봄
  const shownSubject = subjects.some((item) => item.id === subjectFilter)
    ? subjectFilter
    : "all";
  // 지도 배치·선은 필터를 거친 개념(숨긴 과목을 가리키는 선수 관계 제외)으로 그림
  const visible = useMemo(
    () => filterBySubject(concepts, shownSubject),
    [concepts, shownSubject],
  );
  const { layers, positions } = useMemo(
    () => layoutConcepts(visible),
    [visible],
  );
  // 상세 패널·선수 개념 이름은 필터와 무관한 원본 개념을 써서 과목 사이 선수 관계도 보여 줌
  const byId = useMemo(
    () => new Map(concepts.map((concept) => [concept.id, concept])),
    [concepts],
  );
  // 역할: 개념이 지금 지도에 그려지는지 확인; 저장값 ID가 constructor 같은 기본 속성 이름이어도 오판하지 않게 자기 속성만 봄
  const isShown = (id: string) => Object.hasOwn(positions, id);
  // 저장된 선택이 지도에 없거나 필터로 숨겨졌으면(첫 방문·지도 변경·과목 필터) 보이는 첫 개념을 보여 줌
  const selected = byId.get(
    isShown(state.selectedId) ? state.selectedId : visible[0].id,
  )!;
  const status = state.statuses[selected.id] || "not-started";
  const successorsOf = (id: string) =>
    concepts.filter((concept) =>
      concept.prerequisites.some((p) => p.id === id),
    );
  // 호버·초점이 있으면 그 개념을, 없으면 클릭으로 고른 개념을 기준으로 직접 선수·후속 관계를 강조
  const activeId = hoveredId ?? focusedId;
  const focus = byId.get(activeId ?? "") ?? selected;
  const related = new Set([
    ...focus.prerequisites.map((p) => p.id),
    ...successorsOf(focus.id).map((concept) => concept.id),
  ]);
  // 역할: 탐색 중일 때만 관련·비관련 개념을 구분하는 표시 값 반환
  const emphasis = (id: string) =>
    activeId
      ? id === activeId || related.has(id)
        ? "related"
        : "muted"
      : undefined;
  // 역할: 마우스와 키보드 탐색을 같은 강조 상태에 연결; 터치에 호버가 남는 현상 방지
  const interaction = (id: string) => ({
    onPointerEnter: (event: React.PointerEvent<HTMLElement>) => {
      if (event.pointerType !== "touch") setHoveredId(id);
    },
    onPointerLeave: () => setHoveredId(null),
    onFocus: (event: React.FocusEvent<HTMLElement>) => {
      if (event.currentTarget.matches(":focus-visible")) setFocusedId(id);
    },
    onBlur: () => setFocusedId(null),
  });
  // 역할: 상세 패널에서 다른 과목 개념(예: 운영체제 개념의 선수 개념 큐)을 고르면 필터를 풀어 그 개념이 지도에 보이게 함
  const select = (id: string) => {
    if (!isShown(id)) setSubjectFilter("all");
    onSelect(id);
  };
  const widest = Math.max(...layers.map((ids) => ids.length));
  // 역할: 단계·줄 안 위치를 캔버스 왼쪽 위 기준 px 좌표로 변환
  const origin = (id: string) => ({
    left:
      gutter +
      (positions[id].x + (widest - 1) / 2) * column +
      (column - nodeWidth) / 2,
    top: positions[id].layer * row,
  });
  const width = gutter + widest * column;
  const height = (layers.length - 1) * row + nodeHeight;
  const edges = visible.flatMap((concept) =>
    concept.prerequisites.map((p) => ({ source: p.id, target: concept.id })),
  );
  // 강조 선을 나중에 그려 옅은 선 위에 보이게 함(SVG는 나중 요소가 위)
  const isFocusEdge = (edge: { source: string; target: string }) =>
    edge.source === focus.id || edge.target === focus.id;
  const orderedEdges = [
    ...edges.filter((edge) => !isFocusEdge(edge)),
    ...edges.filter(isFocusEdge),
  ];
  const successors = successorsOf(selected.id);

  return (
    <main className="roadmap-layout">
      <section className="card roadmap-map" aria-label="개념 연결 지도">
        <div className="roadmap-heading">
          <div>
            <span className="badge">
              {shownSubject === "all"
                ? subjects.map((item) => item.title).join(" + ")
                : subjects.find((item) => item.id === shownSubject)!.title}{" "}
              개념 지도
            </span>
            <h2>어떤 개념을 살펴볼까요?</h2>
            <p>
              위에서 아래로 갈수록 앞 단계 개념을 먼저 알아야 하는 개념이에요.
              마우스를 올리거나 키보드 초점을 두면 직접 이어진 선수·후속 개념이
              강조되고, 클릭하면 상세를 볼 수 있어요.
            </p>
          </div>
          <BookOpen size={22} aria-hidden="true" />
        </div>
        {/* 과목 필터가 범례를 겸함: 버튼의 색 막대가 지도 노드 위쪽의 과목 표식과 같은 모양 */}
        {multiSubject && (
          <div className="subject-filter" role="group" aria-label="과목">
            {[{ id: "all", title: "전체" }, ...subjects].map((item) => (
              <button
                type="button"
                key={item.id}
                aria-pressed={shownSubject === item.id}
                onClick={() => setSubjectFilter(item.id)}
              >
                {item.id !== "all" && (
                  <span
                    className="subject-swatch"
                    data-subject={item.id}
                    aria-hidden="true"
                  />
                )}
                {item.title}
              </button>
            ))}
          </div>
        )}
        <div className="roadmap-legend" aria-label="학습 상태 범례">
          {Object.entries(statusLabels).map(([key, label]) => (
            <span className={`status-badge ${key}`} key={key}>
              {label}
            </span>
          ))}
        </div>
        <div className="roadmap-scroll">
          <div
            className="roadmap-canvas"
            data-interacting={Boolean(activeId)}
            style={
              {
                "--canvas-width": `${width}px`,
                "--canvas-height": `${height}px`,
              } as CSSProperties
            }
          >
            <svg
              className="roadmap-edges"
              viewBox={`0 0 ${width} ${height}`}
              aria-hidden="true"
            >
              <defs>
                <marker
                  id="roadmap-arrow"
                  viewBox="0 0 8 8"
                  refX="7"
                  refY="4"
                  markerWidth="8"
                  markerHeight="8"
                  orient="auto"
                >
                  <path d="M 0 0 L 8 4 L 0 8 z" />
                </marker>
              </defs>
              {orderedEdges.map((edge) => {
                const from = origin(edge.source);
                const to = origin(edge.target);
                const x1 = from.left + nodeWidth / 2;
                const y1 = from.top + nodeHeight;
                const x2 = to.left + nodeWidth / 2;
                const y2 = to.top;
                const bend = (y2 - y1) / 2;
                const strong = isFocusEdge(edge);
                return (
                  <path
                    className="prerequisite-edge"
                    key={`${edge.source}-${edge.target}`}
                    data-source={edge.source}
                    data-target={edge.target}
                    data-emphasis={strong ? "related" : undefined}
                    markerEnd={strong ? "url(#roadmap-arrow)" : undefined}
                    d={`M ${x1} ${y1} C ${x1} ${y1 + bend} ${x2} ${y2 - bend} ${x2} ${y2}`}
                  />
                );
              })}
            </svg>
            {layers.map((ids, layer) => (
              <div
                className="roadmap-layer"
                role="group"
                aria-label={`${layer + 1}단계`}
                key={layer}
              >
                <span
                  className="layer-label"
                  aria-hidden="true"
                  style={{ top: layer * row + nodeHeight / 2 }}
                >
                  {layer + 1}단계
                </span>
                {ids.map((id) => {
                  const concept = byId.get(id)!;
                  const currentStatus =
                    state.statuses[concept.id] || "not-started";
                  return (
                    <button
                      className={`roadmap-node ${currentStatus}`}
                      key={concept.id}
                      data-concept={concept.id}
                      data-subject={multiSubject ? concept.subject : undefined}
                      data-active={activeId === concept.id}
                      data-emphasis={emphasis(concept.id)}
                      {...interaction(concept.id)}
                      aria-pressed={selected.id === concept.id}
                      aria-label={`${concept.title}${multiSubject ? ` · ${concept.domain}` : ""} · ${statusLabels[currentStatus]}`}
                      style={origin(concept.id)}
                      onClick={() => {
                        onSelect(concept.id);
                        if (window.matchMedia("(max-width: 580px)").matches)
                          requestAnimationFrame(() =>
                            document
                              .querySelector(".concept-detail")
                              ?.scrollIntoView({ block: "start" }),
                          );
                      }}
                    >
                      <strong>{concept.title}</strong>
                      {currentStatus !== "not-started" && (
                        <span className={`status-badge ${currentStatus}`}>
                          {statusLabels[currentStatus]}
                        </span>
                      )}
                      {/* 모바일 목록에서는 선이 없으므로 선수 개념 이름으로 방향을 보여 줌 */}
                      {concept.prerequisites.length > 0 && (
                        <span className="node-prerequisites">
                          먼저:{" "}
                          {concept.prerequisites
                            .map((p) => byId.get(p.id)?.title)
                            .join(", ")}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
        <p className="roadmap-caption">
          같은 단계의 개념끼리는 순서가 없어요. 선은 직접 선수 관계이고, 고른
          개념의 선은 화살표(선수 → 후속)로 강조돼요.
        </p>
      </section>
      {/* 상세 영역: 학습 시작과 [막혔어요](MVP 개념만)를 concept-actions에 모음; 막힘 패널은 App이 화면 옆에 띄움 */}
      <section className="card concept-detail" aria-label="선택한 개념">
        <span className="badge">
          {selected.domain} · {positions[selected.id].layer + 1}단계
        </span>
        <h2>{selected.title}</h2>
        <p>{selected.description}</p>
        <h3>학습 목표</h3>
        <ul className="concept-objectives">
          {selected.objectives.map((objective) => (
            <li key={objective}>{objective}</li>
          ))}
        </ul>
        <label className="status-control">
          학습 상태
          <select
            aria-label="학습 상태"
            value={status}
            onChange={(e) =>
              onStatusChange(selected.id, e.target.value as LearningStatus)
            }
          >
            {Object.entries(statusLabels).map(([value, label]) => (
              <option value={value} key={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <h3>먼저 살펴볼 개념</h3>
        {selected.prerequisites.length ? (
          <ul className="concept-links">
            {selected.prerequisites.map((p) => (
              <li key={p.id}>
                <button
                  className="button secondary"
                  onClick={() => select(p.id)}
                >
                  {byId.get(p.id)?.title}
                  <ArrowUpRight size={14} aria-hidden="true" />
                </button>
                <p>{p.reason}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p>기초 개념부터 바로 시작할 수 있어요.</p>
        )}
        <h3>이어서 살펴볼 개념</h3>
        {successors.length ? (
          <ul className="concept-links">
            {successors.map((item) => (
              <li key={item.id}>
                <button
                  className="button secondary"
                  onClick={() => select(item.id)}
                >
                  {item.title}
                  <ArrowUpRight size={14} aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p>이 개념을 바탕으로 이어지는 개념은 아직 지도에 없어요.</p>
        )}
        <div className="concept-actions">
          <button
            className="button primary start-learning"
            onClick={() => onStart(selected)}
          >
            {state.conversationIds[selected.id] ? "학습 이어가기" : "학습 시작"}
            <ArrowUpRight size={16} aria-hidden="true" />
          </button>
          <StuckButton
            concept={selected}
            inProgress={Boolean(stuckSessions[`roadmap:${selected.id}`])}
            onStuck={() => onStuck(selected)}
          />
        </div>
        <p className="storage-note">
          선택한 개념과 학습 상태는 이 브라우저에 저장돼요. 완료 여부는 직접
          표시해 주세요. 브라우저 데이터를 지우면 기록과 대화 연결이 초기화돼요.
        </p>
      </section>
    </main>
  );
}
