// 파일 역할: #/experiment 비교 실험 화면; 진행자 시작 화면과 참가자 단계 화면(사전 시험·학습 2회·사후 시험·설문)
// - 실험 중에는 로드맵·메뉴를 그리지 않고 단계 제목과 서버 기준 남은 시간만 보여 줌
// - 제안 시스템 조건은 기존 StuckPanel(붙여넣기 진입), 채팅 조건은 기존 ChatSession을 그대로 씀
// - 조건이 섞이지 않게 채팅 조건에는 막힘 진입을, 제안 시스템 조건에는 자유 채팅 입력을 두지 않음
import { useEffect, useMemo, useState } from "react";
import { loadConceptMap } from "./api";
import { ChatSession } from "./ChatSession";
import { StuckPanel, type StuckRequest } from "./StuckPanel";
import { loadStuckStore, saveStuckStore } from "./stuck";
import type { Concept } from "./roadmap";
import {
  EXPERIMENT_CONCEPTS,
  allAnswered,
  createExperiment,
  formatClock,
  getExperiment,
  loadExperimentId,
  nextStage,
  postEvent,
  remainingAt,
  saveExperimentId,
  stageTitles,
  submitPosttest,
  submitPretest,
  submitSurvey,
  validParticipant,
  type ExperimentItem,
  type ExperimentView,
  type NewExperiment,
} from "./experiment";
import "./experiment.css";

// 역할: 같은 사건을 새로고침 뒤에 다시 보내지 않도록, 기록에 성공한 사건을 세션·단계별로 브라우저에 남김
// - 보내는 중인 사건도 기억해 StrictMode의 effect 이중 실행·연속 클릭으로 두 번 보내지 않음
// - 실패하면 표시를 남기지 않아 다음 기회에 다시 보냄. 저장이 안 되는 환경이면 화면이 켜져 있는 동안만 기억
const sentInMemory = new Set<string>();
const sending = new Set<string>();
const sentKey = (key: string) => `cs-experiment-sent:${key}`;
function wasSent(key: string): boolean {
  try {
    if (localStorage.getItem(sentKey(key))) return true;
  } catch {
    // 아래 메모리 기록으로 판단
  }
  return sentInMemory.has(key);
}
async function sendOnce(key: string, action: () => Promise<boolean>) {
  if (wasSent(key) || sending.has(key)) return;
  sending.add(key);
  const ok = await action();
  sending.delete(key);
  if (!ok) return;
  sentInMemory.add(key);
  try {
    localStorage.setItem(sentKey(key), "1");
  } catch {
    // 메모리 기록만 남김
  }
}

// 역할: 학습 단계별 채팅 대화 id; 새로고침해도 같은 대화를 이어 보도록 브라우저에 보관
function chatConversationId(session: string, stage: string): string {
  const key = `cs-experiment-chat:${session}:${stage}`;
  try {
    const saved = localStorage.getItem(key);
    if (saved) return saved;
    const id = crypto.randomUUID().replaceAll("-", "");
    localStorage.setItem(key, id);
    return id;
  } catch {
    return crypto.randomUUID().replaceAll("-", "");
  }
}

