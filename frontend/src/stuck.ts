// 파일 역할: 막힘 세션 API 호출·오류 분류와 진행 중 세션 ID의 브라우저 저장; 패널 화면과 통신·저장 처리를 분리
// - 화면 내용(view)은 AI core 엔진이 만들고 Spring이 저장한 체크포인트를 그대로 받음; 정답 키는 오지 않음
import type { AnimationData } from "./animation";

// MVP 지원 개념: 문항 은행이 있는 선형 구조 11개만 [막혔어요]를 켬(나머지는 안내 문구)
export const supportedConcepts = new Set([
  "memory-address",
  "reference",
  "logarithm",
  "big-o",
  "complexity-analysis",
  "array",
  "dynamic-array",
  "linked-list",
  "adt",
  "stack",
  "queue",
]);

// 역할: 막힘이 시작된 곳; return_to는 복귀할 해시 경로(예: #/learn?concept=stack)
export type Entry =
  | { kind: "roadmap"; concept: string; return_to: string }
  | { kind: "excerpt"; excerpt: string; return_to: string };

export type ConceptRef = { id: string; title: string };

// 역할: 화면용 문항; 선택형은 A~D 보기, 단답·서술은 입력창으로 답함
export type ItemView = {
  id: string;
  format: "multiple_choice" | "short_exact" | "explanation";
  question: string;
  options?: Record<string, string>;
};

// 역할: 진단 결과의 판정 근거 한 줄; 학습자가 본 문항과 낸 답(건너뛰기·문항 없음이면 null)
export type Evidence = {
  concept: string;
  item: string | null;
  result: "known" | "gap" | "self_known" | "no_items";
  question: string | null;
  answer: string | null;
  options?: Record<string, string>;
};

// 역할: 엔진 view.kind별 화면 내용(stuck.py와 같은 필드 이름)
export type StuckView =
  | {
      kind: "question";
      purpose: "diagnosis" | "explanation";
      concept: ConceptRef;
      item: ItemView;
    }
  | {
      kind: "diagnosis_result";
      target: ConceptRef;
      gaps: ConceptRef[];
      evidence: Evidence[];
      opted_out: boolean;
    }
  | {
      kind: "remediation";
      concept: ConceptRef;
      attempt: number;
      text: string;
      is_target: boolean;
      item?: ItemView;
      note?: string;
      // 코드가 계산한 단계별 그림; 장면이 없는 개념·생성 실패·필드 도입 전 저장 세션이면 없음
      animation?: AnimationData;
    }
  | {
      kind: "result";
      concept: ConceptRef;
      passed: boolean;
      retry?: boolean;
      rubric_met?: boolean[];
    }
  | { kind: "unresolved"; concept: ConceptRef; rubric_met?: boolean[] }
  | { kind: "next" }
  | {
      kind: "summary";
      text: string;
      return_to: string;
      results: { concept: ConceptRef; outcome: string; attempts: number }[];
    }
  | { kind: "out_of_scope"; return_to: string };

// 역할: Spring이 브라우저에 공개하는 세션; version은 다음 step 요청에 그대로 돌려보내는 체크포인트 번호
export type StuckSession = {
  id: string;
  version: number;
  stage: string;
  view: StuckView;
  entry: Entry;
};

export type StepInput =
  | { type: "answer"; item: string; answer: string }
  | { type: "skip_known"; item: string }
  | { type: "opt_out" }
  | { type: "continue" };

// 역할: HTTP 상태와 한국어 안내를 함께 담은 오류; status 0은 서버에 닿지 못한 경우
export class StuckError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// 역할: 같은 요청을 다시 보내도 되는 일시적 실패인지 판단
// - 502·503·504·연결 실패: Spring이 상태를 바꾸지 않았으므로 같은 version으로 재전송 가능
export function canRetry(error: unknown) {
  return (
    error instanceof StuckError && [0, 502, 503, 504].includes(error.status)
  );
}

export function isFinished(stage: string) {
  return stage === "complete" || stage === "out_of_scope";
}

// 역할: 진행 중 세션을 개념·진입별로 구분하는 저장 키; 붙여넣기는 시작한 학습 화면별로 하나
export function entryKey(entry: Entry) {
  return entry.kind === "roadmap"
    ? `roadmap:${entry.concept}`
    : `excerpt:${entry.return_to}`;
}

