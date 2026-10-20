import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import Markdown from "react-markdown";
import {
  ArrowUp,
  ArrowUpRight,
  PanelRightOpen,
  Check,
  LoaderCircle,
  Layers,
} from "lucide-react";
import {
  requestChat,
  loadConversation,
  newConversation,
  type Artifact,
  type Message,
  type AgentEvent,
} from "./api";
import { LearningShell, AgentNotes } from "./LearningShell";
import { DiagramPanel } from "./DiagramPanel";
import "./style.css";

function App() {
  const [conversationId, setConversationId] = useState(
    () => localStorage.getItem("cs-conversation") || newConversation(),
  );
  const [loading, setLoading] = useState(true);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [contextNotice, setContextNotice] = useState("");
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const inFlight = useRef(false);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    loadConversation(conversationId)
      .then((restored) => {
        if (cancelled) return;
        setMessages(restored);
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

  async function send(text: string) {
    const value = text.trim();
    if (!value || inFlight.current || loading) return;
    inFlight.current = true;
    setMessages((old) => [...old, { role: "user", content: value }]);
    let partialAnswer = "";
    setDraft("");
    setQuestion("");
    setBusy(true);
    setError("");
    setStatus("질문을 살펴보고 있어요");
    function showProgress(event: AgentEvent) {
      if (event.type === "context")
        setContextNotice(
          event.dropped_messages
            ? `대화는 모두 저장돼 있어요. 이번 답변에는 토큰 한도에 맞는 최근 대화와 활성 도식을 사용해요.`
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
      if (event.type === "tool_started")
        setStatus("개념을 도식으로 정리하고 있어요");
      if (event.type === "tool_error")
        setStatus("도식의 연결을 다시 확인하고 있어요");
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
      setQuestion(value);
    } finally {
      setDraft("");
      inFlight.current = false;
      setBusy(false);
      setStatus("");
      input.current?.focus();
    }
  }
  const suggestions = [
    "가상 메모리와 페이징을 그림으로 설명해줘",
    "BFS와 DFS는 어떻게 달라?",
    "TCP 연결 과정을 도식으로 보여줘",
  ];
  return (
    <LearningShell
      busy={busy || loading}
      onAsk={() => input.current?.focus()}
      onNew={() => {
        setLoading(true);
        setConversationId(newConversation());
        setContextNotice("");
        setMessages([]);
        setArtifact(null);
        setPanelOpen(false);
        setError("");
        setQuestion("");
        input.current?.focus();
      }}
    >
      <main className="learning-layout">
        <section className="conversation card" aria-label="학습 대화">
          <div className="conversation-heading">
            <div>
              <span className="badge neutral">자유 학습</span>
              <h2>
                {messages.length ? "함께 알아보는 개념" : "질문에서 이해로"}
              </h2>
            </div>
            {artifact && !panelOpen && (
              <button
                className="button secondary"
                onClick={() => setPanelOpen(true)}
              >
                <PanelRightOpen size={16} aria-hidden="true" />
                도식 열기
              </button>
            )}
          </div>
          <div className="scroll-area">
            {messages.length === 0 ? (
              <div className="welcome">
                <span className="badge">개념 탐색</span>
                <h3>
                  어떤 개념을
                  <br />
                  함께 풀어볼까요?
                </h3>
                <p>
                  질문에서 이해로.
                  <br className="mobile-break" /> 복잡한 개념은 그림으로 연결해
                  보세요.
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
                        완료되지 않은 문답 · 다음 답변의 대화 기록에서
                        제외됩니다.
                      </p>
                    )}
                    <div className="message-content">
                      {m.role === "assistant" ? (
                        <Markdown>{m.content}</Markdown>
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
                        {m.artifact.diagram.title}
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
                  <Markdown>{draft}</Markdown>
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
                  <span> 생성된 도식은 도식 패널에서 확인할 수 있습니다.</span>
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
                  <Layers aria-hidden="true" size={14} /> 필요할 때 도식으로
                </span>
                <button
                  className="send"
                  aria-label="질문 보내기"
                  disabled={busy || loading || !question.trim()}
                >
                  {busy ? (
                    <LoaderCircle
                      aria-hidden="true"
                      size={20}
                      className="spin"
                    />
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
                  ? "생성한 도식을 다시 열어 설명과 함께 살펴보세요."
                  : "도식이 생성되면 이 공간에서 확인할 수 있어요."}
              </p>
              {artifact && (
                <button
                  className="button secondary"
                  onClick={() => setPanelOpen(true)}
                >
                  <PanelRightOpen size={16} aria-hidden="true" />
                  도식 열기
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
        {panelOpen && artifact && (
          <DiagramPanel
            artifact={artifact}
            onClose={() => {
              setPanelOpen(false);
              requestAnimationFrame(() => input.current?.focus());
            }}
            onImageError={() =>
              setError("도식을 불러오지 못했습니다. 서버 연결을 확인해 주세요.")
            }
          />
        )}
      </main>
    </LearningShell>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
