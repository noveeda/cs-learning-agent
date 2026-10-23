// 파일 역할: React 진입점; 해시 경로·로드맵 상태·개념별 대화 ID·커리큘럼 화면 연결
import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { newConversation } from "./api";
import { ChatSession } from "./ChatSession";
import { CurriculumDraft } from "./CurriculumDraft";
import { LearningShell } from "./LearningShell";
import { Roadmap } from "./Roadmap";
import {
  concepts,
  loadRoadmap,
  saveRoadmap,
  type Concept,
  type RoadmapState,
  type LearningStatus,
} from "./roadmap";
import "./style.css";
import "./roadmap.css";

// 역할: URL 해시에서 화면과 알려진 개념만 해석; 잘못된 경로는 학습 화면으로 처리
function readRoute() {
  const hash = window.location.hash || "#/learn";
  const [path, query] = hash.slice(1).split("?");
  const concept = concepts.find(
    (item) => item.id === new URLSearchParams(query).get("concept"),
  );
  return {
    page: path === "/roadmap" ? "roadmap" : "learn",
    concept: path === "/learn" ? concept : undefined,
    hash,
  };
}

// 역할: 화면 간 상태를 조율하고 선택 개념의 대화와 학습 표시를 연결
function App() {
  const [curriculumOpen, setCurriculumOpen] = useState(false);
  const [roadmap, setRoadmap] = useState(loadRoadmap);
  const [route, setRoute] = useState(readRoute);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const routeRef = useRef(route);
  const [freeId, setFreeId] = useState(() => {
    try {
      return localStorage.getItem("cs-conversation") || newConversation();
    } catch {
      return crypto.randomUUID().replaceAll("-", "");
    }
  });
  useEffect(() => {
    const onRoute = () => {
      // 응답 중 해시가 바뀌어 세션이 해제되는 것을 막기 위해 마지막 경로로 복원
      if (busyRef.current) {
        window.history.replaceState(null, "", routeRef.current.hash);
        setNotice("응답을 기다린 뒤 화면을 이동해 주세요.");
        return;
      }
      setCurriculumOpen(false);
      const next = readRoute();
      routeRef.current = next;
      setRoute(next);
      setNotice("");
    };
    window.addEventListener("hashchange", onRoute);
    return () => window.removeEventListener("hashchange", onRoute);
  }, []);

  // 역할: 브라우저 저장 성공 뒤에만 화면 상태 갱신; 저장 실패를 성공처럼 표시하지 않음
  function updateRoadmap(next: RoadmapState) {
    try {
      saveRoadmap(next);
      setRoadmap(next);
      setNotice("");
      return true;
    } catch {
      setNotice(
        "변경 사항을 저장하지 못했습니다. 브라우저 저장 공간과 사이트 데이터 권한을 확인한 뒤 다시 시도해 주세요.",
      );
      return false;
    }
  }
  // 역할: 상세 패널에 표시할 개념을 선택하고 브라우저에 보관
  function selectConcept(id: string) {
    updateRoadmap({ ...roadmap, selectedId: id });
  }
  // 역할: 사용자가 지정한 학습 상태 저장; AI의 이해도 판정과는 별개
  function updateStatus(id: string, status: LearningStatus) {
    updateRoadmap({
      ...roadmap,
      statuses: { ...roadmap.statuses, [id]: status },
    });
  }
  // 역할: 개념별 대화 ID를 재사용하거나 생성하고 저장 성공 시 학습 화면으로 이동
  function startConcept(concept: Concept) {
    const id =
      roadmap.conversationIds[concept.id] ||
      crypto.randomUUID().replaceAll("-", "");
    const next: RoadmapState = {
      ...roadmap,
      selectedId: concept.id,
      statuses: {
        ...roadmap.statuses,
        [concept.id]:
          roadmap.statuses[concept.id] === "completed"
            ? "completed"
            : "in-progress",
      },
      conversationIds: { ...roadmap.conversationIds, [concept.id]: id },
    };
    if (updateRoadmap(next))
      window.location.hash = `/learn?concept=${concept.id}`;
  }
  const conceptId = route.concept?.id;
  const conceptConversation = conceptId
    ? roadmap.conversationIds[conceptId]
    : undefined;
  // Direct links without a saved session open the roadmap so starting can be saved first.
  const isRoadmap =
    route.page === "roadmap" || Boolean(route.concept && !conceptConversation);
  return (
    <LearningShell
      page={isRoadmap ? "roadmap" : "learn"}
      busy={busy}
      headerExtra={
        <button
          className="button secondary"
          onClick={() => setCurriculumOpen((open) => !open)}
        >
          {curriculumOpen ? (isRoadmap ? "로드맵으로" : "학습 대화로") : "커리큘럼 초안"}
        </button>
      }
      onNavigate={(page) => {
        setCurriculumOpen(false);
        window.location.hash = `/${page}`;
      }}
      onAsk={() => {
        setCurriculumOpen(false);
        requestAnimationFrame(() => document.getElementById("question")?.focus());
      }}
      onNew={() => {
        if (busyRef.current) return;
        setCurriculumOpen(false);
        if (conceptId) {
          updateRoadmap({
            ...roadmap,
            conversationIds: {
              ...roadmap.conversationIds,
              [conceptId]: crypto.randomUUID().replaceAll("-", ""),
            },
          });
        } else {
          try {
            setFreeId(newConversation());
            setNotice("");
          } catch {
            setNotice(
              "새 학습을 저장하지 못했습니다. 브라우저 저장 공간을 확인해 주세요.",
            );
          }
        }
      }}
    >
      {notice && (
        <div className="app-notice error" role="alert">
          {notice}
        </div>
      )}
      <div className="curriculum-container" hidden={!curriculumOpen}>
        <CurriculumDraft />
      </div>
      <div hidden={curriculumOpen}>
        {isRoadmap ? (
          <Roadmap
            state={roadmap}
            onSelect={selectConcept}
            onStatusChange={updateStatus}
            onStart={startConcept}
          />
        ) : (
          <ChatSession
            key={conceptConversation || freeId}
            conversationId={conceptConversation || freeId}
            concept={route.concept}
            learningStatus={
              conceptId
                ? roadmap.statuses[conceptId] || "not-started"
                : "not-started"
            }
            onStatusChange={(status) => {
              if (conceptId) updateStatus(conceptId, status);
            }}
            onBusyChange={(value) => {
              busyRef.current = value;
              setBusy(value);
            }}
          />
        )}
      </div>
    </LearningShell>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