// 역할: 실험 화면 전체; 세션이 없으면 진행자 시작 화면, 있으면 서버가 알려 준 단계 화면
export function ExperimentRunner() {
  const [id, setId] = useState(() => loadExperimentId());
  const [view, setView] = useState<ExperimentView | null>(null);
  const [error, setError] = useState("");
  const [concepts, setConcepts] = useState<Concept[]>([]);

  // 막힘 패널이 개념 이름을 보여 주는 데만 쓰므로 실패하면 개념 ID로 표시
  useEffect(() => {
    loadConceptMap().then(setConcepts, () => setConcepts([]));
  }, []);

  useEffect(() => {
    if (!id) return;
    let ignore = false;
    getExperiment(id).then(
      (next) => {
        if (!ignore) setView(next);
      },
      (failure) => {
        if (ignore) return;
        if (failure.status === 404) {
          saveExperimentId(null);
          setId(null);
          setError("저장된 실험 세션을 찾지 못했어요. 다시 시작해 주세요.");
        } else setError(failure.message);
      },
    );
    return () => {
      ignore = true;
    };
  }, [id]);

  // 역할: 진행할 세션을 정하고 새로고침 복구용으로 저장
  function open(next: ExperimentView | string) {
    const nextId = typeof next === "string" ? next : next.id;
    saveExperimentId(nextId);
    setError("");
    setId(nextId);
    setView(typeof next === "string" ? null : next);
  }

  // 역할: 단계 화면의 POST 뒤 다시 읽은 상태를 반영; 실패하면 화면을 그대로 두고 안내
  async function apply(action: () => Promise<ExperimentView>) {
    setError("");
    try {
      setView(await action());
    } catch (failure) {
      setError((failure as Error).message);
    }
  }

  const learning = view?.stage === "learn1" || view?.stage === "learn2";
  return (
    <div className="experiment">
      <header className="experiment-header">
        <div>
          <span className="eyebrow">
            비교 실험{view ? ` · ${view.participant}` : ""}
          </span>
          <h1>{view ? stageTitles[view.stage] : "실험 시작"}</h1>
        </div>
        {learning && view && (
          <Countdown
            key={`${view.stage}-${view.remaining_seconds}`}
            seconds={view.remaining_seconds ?? 0}
          />
        )}
      </header>
      {error && (
        <div className="experiment-error" role="alert">
          {error}
        </div>
      )}
      {!id ? (
        <SetupScreen onOpen={open} />
      ) : !view ? (
        <p className="experiment-help">실험 세션을 불러오고 있어요.</p>
      ) : view.stage === "pretest" ? (
        <ItemsForm
          key="pretest"
          items={view.pretest ?? []}
          intro="아는 만큼 답하세요. 모르면 비워 두어도 괜찮아요."
          onSubmit={(answers) => apply(() => submitPretest(view.id, answers))}
        />
      ) : learning ? (
        <LearnScreen
          key={view.stage}
          view={view}
          concepts={concepts}
          onView={setView}
          onError={setError}
        />
      ) : view.stage === "posttest" ? (
        <ItemsForm
          key="posttest"
          items={view.posttest ?? []}
          intro="두 개념에 대한 문제예요. 서술 문항은 내 말로 설명해 주세요."
          onSubmit={(answers) => apply(() => submitPosttest(view.id, answers))}
        />
      ) : view.stage === "survey" ? (
        <ItemsForm
          key="survey"
          items={view.survey ?? []}
          intro="두 도구를 써 본 느낌을 알려 주세요."
          onSubmit={(answers) => apply(() => submitSurvey(view.id, answers))}
        />
      ) : (
        <section className="card experiment-card">
          <h2>실험이 끝났어요. 참여해 주셔서 감사합니다.</h2>
          <p className="experiment-help">진행자에게 알려 주세요.</p>
          <button
            className="button secondary"
            onClick={() => {
              saveExperimentId(null);
              setId(null);
              setView(null);
            }}
          >
            새 참가자 시작(진행자)
          </button>
        </section>
      )}
    </div>
  );
}

// 역할: 서버가 준 남은 초에서 1초마다 줄여 보여 줌; 새로고침하면 서버 값으로 다시 시작하므로 시간이 늘지 않음
function Countdown({ seconds }: { seconds: number }) {
  const [deadline] = useState(() => Date.now() + seconds * 1000);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const left = remainingAt(deadline, now);
  return (
    <p
      className={`experiment-clock${left <= 60 ? " soon" : ""}`}
      aria-label="남은 시간"
    >
      {formatClock(left)}
    </p>
  );
}

