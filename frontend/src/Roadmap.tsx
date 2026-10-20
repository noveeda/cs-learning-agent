import { ArrowUpRight, BookOpen } from "lucide-react";
import {
  concepts,
  statusLabels,
  type Concept,
  type LearningStatus,
  type RoadmapState,
} from "./roadmap";

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
  const selected =
    concepts.find((concept) => concept.id === state.selectedId) || concepts[0];
  const status = state.statuses[selected.id] || "not-started";
  return (
    <main className="roadmap-layout">
      <section className="card roadmap-map" aria-label="개념 연결 지도">
        <div className="roadmap-heading">
          <div>
            <span className="badge">기본 CS 로드맵</span>
            <h2>어떤 개념을 살펴볼까요?</h2>
            <p>개념을 선택하면 설명과 연결을 확인할 수 있어요.</p>
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
        <div className="roadmap-domain-labels" aria-hidden="true">
          <span>운영체제</span>
          <span>자료구조</span>
          <span>네트워크</span>
        </div>
        <div className="roadmap-canvas">
          <svg
            className="roadmap-edges"
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            <defs>
              <marker
                id="roadmap-arrow"
                markerWidth="5"
                markerHeight="5"
                refX="4"
                refY="2.5"
                orient="auto"
              >
                <path d="M0,0 L5,2.5 L0,5" fill="none" stroke="currentColor" />
              </marker>
            </defs>
            {concepts.flatMap((concept) =>
              concept.prerequisites.map((id) => {
                const from = concepts.find((item) => item.id === id)!;
                // Route the longer branch around the middle card so it cannot imply a different prerequisite.
                const path =
                  concept.id === "memory"
                    ? `M ${from.position.x} ${from.position.y} H 1 V ${concept.position.y} H ${concept.position.x - 14.8}`
                    : `M ${from.position.x} ${from.position.y} L ${concept.position.x} ${concept.position.y - 12}`;
                return (
                  <path
                    key={`${id}-${concept.id}`}
                    d={path}
                    markerEnd="url(#roadmap-arrow)"
                  />
                );
              }),
            )}
          </svg>
          {concepts.map((concept) => {
            const currentStatus = state.statuses[concept.id] || "not-started";
            return (
              <button
                className={`roadmap-node ${currentStatus}`}
                key={concept.id}
                aria-pressed={selected.id === concept.id}
                aria-label={`${concept.title} · ${statusLabels[currentStatus]}`}
                style={{
                  left: `${concept.position.x}%`,
                  top: `${concept.position.y}%`,
                }}
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
          연결은 먼저 살펴보면 좋은 개념을 뜻해요. 순서에 관계없이 시작할 수
          있어요.
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
