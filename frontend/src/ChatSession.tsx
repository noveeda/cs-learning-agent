/**
 * @file
 * @brief 대화 한 세션의 복구·질문 입력·실시간 답변·도식/애니메이션/교재 그림 표시와 실패 후 재시도, 막힘 진입 버튼 담당
 */
import { useEffect, useRef, useState } from "react";
import { LearningMarkdown } from "./markdown";
import {
  ArrowUp,
  ArrowUpRight,
  PanelRightOpen,
  Check,
  LoaderCircle,
  Layers,
  X,
} from "lucide-react";
import {
  artifactNoun,
  artifactTitle,
  harnessProgress,
  isAnimationArtifact,
  requestChat,
  loadConversation,
  toolProgress,
  type AnimationArtifact,
  type ChatArtifact,
  type Message,
  type AgentEvent,
} from "./api";
import { AgentNotes } from "./LearningShell";
import { AnimationPlayer } from "./AnimationPlayer";
import { DiagramPanel } from "./DiagramPanel";
import { StuckButton } from "./StuckPanel";
import { statusLabels, type Concept, type LearningStatus } from "./roadmap";

/**
 * @brief 애니메이션 artifact 패널; 도식 패널과 같은 자리·닫기 동작으로 재생기를 보여 줌
 * - SVG 파일이 없으므로 확대·저장 대신 단계 이동만 둠
 * - artifact가 바뀌면 key로 재생기를 새로 만들어 첫 단계부터 보여 줌
 */
function AnimationPanel({
  artifact,
  onClose,
}: {
  artifact: AnimationArtifact;
  onClose: () => void;
}) {
  return (
    <aside className="artifact-panel card" aria-label="생성된 애니메이션">
      <div className="panel-header">
        <div>
          <span className="eyebrow">학습 애니메이션</span>
          <h2>{artifact.animation.title}</h2>
        </div>
        <button
          className="icon-button"
          aria-label="애니메이션 닫기"
          onClick={onClose}
        >
          <X size={20} aria-hidden="true" />
        </button>
      </div>
      <div className="diagram-stage">
        <AnimationPlayer
          key={artifact.artifact_id}
          animation={artifact.animation}
        />
      </div>
    </aside>
  );
}

/**
 * @brief 대화 ID에 해당하는 기록을 복구하고 질문·도식·학습 상태 UI 구성
 * - 막힘 패널은 App이 대화 옆에 띄우므로 여기서는 진입 버튼만 두고 대화 상태는 건드리지 않음
 */