// 역할: JSON 요청을 보내고 실패하면 서버의 message(없으면 기본 안내)로 StuckError 발생
async function request(url: string, body?: unknown): Promise<StuckSession> {
  let response: Response;
  try {
    response = await fetch(url, {
      cache: "no-store",
      ...(body === undefined
        ? {}
        : {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }),
    });
  } catch {
    throw new StuckError(
      0,
      "서버에 연결할 수 없습니다. 연결을 확인한 뒤 다시 시도해 주세요.",
    );
  }
  // 프록시 오류처럼 JSON이 아닌 응답도 있으므로 해석 실패는 null로 두고 기본 안내 사용
  const data = await response.json().catch(() => null);
  if (!response.ok)
    throw new StuckError(
      response.status,
      typeof data?.message === "string"
        ? data.message
        : "막힘 세션을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    );
  return data as StuckSession;
}

export function createSession(entry: Entry) {
  return request("/api/stuck/sessions", { entry });
}

export function getSession(id: string) {
  return request(`/api/stuck/sessions/${id}`);
}

// 역할: 한 단계 진행; 409(다른 탭 진행·동시 요청)면 저장된 최신 단계를 다시 불러와 reloaded로 알림
export async function stepSession(
  session: StuckSession,
  input: StepInput,
): Promise<{ session: StuckSession; reloaded: boolean }> {
  try {
    const next = await request(`/api/stuck/sessions/${session.id}/step`, {
      version: session.version,
      input,
    });
    return { session: next, reloaded: false };
  } catch (error) {
    if (!(error instanceof StuckError) || error.status !== 409) throw error;
    return { session: await getSession(session.id), reloaded: true };
  }
}

// 역할: 진행 중 세션 목록과 열려 있던 패널; 새로고침·재접속 후 GET으로 같은 단계를 복구하는 기준
export type StuckStore = {
  sessions: Record<string, string>;
  active: string | null;
};

const storageKey = "cs-stuck-sessions-v1";

// 역할: 형식이 맞는 키(roadmap:·excerpt:)와 Spring 세션 ID(32자리 16진수)만 복구; 손상값은 빈 목록
export function loadStuckStore(storage?: Pick<Storage, "getItem">): StuckStore {
  const store: StuckStore = { sessions: {}, active: null };
  try {
    const saved = JSON.parse(
      (storage ?? localStorage).getItem(storageKey) ?? "null",
    );
    if (saved?.version !== 1) return store;
    const sessions =
      typeof saved.sessions === "object" &&
      saved.sessions !== null &&
      !Array.isArray(saved.sessions)
        ? saved.sessions
        : {};
    for (const [key, id] of Object.entries(sessions))
      if (
        /^(roadmap|excerpt):/.test(key) &&
        key.length <= 320 &&
        typeof id === "string" &&
        /^[0-9a-f]{32}$/.test(id)
      )
        store.sessions[key] = id;
    if (typeof saved.active === "string" && saved.active in store.sessions)
      store.active = saved.active;
  } catch {
    // 저장소 접근이 막히거나 값이 깨져도 학습 화면은 열려야 함
  }
  return store;
}

// 역할: 저장 실패 예외는 호출 화면이 안내하도록 그대로 전달
export function saveStuckStore(
  store: StuckStore,
  storage: Pick<Storage, "setItem"> = localStorage,
) {
  storage.setItem(storageKey, JSON.stringify({ version: 1, ...store }));
}

// 역할: 새로 시작한 세션을 진행 중 목록에 넣고 열린 패널로 표시
export function rememberStuckSession(
  key: string,
  id: string,
  storage: Pick<Storage, "getItem" | "setItem"> = localStorage,
) {
  const store = loadStuckStore(storage);
  saveStuckStore(
    { sessions: { ...store.sessions, [key]: id }, active: key },
    storage,
  );
}

// 역할: 끝난 세션(complete·out_of_scope)을 진행 중 목록에서 빼고 패널 열림 표시도 지움
export function finishStuckSession(
  key: string,
  storage: Pick<Storage, "getItem" | "setItem"> = localStorage,
) {
  const store = loadStuckStore(storage);
  const sessions = { ...store.sessions };
  delete sessions[key];
  saveStuckStore(
    { sessions, active: store.active === key ? null : store.active },
    storage,
  );
}
