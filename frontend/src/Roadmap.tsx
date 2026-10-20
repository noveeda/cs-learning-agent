import { type CSSProperties } from "react";
import { ArrowUpRight, BookOpen } from "lucide-react";
import {
  concepts,
  statusLabels,
  type Concept,
  type LearningStatus,
  type RoadmapState,
} from "./roadmap";

const domains = [
  { name: "운영체제", x: 31, y: 70, color: "#315be8", surface: "#edf1ff" },
  { name: "자료구조", x: 27, y: 32, color: "#087c79", surface: "#eaf6f4" },
  { name: "네트워크", x: 75, y: 45, color: "#9a6328", surface: "#fbf3e8" },
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
        <div className="roadmap-canvas">
          <svg
            className="roadmap-edges"
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            {domains.map((domain) => (
              <line
                key={domain.name}
                x1="50"
                y1="50"
                x2={domain.x}
                y2={domain.y}
                stroke={domain.color}
                className="domain-edge"
              />
            ))}
            {concepts.map((concept) => {
              const domain = domains.find(
                (item) => item.name === concept.domain,
              )!;
              return (
                <line
                  key={concept.id}
                  x1={domain.x}
                  y1={domain.y}
                  x2={concept.position.x}
                  y2={concept.position.y}
                  stroke={domain.color}
                />
              );
            })}
            {selected.prerequisites.map((id) => {
              const prerequisite = concepts.find((item) => item.id === id)!;
              return (
                <line
                  className="prerequisite-edge"
                  key={id}
                  x1={prerequisite.position.x}
                  y1={prerequisite.position.y}
                  x2={selected.position.x}
                  y2={selected.position.y}
                />
              );
            })}
          </svg>
          <div className="roadmap-root">
            <strong>내 로드맵</strong>
            <span>기본 CS</span>
          </div>
          {domains.map((domain) => (
            <div
              className="roadmap-domain"
              key={domain.name}
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
          실선은 영역과 개념의 연결이에요. 선택한 개념의 선수 관계는 점선과
          상세에서 확인하세요.
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
