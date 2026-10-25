// 파일 역할: React 진입점; 개념 지도 로딩·해시 경로·로드맵 상태·개념별 대화 ID·막힘 패널·커리큘럼 화면 연결
import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { loadConceptMap, newConversation } from "./api";
import { ChatSession } from "./ChatSession";
import { CurriculumDraft } from "./CurriculumDraft";
import { LearningShell } from "./LearningShell";
import { ConceptMapStatus, Roadmap } from "./Roadmap";
import { StuckPanel, type StuckRequest } from "./StuckPanel";
import { entryKey, loadStuckStore, saveStuckStore, type Entry } from "./stuck";
import {
  loadRoadmap,
  saveRoadmap,
  type Concept,
  type RoadmapState,
  type LearningStatus,
} from "./roadmap";
import "./style.css";
import "./roadmap.css";

// 역할: URL 해시에서 화면과 개념 ID 형식만 해석; 지도에 있는 개념인지는 지도를 받은 뒤 확인
function readRoute() {
  const hash = window.location.hash || "#/learn";
  const [path, query] = hash.slice(1).split("?");
  const conceptId = new URLSearchParams(query).get("concept") ?? "";
  return {
    page: path === "/roadmap" ? "roadmap" : "learn",
    conceptId:
      path === "/learn" && /^[a-z][a-z0-9-]{1,40}$/.test(conceptId)
        ? conceptId
        : undefined,
    hash,
  };
}

// 개념 지도 요청 상태: 로드맵과 개념별 학습 화면 모두 지도를 받아야 개념 제목·설명을 알 수 있음
type ConceptMapState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; concepts: Concept[] };

