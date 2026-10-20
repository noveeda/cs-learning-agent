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
  function selectConcept(id: string) {
    updateRoadmap({ ...roadmap, selectedId: id });
  }
  function updateStatus(id: string, status: LearningStatus) {
    updateRoadmap({
      ...roadmap,
      statuses: { ...roadmap.statuses, [id]: status },
    });
  }
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
