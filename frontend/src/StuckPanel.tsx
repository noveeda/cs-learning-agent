// 파일 역할: [막혔어요]·붙여넣기 진입 버튼과 막힘 패널; 엔진이 만든 view.kind별 화면(진단·결과·보충·확인·복귀)을 그리고 입력을 한 단계씩 보냄
import { useEffect, useRef, useState, type ReactNode } from "react";
import Markdown from "react-markdown";
import { LoaderCircle, RotateCcw, X } from "lucide-react";
import type { Concept } from "./roadmap";
import { remediationParts } from "./animation";
import { AnimationPlayer } from "./AnimationPlayer";
import {
  canRetry,
  createSession,
  entryKey,
  finishStuckSession,
  getSession,
  isFinished,
  rememberStuckSession,
  stepSession,
  StuckError,
  type Entry,
  type ItemView,
  type StepInput,
  type StuckSession,
} from "./stuck";
import "./stuck.css";

// 역할: 패널을 여는 방법; 진행 중 세션 재개·새 세션 시작·붙여넣기 입력부터 시작
export type StuckRequest =
  | { mode: "resume"; key: string; id: string }
  | { mode: "start"; entry: Entry }
  | { mode: "paste"; returnTo: string };

const excerptLimit = 2000;

const evidenceLabels = {
  known: "맞힘",
  gap: "빈틈",
  self_known: "이미 안다고 표시",
  // 엔진은 진단 문항이 없는 개념을 판단하지 않고 no_items로만 기록
  no_items: "진단 문항 없음",
};

const outcomeLabels: Record<string, string> = {
  passed: "이해 확인 통과",
  unresolved: "미해결",
  unchecked: "확인 문항 없이 넘어감",
  pending: "진행 전",
};

// 역할: 개념 학습 화면·로드맵 상세의 [막혔어요]; 확인·서술 문항이 아직 없는 개념은 버튼 대신 안내 문구
export function StuckButton({
  concept,
  inProgress,
  onStuck,
}: {
  concept: Concept;
  inProgress: boolean;
  onStuck: () => void;
}) {
  if (!concept.stuckSupported)
    return (
      <p className="stuck-unsupported">
        아직 [막혔어요]를 지원하지 않는 개념이에요. 이해 확인 문항이 준비된
        개념부터 차례로 열고 있어요.
      </p>
    );
  return (
    <button className="button secondary stuck-button" onClick={onStuck}>
      {inProgress ? "막힘 이어서 풀기" : "막혔어요"}
    </button>
  );
}

// 역할: 문항 형식에 맞는 답 입력(선택형 보기·단답·서술)과 제출; 같은 단계에서 재시도해도 입력값을 유지
function AnswerForm({
  item,
  disabled,
  submitLabel,
  onSubmit,
  children,
}: {
  item: ItemView;
  disabled: boolean;
  submitLabel: string;
  onSubmit: (answer: string) => void;
  children?: ReactNode;
}) {
  const [answer, setAnswer] = useState("");
  return (
    <form
      className="stuck-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (answer.trim()) onSubmit(answer);
      }}
    >
      <p className="stuck-question" id={`question-${item.id}`}>
        {item.question}
      </p>
      {item.options ? (
        <div
          className="stuck-options"
          role="radiogroup"
          aria-labelledby={`question-${item.id}`}
        >
          {Object.entries(item.options).map(([label, text]) => (
            <label key={label}>
              <input
                type="radio"
                name={item.id}
                value={label}
                checked={answer === label}
                disabled={disabled}
                onChange={() => setAnswer(label)}
              />
              <span>
                {label}. {text}
              </span>
            </label>
          ))}
        </div>
      ) : item.format === "explanation" ? (
        <textarea
          aria-labelledby={`question-${item.id}`}
          value={answer}
          maxLength={2000}
          rows={6}
          disabled={disabled}
          placeholder="내 말로 설명해 보세요"
          onChange={(event) => setAnswer(event.target.value)}
        />
      ) : (
        <input
          className="stuck-input"
          aria-labelledby={`question-${item.id}`}
          value={answer}
          maxLength={200}
          disabled={disabled}
          placeholder="답을 입력하세요"
          onChange={(event) => setAnswer(event.target.value)}
        />
      )}
      <div className="stuck-actions">
        <button
          className="button primary"
          disabled={disabled || !answer.trim()}
        >
          {submitLabel}
        </button>
        {children}
      </div>
    </form>
  );
}