// 역할: 화면 간 상태를 조율하고 선택 개념의 대화와 학습 표시를 연결
function App() {
  const [curriculumOpen, setCurriculumOpen] = useState(false);
  const [roadmap, setRoadmap] = useState(loadRoadmap);
  const [conceptMap, setConceptMap] = useState<ConceptMapState>({
    status: "loading",
  });
  // 값이 바뀔 때마다 지도를 다시 요청; 다시 시도 버튼이 1씩 올림
  const [mapRequest, setMapRequest] = useState(0);
  const [route, setRoute] = useState(readRoute);
  // 진행 중 막힘 세션 목록(개념·진입별 세션 ID); 버튼을 "이어서 풀기"로 바꾸는 데 사용
  const [stuckSessions, setStuckSessions] = useState(
    () => loadStuckStore().sessions,
  );
  // 새로고침·재접속 전에 열려 있던 패널은 같은 세션으로 다시 열어 같은 단계에서 재개(M4)
  const [stuck, setStuck] = useState<StuckRequest | null>(() => {
    const { active, sessions } = loadStuckStore();
    return active
      ? { mode: "resume", key: active, id: sessions[active] }
      : null;
  });
  // 패널을 열 때마다 올려 key로 사용; 다른 개념의 막힘을 열면 이전 패널 상태를 남기지 않고 새로 만듦
  const [stuckOpening, setStuckOpening] = useState(0);
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
  useEffect(() => {
    // 늦게 끝난 이전 요청(StrictMode 이중 실행·연속 재시도)이 최신 결과를 덮지 않도록 무시 표시
    let ignore = false;
    setConceptMap({ status: "loading" });
    loadConceptMap().then(
      (concepts) => {
        if (!ignore) setConceptMap({ status: "ready", concepts });
      },
      (error: Error) => {
        if (!ignore) setConceptMap({ status: "error", message: error.message });
      },
    );
    return () => {
      ignore = true;
    };
  }, [mapRequest]);

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
  const concept =
    conceptMap.status === "ready"
      ? conceptMap.concepts.find((item) => item.id === route.conceptId)
      : undefined;
  const conceptId = concept?.id;
  // 역할: 막힘 패널을 엶; 같은 개념·진입의 진행 중 세션이 있으면 새로 만들지 않고 이어서 엶
  function openStuck(key: string, start: StuckRequest) {
    const store = loadStuckStore();
    const id = store.sessions[key];
    if (id) {
      try {
        saveStuckStore({ ...store, active: key });
      } catch {
        // 열림 표시를 못 남기면 새로고침 후 패널이 자동으로 열리지 않을 뿐 진행은 가능
      }
      setStuck({ mode: "resume", key, id });
    } else setStuck(start);
    setStuckOpening((count) => count + 1);
  }
  // 역할: 개념의 [막혔어요]; 복귀 위치는 버튼을 누른 화면의 해시 경로
  function stuckOnConcept(id: string) {
    const entry: Entry = {
      kind: "roadmap",
      concept: id,
      return_to: window.location.hash || "#/learn",
    };
    openStuck(entryKey(entry), { mode: "start", entry });
  }
  // 역할: 패널만 닫음; 세션은 진행 중 목록에 남아 다시 열면 이어서 함
  function closeStuck() {
    try {
      saveStuckStore({ ...loadStuckStore(), active: null });
    } catch {
      // 저장 실패 시 새로고침하면 패널이 다시 열릴 수 있지만 학습 진행에는 영향 없음
    }
    setStuck(null);
  }
  // 역할: 복귀 요약의 "원래 학습으로 돌아가기"; 대화는 패널 뒤에 그대로 있어 내용·스크롤이 유지됨
  function returnFromStuck(to: string) {
    closeStuck();
    if ((window.location.hash || "#/learn") !== to) window.location.hash = to;
  }
  const conceptConversation = conceptId
    ? roadmap.conversationIds[conceptId]
    : undefined;
  // 저장된 대화가 있는 개념 학습 주소는 지도를 받을 때까지 기다림; 제목 없이 대화를 열면 첫 질문이 비게 됨
  const waitingForMap = Boolean(
    route.conceptId &&
    conceptMap.status !== "ready" &&
    roadmap.conversationIds[route.conceptId],
  );
  // Direct links without a saved session open the roadmap so starting can be saved first.
  // 지도에 없는 개념 주소(옛 개념 ID 등)도 로드맵으로 보냄
  const isRoadmap =
    route.page === "roadmap" ||
    Boolean(route.conceptId && !waitingForMap && !conceptConversation);
  const mapStatus = (
    <ConceptMapStatus
      error={conceptMap.status === "error" ? conceptMap.message : undefined}
      onRetry={() => setMapRequest((count) => count + 1)}
    />
  );
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
        {waitingForMap ? (
          mapStatus
        ) : isRoadmap ? (
          conceptMap.status === "ready" ? (
            <Roadmap
              concepts={conceptMap.concepts}
              state={roadmap}
              onSelect={selectConcept}
              onStatusChange={updateStatus}
              onStart={startConcept}
              stuckSessions={stuckSessions}
              onStuck={(item) => stuckOnConcept(item.id)}
            />
          ) : (
            mapStatus
          )
        ) : (
          <ChatSession
            key={conceptConversation || freeId}
            conversationId={conceptConversation || freeId}
            concept={concept}
            stuckInProgress={Boolean(
              conceptId && stuckSessions[`roadmap:${conceptId}`],
            )}
            onStuck={() => {
              if (conceptId) stuckOnConcept(conceptId);
            }}
            onPaste={() => {
              const to = window.location.hash || "#/learn";
              openStuck(`excerpt:${to}`, { mode: "paste", returnTo: to });
            }}
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
      {stuck && (
        <StuckPanel
          key={stuckOpening}
          request={stuck}
          concepts={conceptMap.status === "ready" ? conceptMap.concepts : []}
          onClose={closeStuck}
          onReturn={returnFromStuck}
          onSessionsChange={() => setStuckSessions(loadStuckStore().sessions)}
        />
      )}
    </LearningShell>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