// 역할: 진행자 시작 화면; 참가자 코드·학년·수강 여부·제외할 개념을 받아 세션을 만듦
// - 같은 코드가 이미 있으면 서버가 409로 막고 기존 세션 id를 주므로 재개를 고르게 함
function SetupScreen({
  onOpen,
}: {
  onOpen: (next: ExperimentView | string) => void;
}) {
  const [form, setForm] = useState<NewExperiment>({
    participant: "",
    grade: 3,
    dsa: "taken",
    exclude: [],
  });
  const [conflict, setConflict] = useState<{
    id: string;
    message: string;
  } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const valid = validParticipant(form.participant);

  async function start() {
    setBusy(true);
    setError("");
    try {
      const result = await createExperiment(form);
      if (result.conflict) setConflict(result);
      else onOpen(result.view);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="card experiment-card experiment-setup"
      onSubmit={(event) => {
        event.preventDefault();
        if (valid && !busy) start();
      }}
    >
      <p className="experiment-help">
        진행자가 입력합니다. 이름·연락처·성적은 받지 않아요.
      </p>
      <label>
        참가자 코드(S1~S9)
        <input
          value={form.participant}
          maxLength={2}
          onChange={(event) => {
            setConflict(null);
            setForm({ ...form, participant: event.target.value.toUpperCase() });
          }}
        />
      </label>
      <label>
        학년
        <select
          value={form.grade}
          onChange={(event) =>
            setForm({ ...form, grade: Number(event.target.value) })
          }
        >
          {[1, 2, 3, 4].map((grade) => (
            <option key={grade} value={grade}>
              {grade}학년
            </option>
          ))}
        </select>
      </label>
      <fieldset>
        <legend>자료구조 수강</legend>
        {(["taken", "taking"] as const).map((value) => (
          <label key={value} className="experiment-choice">
            <input
              type="radio"
              name="dsa"
              checked={form.dsa === value}
              onChange={() => setForm({ ...form, dsa: value })}
            />
            {value === "taken" ? "수강함" : "수강 중"}
          </label>
        ))}
      </fieldset>
      <fieldset>
        <legend>제외할 개념(서술 답 수집에 이미 쓴 개념)</legend>
        <div className="experiment-concepts">
          {EXPERIMENT_CONCEPTS.map((concept) => (
            <label key={concept} className="experiment-choice">
              <input
                type="checkbox"
                checked={form.exclude.includes(concept)}
                onChange={(event) =>
                  setForm({
                    ...form,
                    exclude: event.target.checked
                      ? [...form.exclude, concept]
                      : form.exclude.filter((item) => item !== concept),
                  })
                }
              />
              {concept}
            </label>
          ))}
        </div>
      </fieldset>
      {error && (
        <div className="experiment-error" role="alert">
          {error}
        </div>
      )}
      {conflict ? (
        <div className="experiment-conflict" role="alert">
          <p>{conflict.message} 기존 세션을 덮어쓰지 않아요.</p>
          <button
            type="button"
            className="button primary"
            onClick={() => onOpen(conflict.id)}
          >
            기존 세션 재개
          </button>
        </div>
      ) : (
        <button className="button primary" disabled={!valid || busy}>
          실험 시작
        </button>
      )}
    </form>
  );
}

// 역할: 시험·설문 문항을 한 화면 목록으로 보여 주고 한 번에 제출; 제출 후에는 고칠 수 없으므로 확인을 한 번 더 받음
function ItemsForm({
  items,
  intro,
  onSubmit,
}: {
  items: ExperimentItem[];
  intro: string;
  onSubmit: (answers: Record<string, string>) => Promise<void>;
}) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const set = (id: string, value: string) => {
    setConfirming(false);
    setAnswers((old) => ({ ...old, [id]: value }));
  };
  const blanks = items.filter(
    (item) => !(answers[item.id] ?? "").trim(),
  ).length;

  return (
    <form
      className="card experiment-card experiment-items"
      onSubmit={async (event) => {
        event.preventDefault();
        if (!confirming) return setConfirming(true);
        setBusy(true);
        await onSubmit(answers);
        setBusy(false);
      }}
    >
      <p className="experiment-help">{intro}</p>
      <ol>
        {items.map((item) => (
          <li key={item.id}>
            <p className="experiment-question" id={`q-${item.id}`}>
              {item.question}
            </p>
            <ItemInput
              item={item}
              value={answers[item.id] ?? ""}
              onChange={(value) => set(item.id, value)}
            />
          </li>
        ))}
      </ol>
      {confirming && (
        <p className="experiment-help" role="status">
          제출하면 답을 고칠 수 없어요.
          {!allAnswered(items, answers) &&
            ` 비어 있는 답이 ${blanks}개 있어요.`}{" "}
          한 번 더 누르면 제출해요.
        </p>
      )}
      <button className="button primary" disabled={busy}>
        {confirming ? "제출 확정" : "제출"}
      </button>
    </form>
  );
}

