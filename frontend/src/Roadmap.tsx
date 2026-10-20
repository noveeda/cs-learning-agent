import { useState, type CSSProperties } from "react";
import { ArrowUpRight, BookOpen } from "lucide-react";
import {
  concepts,
  conceptRelations,
  statusLabels,
  type Concept,
  type LearningStatus,
  type RoadmapState,
} from "./roadmap";

const domains = [
  { name: "운영체제", x: 31, y: 77, color: "#315be8", surface: "#edf1ff" },
  { name: "자료구조", x: 25, y: 25, color: "#087c79", surface: "#eaf6f4" },
  { name: "네트워크", x: 77, y: 29, color: "#9a6328", surface: "#fbf3e8" },
];

export function Roadmap({
  state,
  onSelect,
  onStatusChange,
  onStart,
}: {
  state: RoadmapState;
  onSelect: (id: string) => void;
  onStatusChange: (id: string, status: LearningStatus) => void;
  onStart: (concept: Concept) => void;
}) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const activeId = hoveredId ?? focusedId;
  const activeConcept = concepts.find((concept) => concept.id === activeId);
  const activeDomain = domains.find((domain) => domain.name === activeId);
  const selected =
    concepts.find((concept) => concept.id === state.selectedId) || concepts[0];
  const status = state.statuses[selected.id] || "not-started";
  const previewIds =
    activeId === "root"
      ? concepts.map((concept) => concept.id)
      : activeDomain
        ? concepts
            .filter((concept) => concept.domain === activeDomain.name)
            .map((concept) => concept.id)
        : [(activeConcept || selected).id];
  const visibleRelations = conceptRelations.filter(
    (relation) =>
      previewIds.includes(relation.source) ||
      previewIds.includes(relation.target),
  );
  const related = new Set(
    concepts
      .filter(
        (concept) =>
          activeId === "root" ||
          concept.domain === activeDomain?.name ||
          concept.domain === activeConcept?.domain,
      )
      .map((concept) => concept.id),
  );
  if (activeId)
    for (const relation of visibleRelations) {
      related.add(relation.source);
      related.add(relation.target);
    }
  const emphasizedDomain = (name: string) =>
    activeId === "root" ||
    name === activeDomain?.name ||
    concepts.some(
      (concept) => concept.domain === name && related.has(concept.id),
    );
  const emphasis = (isRelated: boolean) =>
    activeId ? (isRelated ? "related" : "muted") : undefined;
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

  return (
    <main className="roadmap-layout">
      <section className="card roadmap-map" aria-label="개념 연결 지도">
        <div className="roadmap-heading">
          <div>
            <span className="badge">기본 CS 로드맵</span>
            <h2>어떤 개념을 살펴볼까요?</h2>
            <p>
              개념에 마우스를 올리거나 키보드 초점을 두면 연결이 강조돼요.
              클릭하면 상세를 볼 수 있어요.
            </p>
          </div>
          <BookOpen size={22} aria-hidden="true" />
        </div>
        <div className="roadmap-legend" aria-label="학습 상태 범례">
          {Object.entries(statusLabels).map(([key, label]) => (
            <span className={`status-badge ${key}`} key={key}>
              {label}
            </span>
          ))}
        </div>
        <div className="roadmap-canvas" data-interacting={Boolean(activeId)}>
          <svg
            className="roadmap-edges"
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            {visibleRelations.map((relation) => {
              const source = concepts.find(
                (concept) => concept.id === relation.source,
              )!;
              const target = concepts.find(
                (concept) => concept.id === relation.target,
              )!;
              return (
                <path
                  className="cross-domain-edge"
                  key={`${source.id}-${target.id}`}
                  data-source={source.id}
                  data-target={target.id}
                  data-emphasis={emphasis(
                    related.has(source.id) && related.has(target.id),
                  )}
                  d={`M ${source.position.x} ${source.position.y} Q ${relation.bend.x} ${relation.bend.y} ${target.position.x} ${target.position.y}`}
                >
                  <title>
                    {source.title} ↔ {target.title}：{relation.description}
                  </title>
                </path>
              );
            })}
          </svg>
          <div
            className="roadmap-root"
            role="group"
            tabIndex={0}
            aria-label="전체 로드맵"
            data-active={activeId === "root"}
            {...interaction("root")}
          >
            <strong>내 로드맵</strong>
            <span>기본 CS</span>
          </div>
          {domains.map((domain) => (
            <div
              className="roadmap-domain"
              role="group"
              key={domain.name}
              tabIndex={0}
              aria-label={`${domain.name} 영역`}
              data-active={activeId === domain.name}
              data-emphasis={emphasis(emphasizedDomain(domain.name))}
              {...interaction(domain.name)}
              style={{
                left: `${domain.x}%`,
                top: `${domain.y}%`,
                color: domain.color,
                background: domain.surface,
                borderColor: domain.color,
              }}
            >
              {domain.name}
            </div>
          ))}
          {concepts.map((concept) => {
            const currentStatus = state.statuses[concept.id] || "not-started";
            const domain = domains.find(
              (item) => item.name === concept.domain,
            )!;
            return (
              <button
                className={`roadmap-node ${currentStatus}`}
                key={concept.id}
                data-domain={concept.domain}
                data-active={activeId === concept.id}
                data-emphasis={emphasis(related.has(concept.id))}
                {...interaction(concept.id)}
                aria-pressed={selected.id === concept.id}
                aria-label={`${concept.title} · ${statusLabels[currentStatus]}`}
                style={
                  {
                    left: `${concept.position.x}%`,
                    top: `${concept.position.y}%`,
                    "--domain-color": domain.color,
                    "--domain-surface": domain.surface,
                  } as CSSProperties
                }
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
                <span className="node-domain">{concept.domain}</span>
                <strong>{concept.title}</strong>
                <span className={`status-badge ${currentStatus}`}>
                  {statusLabels[currentStatus]}
                </span>
              </button>
            );
          })}
        </div>
        <p className="roadmap-caption">
          큰 원은 학습 영역이고, 안의 작은 원은 해당 영역의 개념이에요. 점선은
          다른 영역의 개념 간 연관 관계예요.
        </p>
      </section>
      <section className="card concept-detail" aria-label="선택한 개념">
        <span className="badge">{selected.domain}</span>
        <h2>{selected.title}</h2>
        <p>{selected.description}</p>
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
            {selected.prerequisites.map((id) => {
              const concept = concepts.find((item) => item.id === id)!;
              return (
                <li key={id}>
                  <button
                    className="button secondary"
                    onClick={() => onSelect(id)}
                  >
                    {concept.title}
                    <ArrowUpRight size={14} aria-hidden="true" />
                  </button>
                </li>
              );
            })}
          </ul>
        ) : (
          <p>기초 개념부터 바로 시작할 수 있어요.</p>
        )}
        <h3>이어서 살펴볼 개념</h3>
        {concepts.some((item) => item.prerequisites.includes(selected.id)) ? (
          <ul className="concept-links">
            {concepts
              .filter((item) => item.prerequisites.includes(selected.id))
              .map((item) => (
                <li key={item.id}>
                  <button
                    className="button secondary"
                    onClick={() => onSelect(item.id)}
                  >
                    {item.title}
                    <ArrowUpRight size={14} aria-hidden="true" />
                  </button>
                </li>
              ))}
          </ul>
        ) : (
          <p>이 개념을 학습한 뒤 다른 영역도 살펴보세요.</p>
        )}
        <h3>다른 영역의 연관 개념</h3>
        {conceptRelations.some(
          (relation) =>
            relation.source === selected.id || relation.target === selected.id,
        ) ? (
          <ul className="concept-links">
            {conceptRelations
              .filter(
                (relation) =>
                  relation.source === selected.id ||
                  relation.target === selected.id,
              )
              .map((relation) => {
                const linkedId =
                  relation.source === selected.id
                    ? relation.target
                    : relation.source;
                const linked = concepts.find(
                  (concept) => concept.id === linkedId,
                )!;
                return (
                  <li key={linkedId}>
                    <button
                      className="button secondary"
                      onClick={() => onSelect(linkedId)}
                    >
                      {linked.title}
                      <ArrowUpRight size={14} aria-hidden="true" />
                    </button>
                    <p>
                      {linked.domain} · {relation.description}
                    </p>
                  </li>
                );
              })}
          </ul>
        ) : (
          <p>아직 연결한 다른 영역의 개념이 없어요.</p>
        )}
        <button
          className="button primary start-learning"
          onClick={() => onStart(selected)}
        >
          {state.conversationIds[selected.id] ? "학습 이어가기" : "학습 시작"}
          <ArrowUpRight size={16} aria-hidden="true" />
        </button>
        <p className="storage-note">
          선택한 개념과 학습 상태는 이 브라우저에 저장돼요. 완료 여부는 직접
          표시해 주세요. 브라우저 데이터를 지우면 기록과 대화 연결이 초기화돼요.
        </p>
      </section>
    </main>
  );
}