export function ChatSession({
  conversationId,
  concept,
  learningStatus,
  stuckInProgress,
  onStatusChange,
  onBusyChange,
  onStuck,
  onPaste,
}: {
  conversationId: string;
  concept?: Concept;
  learningStatus: LearningStatus;
  stuckInProgress: boolean;
  onStatusChange: (status: LearningStatus) => void;
  onBusyChange: (busy: boolean) => void;
  onStuck: () => void;
  onPaste: () => void;
}) {
  const [loading, setLoading] = useState(true);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState(
    concept ? `${concept.title} 개념을 예시와 함께 설명해줘` : "",
  );
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [contextNotice, setContextNotice] = useState("");
  // 활성 artifact; 다음 질문에 artifact_id로 보내 수정 요청 대상이 됨(도식·애니메이션 공통)
  const [artifact, setArtifact] = useState<ChatArtifact | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const inFlight = useRef(false);
  useEffect(() => {
    // 느린 이전 조회 응답이 새 세션 상태를 덮어쓰지 않도록 effect 정리 여부 확인
    let cancelled = false;
    setLoading(true);
    setError("");
    loadConversation(conversationId)
      .then((restored) => {
        if (cancelled) return;
        setMessages(restored);
        if (restored.length) setQuestion("");
        const latest = restored.filter((m) => m.artifact).at(-1)?.artifact;
        setArtifact(latest || null);
        setPanelOpen(Boolean(latest));
        setLoading(false);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId, loadAttempt]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, status, draft]);

  /** @brief 도식·애니메이션 패널을 닫고 다음 질문을 바로 입력하도록 초점을 입력창으로 돌려줌 */
  const closePanel = () => {
    setPanelOpen(false);
    requestAnimationFrame(() => input.current?.focus());
  };

  /** @brief 중복 전송을 막고 진행 이벤트와 최종 답변 반영; 실패 시 질문과 부분 답변 보존 */
  async function send(text: string) {
    const value = text.trim();
    if (!value || inFlight.current || loading) return;
    // React 상태 갱신 전에 연속 클릭이 들어올 수 있으므로 ref로 즉시 잠금
    inFlight.current = true;
    setMessages((old) => [...old, { role: "user", content: value }]);
    let partialAnswer = "";
    setDraft("");
    setQuestion("");
    setBusy(true);
    onBusyChange(true);
    setError("");
    setStatus("질문을 살펴보고 있어요");
    /** @brief 에이전트 이벤트를 답변 조각·도식·사용자 진행 안내로 변환 */
    function showProgress(event: AgentEvent) {
      if (event.type === "context")
        setContextNotice(
          event.dropped_messages
            ? `대화는 모두 저장돼 있어요. 이번 답변에는 토큰 한도에 맞는 최근 대화${artifact ? `와 활성 ${artifactNoun(artifact)}을` : "를"} 사용해요.`
            : "",
        );
      if (event.type === "text_start" || event.type === "tool_started") {
        partialAnswer = "";
        setDraft("");
      }
      if (event.type === "text_delta") {
        partialAnswer += event.text;
        setDraft(partialAnswer);
        setStatus("답변을 작성하고 있어요");
      }
      // 결과가 도식인지 애니메이션인지는 도구 이름으로만 미리 알 수 있음
      if (event.type === "tool_started")
        setStatus(toolProgress(event.name, false));
      if (event.type === "tool_error")
        setStatus(toolProgress(event.name, true));
      // 근거 하네스는 도구 대신 단계(status)를 알려 줌; 모르는 단계면 직전 문구를 그대로 둠
      if (event.type === "status") {
        const label = harnessProgress(event);
        if (label) setStatus(label);
      }
      if (event.type === "artifact") {
        setArtifact(event);
        setPanelOpen(true);
        setStatus("그림에 맞춰 설명을 정리하고 있어요");
      }
    }
    try {
      const { answer, artifact: generated } = await requestChat(
        value,
        conversationId,
        showProgress,
        artifact?.artifact_id,
      );
      setMessages((old) => [
        ...old,
        { role: "assistant", content: answer, artifact: generated },
      ]);
    } catch (err) {
      setError(
        err instanceof Error && err.name !== "AbortError"
          ? err.message
          : "응답 대기 시간이 초과됐습니다. 다시 시도해 주세요.",
      );
      setMessages((old) => [
        ...old.map((m, i) =>
          i === old.length - 1 ? { ...m, failed: true } : m,
        ),
        ...(partialAnswer
          ? [
              {
                role: "assistant" as const,
                content: partialAnswer,
                failed: true,
              },
            ]
          : []),
      ]);
      // 실패한 질문을 복원해 사용자가 다시 입력하지 않고 재시도할 수 있도록 처리
      setQuestion(value);
    } finally {
      setDraft("");
      inFlight.current = false;
      setBusy(false);
      onBusyChange(false);
      setStatus("");
      input.current?.focus();
    }
  }
  const suggestions = concept
    ? [
        `${concept.title} 개념을 예시와 함께 설명해줘`,
        `${concept.title} 핵심을 도식으로 보여줘`,
        `${concept.title}에서 자주 헷갈리는 점을 알려줘`,
      ]
    : [
        "가상 메모리와 페이징을 그림으로 설명해줘",
        "BFS와 DFS는 어떻게 달라?",
        "TCP 연결 과정을 도식으로 보여줘",
      ];
  return (
    <main className="learning-layout">
      <section className="conversation card" aria-label="학습 대화">
        <div className="conversation-heading">
          <div>
            <span className="badge neutral">
              {concept ? concept.domain : "자유 학습"}
            </span>
            <h2>
              {concept?.title ||
                (messages.length ? "함께 알아보는 개념" : "질문에서 이해로")}
            </h2>
          </div>
          {concept && (
            <label className="status-control">
              학습 상태
              <select
                aria-label="학습 상태"
                value={learningStatus}
                onChange={(e) =>
                  onStatusChange(e.target.value as LearningStatus)
                }
              >
                {Object.entries(statusLabels).map(([value, label]) => (
                  <option value={value} key={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          )}
          {artifact && !panelOpen && (
            <button
              className="button secondary"
              onClick={() => setPanelOpen(true)}
            >
              <PanelRightOpen size={16} aria-hidden="true" />
              {artifactNoun(artifact)} 열기
            </button>
          )}
        </div>
        <div className="stuck-entry">
          {concept && (
            <StuckButton
              concept={concept}
              inProgress={stuckInProgress}
              onStuck={onStuck}
            />
          )}
          <button className="button secondary" onClick={onPaste}>
            막힌 문장 붙여넣기
          </button>
        </div>
        <div className="scroll-area">
          {messages.length === 0 ? (
            <div className="welcome">
              <span className="badge">개념 탐색</span>
              <h3>
                {concept ? (
                  concept.title
                ) : (
                  <>
                    어떤 개념을
                    <br />
                    함께 풀어볼까요?
                  </>
                )}
              </h3>
              <p>
                {concept ? (
                  concept.description
                ) : (
                  <>
                    질문에서 이해로.
                    <br className="mobile-break" /> 복잡한 개념은 그림으로
                    연결해 보세요.
                  </>
                )}
              </p>
              <div className="suggestions">
                {suggestions.map((s) => (
                  <button
                    key={s}
                    disabled={busy || loading}
                    onClick={() => send(s)}
                  >
                    {s}
                    <ArrowUpRight aria-hidden="true" size={16} />
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="messages" role="log" aria-label="대화 기록">
              {messages.map((m, i) => (
                <article className={`message ${m.role}`} key={i}>
                  {m.role === "assistant" && (
                    <div className="assistant-label">
                      <Layers aria-hidden="true" size={16} /> CS Studio
                    </div>
                  )}
                  {m.failed && (
                    <p role="status">
                      완료되지 않은 문답 · 다음 답변의 대화 기록에서 제외됩니다.
                    </p>
                  )}
                  <div className="message-content">
                    {m.role === "assistant" ? (
                      <LearningMarkdown>{m.content}</LearningMarkdown>
                    ) : (
                      m.content
                    )}
                  </div>
                  {m.artifact && (
                    <button
                      className="artifact-chip"
                      onClick={() => {
                        setArtifact(m.artifact!);
                        setPanelOpen(true);
                      }}
                    >
                      <Check aria-hidden="true" size={14} />
                      {artifactTitle(m.artifact)}
                      <ArrowUpRight aria-hidden="true" size={14} />
                    </button>
                  )}
                </article>
              ))}
            </div>
          )}
          {busy && draft && (
            <article className="message assistant streaming">
              <div className="assistant-label">
                <Layers aria-hidden="true" size={16} /> CS Studio
              </div>
              <div className="message-content">
                <LearningMarkdown>{draft}</LearningMarkdown>
              </div>
            </article>
          )}
          {busy && (
            <div className="status" role="status">
              <LoaderCircle aria-hidden="true" size={15} className="spin" />
              {status}
            </div>
          )}
          {error && (
            <div className="error" role="alert">
              {error}
              {loading && (
                <button
                  className="button secondary"
                  onClick={() => setLoadAttempt((value) => value + 1)}
                >
                  다시 불러오기
                </button>
              )}
              {artifact && (
                <span>{` 생성된 ${artifactNoun(artifact)}은 ${artifactNoun(artifact)} 패널에서 확인할 수 있습니다.`}</span>
              )}
            </div>
          )}
          <div ref={bottom} />
        </div>
        <div className="composer-area">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              send(question);
            }}
          >
            <label className="sr-only" htmlFor="question">
              질문
            </label>
            <textarea
              id="question"
              ref={input}
              value={question}
              maxLength={4000}
              disabled={busy || loading}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  send(question);
                }
              }}
              placeholder="궁금한 개념을 질문하세요"
              rows={2}
            />
            <div className="composer-footer">
              <span>
                <Layers aria-hidden="true" size={14} /> 필요할 때
                도식·애니메이션으로
              </span>
              <button
                className="send"
                aria-label="질문 보내기"
                disabled={busy || loading || !question.trim()}
              >
                {busy ? (
                  <LoaderCircle aria-hidden="true" size={20} className="spin" />
                ) : (
                  <ArrowUp aria-hidden="true" size={20} />
                )}
              </button>
            </div>
          </form>
          <p className="footnote">
            {loading ? "대화를 불러오는 중입니다. " : contextNotice} AI의
            설명에는 오류가 있을 수 있어요. 중요한 개념은 자료와 함께
            확인하세요.
          </p>
        </div>
      </section>
      {!(panelOpen && artifact) && (
        <AgentNotes
          hasMessages={messages.length > 0}
          disabled={busy || loading}
          onSuggest={(text) => {
            setQuestion(text);
            input.current?.focus();
          }}
        >
          <section className="card support-card">
            <h2>그림으로 연결해요</h2>
            <p>
              {artifact
                ? `생성한 ${artifactNoun(artifact)}을 다시 열어 설명과 함께 살펴보세요.`
                : "도식이나 애니메이션이 생성되면 이 공간에서 확인할 수 있어요."}
            </p>
            {artifact && (
              <button
                className="button secondary"
                onClick={() => setPanelOpen(true)}
              >
                <PanelRightOpen size={16} aria-hidden="true" />
                {artifactNoun(artifact)} 열기
              </button>
            )}
          </section>
          <section className="card support-card">
            <h2>이렇게 학습해 보세요</h2>
            <ol className="learning-steps">
              <li>궁금한 개념 질문하기</li>
              <li>답변과 도식 함께 살펴보기</li>
              <li>어려운 부분 다시 질문하기</li>
            </ol>
          </section>
        </AgentNotes>
      )}
      {panelOpen &&
        artifact &&
        (isAnimationArtifact(artifact) ? (
          <AnimationPanel artifact={artifact} onClose={closePanel} />
        ) : (
          <DiagramPanel
            artifact={artifact}
            onClose={closePanel}
            onImageError={() =>
              setError(`${artifactNoun(artifact)}을 불러오지 못했습니다. 서버 연결을 확인해 주세요.`)
            }
          />
        ))}
    </main>
  );
}