// 역할: 막힘 세션 하나를 열어 단계별 화면을 그림
// - 세션 ID는 처음 받은 순간 브라우저에 저장하고, 끝나면 진행 중 목록에서 뺌(새로고침 재개 기준)
// - 요청은 한 번에 하나만 보냄(inFlight): 연속 클릭으로 같은 단계를 두 번 진행하지 않게 함
export function StuckPanel({
  request,
  concepts,
  onClose,
  onReturn,
  onSessionsChange,
}: {
  request: StuckRequest;
  concepts: Concept[];
  onClose: () => void;
  onReturn: (returnTo: string) => void;
  onSessionsChange: () => void;
}) {
  const [session, setSession] = useState<StuckSession | null>(null);
  const [excerpt, setExcerpt] = useState("");
  // 붙여넣기 입력창을 보여 줄 복귀 위치; 붙여넣기로 열었거나 붙여넣기 세션을 처음부터 다시 할 때 설정
  const [pasteTo, setPasteTo] = useState(
    request.mode === "paste" ? request.returnTo : null,
  );
  // "처음부터 다시" 확인 단계; 한 번 더 눌러야 진행을 버려 실수로 지우는 일을 막음
  const [confirmRestart, setConfirmRestart] = useState(false);
  const [pending, setPending] = useState("");
  const [error, setError] = useState<{
    message: string;
    retry?: () => void;
  } | null>(null);
  const [notice, setNotice] = useState("");
  const inFlight = useRef(false);
  const started = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const alert = useRef<HTMLDivElement>(null);
  const titleOf = (id: string) =>
    concepts.find((concept) => concept.id === id)?.title ?? id;

  // 역할: 받은 세션을 화면에 반영하고 진행 중 목록 저장·정리; 저장 실패는 진행을 막지 않고 안내만 함
  function accept(next: StuckSession) {
    setSession(next);
    const key = entryKey(next.entry);
    try {
      if (isFinished(next.stage)) finishStuckSession(key);
      else rememberStuckSession(key, next.id);
      setNotice("");
    } catch {
      setNotice(
        "진행 상황을 브라우저에 저장하지 못했어요. 새로고침하면 이어서 하지 못할 수 있어요. 브라우저 저장 공간과 사이트 데이터 권한을 확인해 주세요.",
      );
    }
    onSessionsChange();
  }

  // 역할: 요청 하나를 실행하고 오류 종류별로 안내
  // - 일시적 실패(502·503·504·연결)는 같은 요청(같은 version)을 다시 보내는 버튼 제공; 화면 상태는 그대로 둠
  // - 404는 저장된 세션이 사라진 것이므로 진행 중 목록에서 빼고 다시 시작하도록 안내
  async function run(
    label: string,
    action: () => Promise<{ session: StuckSession; reloaded?: boolean }>,
  ) {
    if (inFlight.current) return;
    inFlight.current = true;
    setPending(label);
    setError(null);
    try {
      const { session: next, reloaded } = await action();
      accept(next);
      if (reloaded)
        setNotice(
          "다른 화면에서 이미 진행된 단계가 있어 최신 화면을 불러왔어요.",
        );
    } catch (failure) {
      const message =
        failure instanceof StuckError
          ? failure.message
          : "막힘 세션을 처리하지 못했어요. 다시 시도해 주세요.";
      if (failure instanceof StuckError && failure.status === 404) {
        if (request.mode === "resume") {
          try {
            finishStuckSession(request.key);
          } catch {
            // 목록 정리에 실패해도 안내는 보여 줌
          }
          onSessionsChange();
        }
        setError({ message: `${message} 패널을 닫고 다시 시작해 주세요.` });
      } else {
        setError({
          message,
          retry: canRetry(failure) ? () => run(label, action) : undefined,
        });
      }
    } finally {
      inFlight.current = false;
      setPending("");
    }
  }

  // 역할: 현재 세션에 입력 하나를 보냄; LLM 단계는 오래 걸릴 수 있어 진행 문구를 함께 넘김
  function send(input: StepInput, label: string) {
    if (session) run(label, () => stepSession(session, input));
  }

  // 역할: 붙여넣은 문장으로 새 세션 시작; return_to는 패널을 연 학습 화면
  function startExcerpt(returnTo: string) {
    const text = excerpt.trim();
    if (!text) return;
    run(
      "붙여넣은 문장이 어떤 개념인지 찾고 있어요. 수십 초 걸릴 수 있어요.",
      async () => ({
        session: await createSession({
          kind: "excerpt",
          excerpt: text,
          return_to: returnTo,
        }),
      }),
    );
  }

  // 역할: "처음부터 다시"(확인 후); 브라우저의 진행 중 목록에서만 빼고 서버 세션 파일은 그대로 둠
  // - 개념 진입은 같은 entry로 바로 새 세션을 시작
  // - 붙여넣기는 다른 문장으로 바꿀 수 있게 이전 문장을 채운 입력창으로 돌아감
  function restart() {
    if (!session || inFlight.current) return;
    const entry = session.entry;
    setConfirmRestart(false);
    try {
      finishStuckSession(entryKey(entry));
    } catch {
      // 목록에서 빼지 못해도 새 세션을 받으면 같은 키를 새 ID로 덮어씀
    }
    onSessionsChange();
    setSession(null);
    setNotice("");
    setError(null);
    if (entry.kind === "excerpt") {
      setExcerpt(entry.excerpt);
      setPasteTo(entry.return_to);
    } else
      run("진단 문항을 준비하고 있어요.", async () => ({
        session: await createSession(entry),
      }));
  }

  useEffect(() => {
    heading.current?.focus();
    // StrictMode의 effect 이중 실행에서 세션이 두 개 만들어지지 않도록 한 번만 시작
    if (started.current) return;
    started.current = true;
    if (request.mode === "resume")
      run("저장된 단계를 불러오고 있어요.", async () => ({
        session: await getSession(request.id),
      }));
    if (request.mode === "start")
      run("진단 문항을 준비하고 있어요.", async () => ({
        session: await createSession(request.entry),
      }));
    // 패널은 요청마다 key로 새로 만들어지므로 처음 한 번만 실행
  }, []);

  // 다음 단계 화면은 위에서부터 읽게 함; 같은 패널 안에서 이전 단계의 스크롤 위치가 남지 않도록 처리
  useEffect(() => {
    scroller.current?.scrollTo({ top: 0 });
  }, [session?.version]);
  // 오류는 긴 화면 아래쪽에 생기므로 안내와 다시 시도 버튼이 보이도록 스크롤
  useEffect(() => {
    if (error) alert.current?.scrollIntoView({ block: "nearest" });
  }, [error]);

  const busy = Boolean(pending);
  const view = session?.view;
  const continueLabel =
    "다음 단계를 준비하고 있어요. 설명을 만드는 단계는 수십 초 걸릴 수 있어요.";

  // 역할: view.kind별 본문; 정답 키는 받지 않으므로 정답을 표시하지 않고 학습자 답과 판정만 보여 줌
  function body() {
    if (!session || !view) {
      if (pasteTo === null) return null;
      return (
        <form
          className="stuck-form"
          onSubmit={(event) => {
            event.preventDefault();
            startExcerpt(pasteTo);
          }}
        >
          <label className="stuck-question" htmlFor="stuck-excerpt">
            막힌 문장이나 문단을 붙여넣어 주세요. 어떤 개념에서 막혔는지 찾아
            빈틈을 진단해요.
          </label>
          <textarea
            id="stuck-excerpt"
            value={excerpt}
            maxLength={excerptLimit}
            rows={7}
            disabled={busy}
            onChange={(event) => setExcerpt(event.target.value)}
          />
          <div className="stuck-actions">
            <span className="stuck-count">
              {excerpt.length}/{excerptLimit}자
            </span>
            <button
              className="button primary"
              disabled={busy || !excerpt.trim()}
            >
              이 문장으로 시작
            </button>
          </div>
        </form>
      );
    }
    // 같은 단계에서 실패·재시도할 때는 입력을 유지하고, 다음 단계로 가면 새 입력창으로 바꿈
    const formKey = `${session.version}`;
    switch (view.kind) {
      case "question":
        return view.purpose === "diagnosis" ? (
          <section>
            <span className="badge">진단 문항 · {view.concept.title}</span>
            <p className="stuck-help">
              막힌 개념의 선수 개념을 짧게 확인해요. 답을 맞히면 그 아래로는
              묻지 않아요.
            </p>
            <AnswerForm
              key={formKey}
              item={view.item}
              disabled={busy}
              submitLabel="답 제출"
              onSubmit={(answer) =>
                send(
                  { type: "answer", item: view.item.id, answer },
                  "답을 확인하고 있어요.",
                )
              }
            >
              <button
                type="button"
                className="button secondary"
                disabled={busy}
                onClick={() =>
                  send(
                    { type: "skip_known", item: view.item.id },
                    "다음 문항을 고르고 있어요.",
                  )
                }
              >
                이미 알아요
              </button>
              <button
                type="button"
                className="button secondary"
                disabled={busy}
                onClick={() =>
                  send(
                    { type: "opt_out" },
                    "진단을 마치고 보충 순서를 정하고 있어요.",
                  )
                }
              >
                바로 설명
              </button>
            </AnswerForm>
          </section>
        ) : (
          <section>
            <span className="badge">
              이해 확인 · 내 말로 설명하기 · {view.concept.title}
            </span>
            <AnswerForm
              key={formKey}
              item={view.item}
              disabled={busy}
              submitLabel="설명 제출"
              onSubmit={(answer) =>
                send(
                  { type: "answer", item: view.item.id, answer },
                  "설명을 채점하고 있어요. 수십 초 걸릴 수 있어요.",
                )
              }
            />
          </section>
        );
      case "diagnosis_result":
        return (
          <section>
            <span className="badge">진단 결과 · {view.target.title}</span>
            {view.opted_out && (
              <p className="stuck-help">
                진단을 멈추고 바로 설명으로 넘어가요. 이미 답한 문항의 결과만
                반영했어요.
              </p>
            )}
            <h3>보충할 빈틈</h3>
            {view.gaps.length ? (
              <ol className="stuck-gaps">
                {view.gaps.map((gap) => (
                  <li key={gap.id}>{gap.title}</li>
                ))}
              </ol>
            ) : (
              <p className="stuck-help">선수 개념에서 빈틈을 찾지 못했어요.</p>
            )}
            <p className="stuck-help">
              빈틈은 선수 개념부터 차례로 보충하고, 마지막에 {view.target.title}
              을(를) 다시 확인해요.
            </p>
            {view.evidence.length > 0 && (
              <>
                <h3>이렇게 판단했어요</h3>
                <ul className="stuck-evidence">
                  {view.evidence.map((row) => (
                    <li key={row.concept} data-result={row.result}>
                      <div className="stuck-evidence-heading">
                        <strong>{titleOf(row.concept)}</strong>
                        <span className={`stuck-result ${row.result}`}>
                          {evidenceLabels[row.result]}
                        </span>
                      </div>
                      {row.question && <p>{row.question}</p>}
                      {row.options && (
                        <ul className="stuck-evidence-options">
                          {Object.entries(row.options).map(([label, text]) => (
                            <li
                              key={label}
                              data-chosen={row.answer === label || undefined}
                            >
                              {label}. {text}
                              {row.answer === label && " ← 내 답"}
                            </li>
                          ))}
                        </ul>
                      )}
                      {row.answer !== null && !row.options && (
                        <p>내 답: {row.answer}</p>
                      )}
                    </li>
                  ))}
                </ul>
              </>
            )}
            <div className="stuck-actions">
              <button
                className="button primary"
                disabled={busy}
                onClick={() => send({ type: "continue" }, continueLabel)}
              >
                보충 시작
              </button>
            </div>
          </section>
        );
      case "remediation": {
        const { text, animation } = remediationParts(view);
        return (
          <section>
            <span className="badge">
              보충 설명 · {view.concept.title}
              {view.is_target ? " (막힌 개념)" : ""}
              {view.attempt > 1 ? ` · ${view.attempt}번째 설명` : ""}
            </span>
            <div className="message-content stuck-text">
              <Markdown>{text}</Markdown>
            </div>
            {/* 다시 설명(attempt)이나 다른 개념으로 넘어가면 첫 단계부터 보여 주도록 key를 바꿈 */}
            {animation && (
              <AnimationPlayer
                key={`${view.concept.id}-${view.attempt}`}
                animation={animation}
              />
            )}
            {view.item ? (
              <>
                <h3>확인 문항</h3>
                <AnswerForm
                  key={formKey}
                  item={view.item}
                  disabled={busy}
                  submitLabel="답 제출"
                  onSubmit={(answer) =>
                    send(
                      { type: "answer", item: view.item!.id, answer },
                      "답을 확인하고 있어요.",
                    )
                  }
                />
              </>
            ) : (
              <>
                {view.note && <p className="stuck-help">{view.note}</p>}
                <div className="stuck-actions">
                  <button
                    className="button primary"
                    disabled={busy}
                    onClick={() => send({ type: "continue" }, continueLabel)}
                  >
                    계속
                  </button>
                </div>
              </>
            )}
          </section>
        );
      }
      case "result":
        return (
          <section>
            <span className="badge">이해 확인 결과 · {view.concept.title}</span>
            <h3>
              {view.passed
                ? "이해 확인을 통과했어요."
                : "아직 확인을 통과하지 못했어요."}
            </h3>
            {view.rubric_met && (
              <p className="stuck-help">
                서술 기준 {view.rubric_met.filter(Boolean).length}/
                {view.rubric_met.length}개를 충족했어요.
              </p>
            )}
            {!view.passed && (
              <p className="stuck-help">
                적용 문항과 서술을 모두 통과해야 이해한 것으로 봐요. 다른
                방식으로 다시 설명해 드릴게요.
              </p>
            )}
            <div className="stuck-actions">
              <button
                className="button primary"
                disabled={busy}
                onClick={() => send({ type: "continue" }, continueLabel)}
              >
                {view.passed ? "다음" : "다시 설명 듣기"}
              </button>
            </div>
          </section>
        );
      case "unresolved":
        return (
          <section>
            <span className="badge">미해결 · {view.concept.title}</span>
            <p className="stuck-help">
              여러 번 설명했지만 아직 확인을 통과하지 못했어요. 미해결로
              기록하고 다음으로 넘어갈 수 있어요.
            </p>
            <div className="stuck-actions">
              <button
                className="button primary"
                disabled={busy}
                onClick={() => send({ type: "continue" }, continueLabel)}
              >
                다음으로
              </button>
            </div>
          </section>
        );
      case "next":
        return (
          <section>
            <p className="stuck-help">다음 단계로 넘어가요.</p>
            <div className="stuck-actions">
              <button
                className="button primary"
                disabled={busy}
                onClick={() => send({ type: "continue" }, continueLabel)}
              >
                계속
              </button>
            </div>
          </section>
        );
      case "summary":
        return (
          <section>
            <span className="badge">복귀 요약</span>
            <div className="message-content stuck-text">
              <Markdown>{view.text}</Markdown>
            </div>
            <ul className="stuck-results">
              {view.results.map((row) => (
                <li key={row.concept.id}>
                  <strong>{row.concept.title}</strong>
                  <span>{outcomeLabels[row.outcome] ?? row.outcome}</span>
                </li>
              ))}
            </ul>
            <div className="stuck-actions">
              <button
                className="button primary"
                onClick={() => onReturn(view.return_to)}
              >
                원래 학습으로 돌아가기
              </button>
            </div>
          </section>
        );
      case "out_of_scope":
        return (
          <section>
            <span className="badge">지원 범위 밖</span>
            <p className="stuck-help">
              붙여넣은 내용에서 막힘 풀기를 지원하는 개념을 찾지 못했어요.
              문항이 준비된 개념만 막힘 풀기를 지원해요. 문장을 바꿔 다시
              붙여넣거나 학습 대화에서 질문해 주세요.
            </p>
            <div className="stuck-actions">
              <button
                className="button primary"
                onClick={() => onReturn(view.return_to)}
              >
                원래 학습으로 돌아가기
              </button>
            </div>
          </section>
        );
    }
  }

  return (
    <aside className="card stuck-panel" aria-label="막힘 풀기">
      <div className="stuck-header">
        <h2 ref={heading} tabIndex={-1}>
          막힘 풀기
        </h2>
        <button
          className="icon-button"
          aria-label="막힘 패널 닫기"
          onClick={onClose}
        >
          <X size={18} aria-hidden="true" />
        </button>
      </div>
      <div className="stuck-body" ref={scroller}>
        {notice && (
          <p className="stuck-notice" role="status">
            {notice}
          </p>
        )}
        {body()}
        {pending && (
          <div className="status" role="status">
            <LoaderCircle aria-hidden="true" size={15} className="spin" />
            {pending}
          </div>
        )}
        {error && (
          <div className="error" role="alert" ref={alert}>
            {error.message}
            {error.retry && (
              <button
                className="button secondary stuck-retry"
                onClick={error.retry}
              >
                <RotateCcw size={14} aria-hidden="true" />
                다시 시도
              </button>
            )}
          </div>
        )}
        {session && !isFinished(session.stage) && (
          <div className="stuck-restart">
            <p className="stuck-footnote">
              진행 상황은 단계마다 저장돼요. 패널을 닫거나 새로고침해도 같은
              단계에서 이어서 할 수 있어요.
            </p>
            {confirmRestart ? (
              <div
                className="stuck-confirm"
                role="group"
                aria-label="처음부터 다시 시작할까요?"
              >
                <p>
                  지금까지의 진단·보충 진행을 버리고 처음부터 다시 시작할까요?
                  버린 진행은 이어서 할 수 없어요.
                </p>
                <div className="stuck-actions">
                  <button
                    className="button primary"
                    disabled={busy}
                    onClick={restart}
                  >
                    다시 시작
                  </button>
                  <button
                    className="button secondary"
                    onClick={() => setConfirmRestart(false)}
                  >
                    취소
                  </button>
                </div>
              </div>
            ) : (
              <button
                className="button secondary"
                disabled={busy}
                onClick={() => setConfirmRestart(true)}
              >
                <RotateCcw size={14} aria-hidden="true" />
                처음부터 다시
              </button>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}