// 역할: 문항 형식별 입력(보기 선택·1~5 척도·단답·서술)
function ItemInput({
  item,
  value,
  onChange,
}: {
  item: ExperimentItem;
  value: string;
  onChange: (value: string) => void;
}) {
  const options =
    item.options ??
    (item.format === "scale"
      ? Object.fromEntries([1, 2, 3, 4, 5].map((n) => [String(n), String(n)]))
      : undefined);
  if (options)
    return (
      <div
        className="experiment-options"
        role="radiogroup"
        aria-labelledby={`q-${item.id}`}
      >
        {Object.entries(options).map(([key, text]) => (
          <label key={key} className="experiment-choice">
            <input
              type="radio"
              name={item.id}
              checked={value === key}
              onChange={() => onChange(key)}
            />
            {item.format === "multiple_choice" ? `${key}. ${text}` : text}
          </label>
        ))}
      </div>
    );
  if (item.format === "explanation" || item.format === "text")
    return (
      <textarea
        aria-labelledby={`q-${item.id}`}
        rows={4}
        maxLength={2000}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  return (
    <input
      aria-labelledby={`q-${item.id}`}
      maxLength={200}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

// 역할: 학습 단계 화면; 왼쪽 읽기 글, 오른쪽 배정된 도구 하나
// - 마감(서버 남은 시간 0)이 되면 도구를 닫고 timeout을 보낸 뒤 "계속 읽기"를 막고 다음 단계 버튼만 남김
// - 사건(help_open·stuck_session·chat_conversation)은 처음 한 번만 보내고 시각은 서버가 붙임
function LearnScreen({
  view,
  concepts,
  onView,
  onError,
}: {
  view: ExperimentView;
  concepts: Concept[];
  onView: (view: ExperimentView) => void;
  onError: (message: string) => void;
}) {
  // 서버 남은 시간을 받은 순간 기준의 마감 시각; 상태를 다시 읽으면 서버 값으로 다시 맞춤
  const deadline = useMemo(
    () => Date.now() + (view.remaining_seconds ?? 0) * 1000,
    [view],
  );
  const [now, setNow] = useState(Date.now);
  const [readPart2, setReadPart2] = useState<string | null>(null);
  const part2 = readPart2 ?? view.passage_part2 ?? null;
  const [stuck, setStuck] = useState<StuckRequest | null>(null);
  const [busy, setBusy] = useState(false);
  const expired = remainingAt(deadline, now) === 0;
  // 붙여넣기 막힘 세션은 복귀 위치별로 하나씩 저장되므로 세션·단계마다 다른 위치를 줘 다른 참가자·단계 세션과 섞이지 않게 함
  const returnTo = `#/experiment?session=${view.id}&stage=${view.stage}`;
  const stuckKey = `excerpt:${returnTo}`;
  const [conversationId] = useState(() =>
    chatConversationId(view.id, view.stage),
  );
  const eventKey = (type: string) => `${view.id}:${view.stage}:${type}`;

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  // 마감: 도구를 닫고 timeout 기록(마감 직후 409는 postEvent가 정상 처리); 2부를 이미 읽었어도 남은 도구는 닫음
  useEffect(() => {
    if (!expired) return;
    setStuck(null);
    if (!part2) sendOnce(eventKey("timeout"), () => send("timeout"));
  }, [expired]);

  // 역할: 사건 하나 기록하고 성공 여부를 돌려줌; 실패해도 학습은 막지 않고 안내만 함
  async function send(type: Parameters<typeof postEvent>[1], ref?: string) {
    try {
      await postEvent(view.id, type, ref);
      return true;
    } catch (failure) {
      onError((failure as Error).message);
      return false;
    }
  }

  function helpOpened() {
    sendOnce(eventKey("help_open"), () => send("help_open"));
  }

  // 역할: 제안 시스템의 [막혔어요]; 이 단계에서 진행 중인 막힘 세션이 있으면 이어서 엶
  function openStuck() {
    helpOpened();
    const id = loadStuckStore().sessions[stuckKey];
    setStuck(
      id ? { mode: "resume", key: stuckKey, id } : { mode: "paste", returnTo },
    );
  }

  // 역할: 막힘 패널이 세션을 저장할 때마다 이 단계 세션 id를 읽어 처음 본 id만 stuck_session으로 보냄
  function sessionsChanged() {
    const id = loadStuckStore().sessions[stuckKey];
    if (id) sendOnce(eventKey(`stuck:${id}`), () => send("stuck_session", id));
  }

  // 역할: 패널을 닫으면 메인 학습 화면에서 자동으로 다시 열리지 않도록 열림 표시를 지움
  function closeStuck() {
    try {
      saveStuckStore({ ...loadStuckStore(), active: null });
    } catch {
      // 열림 표시를 못 지워도 실험 진행에는 영향 없음
    }
    setStuck(null);
  }

  // 역할: 2부 열기(O2 기록); 마감과 겹쳐 거부되면 서버 상태로 다시 그림
  async function continueReading() {
    setBusy(true);
    try {
      const result = await postEvent(view.id, "continue_reading");
      if (result.passage_part2) setReadPart2(result.passage_part2);
      else onView(await getExperiment(view.id));
    } catch (failure) {
      onError((failure as Error).message);
      onView(await getExperiment(view.id).catch(() => view));
    }
    setBusy(false);
  }

  async function next() {
    setBusy(true);
    try {
      onView(await nextStage(view.id));
    } catch (failure) {
      onError((failure as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="experiment-learn">
      <article className="card experiment-passage" aria-label="읽기 글">
        <span className="eyebrow">{view.concept?.title}</span>
        <p>{view.passage_part1}</p>
        {part2 && <p className="experiment-part2">{part2}</p>}
        <div className="experiment-actions">
          {!part2 && (
            <button
              className="button primary"
              disabled={expired || busy}
              onClick={continueReading}
            >
              계속 읽기
            </button>
          )}
          {expired && !part2 && (
            <p className="experiment-help">
              시간이 끝나 도구를 닫았어요. 다음 단계로 넘어가 주세요.
            </p>
          )}
          {(part2 || expired) && (
            <button className="button primary" disabled={busy} onClick={next}>
              다음 단계
            </button>
          )}
        </div>
      </article>
      <section
        className={`experiment-tool ${view.condition}`}
        aria-label="학습 도구"
      >
        {expired ? (
          <p className="experiment-help">도구를 닫았어요.</p>
        ) : view.condition === "chat" ? (
          <ChatSession
            conversationId={conversationId}
            learningStatus="not-started"
            stuckInProgress={false}
            onStatusChange={() => {}}
            onStuck={() => {}}
            onPaste={() => {}}
            onBusyChange={(value) => {
              // 첫 질문을 보내는 순간이 채팅 조건의 첫 도움 요청
              if (!value) return;
              helpOpened();
              sendOnce(eventKey("chat"), () =>
                send("chat_conversation", conversationId),
              );
            }}
          />
        ) : (
          <div className="card experiment-card">
            <p className="experiment-help">
              읽다가 막힌 문장이 있으면 [막혔어요]를 누르고 그 문장을
              붙여넣으세요.
            </p>
            <button className="button primary" onClick={openStuck}>
              {loadStuckStore().sessions[stuckKey]
                ? "막힘 이어서 풀기"
                : "막혔어요"}
            </button>
          </div>
        )}
      </section>
      {stuck && !expired && (
        <StuckPanel
          request={stuck}
          concepts={concepts}
          onClose={closeStuck}
          onReturn={closeStuck}
          onSessionsChange={sessionsChanged}
        />
      )}
    </div>
  );
}
