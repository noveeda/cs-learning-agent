// 파일 역할: 비교 실험 화면이 쓰는 Spring 실험 세션 API 호출·남은 시간 계산·새로고침 복구용 세션 id 저장
// - 단계·조건·남은 시간은 모두 서버가 정하고, 화면은 POST 뒤 항상 GET으로 다시 읽어 서버 상태만 그림
// - 정답 키·루브릭은 서버가 보내지 않으므로 이 파일과 화면에도 없음

// 실험 단계: 사전 시험 → 개념 1 학습 → 개념 2 학습 → 사후 시험 → 설문 → 끝
export type Stage =
  "pretest" | "learn1" | "learn2" | "posttest" | "survey" | "done";

// 역할: 화면용 문항(시험·설문 공통); 정답·근거 없이 묻는 내용과 보기만 있음
// - scale은 1~5 척도(options가 없으면 1~5), choice는 보기 중 하나, text·explanation은 서술
export type ExperimentItem = {
  id: string;
  concept?: string;
  format:
    | "multiple_choice"
    | "short_exact"
    | "explanation"
    | "scale"
    | "choice"
    | "text";
  question: string;
  options?: Record<string, string>;
};

// 역할: GET /api/experiments/{id} 응답; 단계에 따라 필요한 필드만 옴
// - remaining_seconds는 학습 단계에서만 오는 서버 시각 기준 남은 시간(새로고침해도 늘지 않음)
// - passage_part2는 "계속 읽기" 뒤에 채워져 새로고침해도 2부를 다시 보여 줄 수 있음
export type ExperimentView = {
  id: string;
  participant: string;
  stage: Stage;
  pretest?: ExperimentItem[];
  concept?: { id: string; title: string };
  condition?: "system" | "chat";
  passage_part1?: string;
  passage_part2?: string | null;
  remaining_seconds?: number;
  posttest?: ExperimentItem[];
  survey?: ExperimentItem[];
};

export type EventType =
  | "help_open"
  | "stuck_session"
  | "chat_conversation"
  | "continue_reading"
  | "timeout";

// 역할: 진행자 시작 화면 입력; 이름·연락처·성적은 받지 않음(학년과 자료구조 수강 여부만)
export type NewExperiment = {
  participant: string;
  grade: number;
  dsa: "taken" | "taking";
  exclude: string[];
};

// 실험 후보 개념(실험 자료 materials.json과 같은 8개); 진행자가 이미 서술 답 수집에 쓴 개념을 빼는 데 씀
export const EXPERIMENT_CONCEPTS = [
  "hash-table",
  "bst",
  "heap",
  "quick-sort",
  "merge-sort",
  "bfs",
  "topological-sort",
  "dynamic-programming",
];

export const stageTitles: Record<Stage, string> = {
  pretest: "사전 시험",
  learn1: "개념 1 학습",
  learn2: "개념 2 학습",
  posttest: "사후 시험",
  survey: "설문",
  done: "끝",
};

export class ExperimentError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const base = "/api/experiments";
const storageKey = "cs-experiment-session";

// 역할: 요청 하나를 보내고 오류는 상태 코드와 서버의 한국어 안내로 바꿈
async function call(url: string, body?: unknown): Promise<any> {
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
    throw new ExperimentError(
      0,
      "서버에 연결할 수 없습니다. 연결을 확인한 뒤 다시 시도해 주세요.",
    );
  }
  // 프록시 오류처럼 JSON이 아닌 응답도 있어 해석 실패는 null로 두고 기본 안내 사용
  const data = await response.json().catch(() => null);
  if (!response.ok)
    throw Object.assign(
      new ExperimentError(
        response.status,
        data?.message || "실험 세션을 처리하지 못했어요. 다시 시도해 주세요.",
      ),
      { data },
    );
  return data;
}

export function getExperiment(id: string): Promise<ExperimentView> {
  return call(`${base}/${id}`);
}

// 역할: 새 실험 세션 생성; 같은 참가자 코드가 이미 있으면 덮어쓰지 않고 기존 id를 돌려줘 재개를 고르게 함
export async function createExperiment(
  input: NewExperiment,
): Promise<
  | { conflict: false; view: ExperimentView }
  | { conflict: true; id: string; message: string }
> {
  try {
    return { conflict: false, view: await call(base, input) };
  } catch (error) {
    const data = (error as { data?: { id?: string } }).data;
    if (error instanceof ExperimentError && error.status === 409 && data?.id)
      return { conflict: true, id: data.id, message: error.message };
    throw error;
  }
}

// 역할: POST로 상태를 바꾼 뒤 GET으로 다시 읽음; 응답 형식이 단계마다 달라도 화면은 GET 형식 하나만 다룸
async function postThenReload(id: string, path: string, body: unknown) {
  await call(`${base}/${id}/${path}`, body);
  return getExperiment(id);
}

export function submitPretest(id: string, answers: Record<string, string>) {
  return postThenReload(id, "pretest", { answers });
}

export function submitPosttest(id: string, answers: Record<string, string>) {
  return postThenReload(id, "posttest", { answers });
}

export function submitSurvey(id: string, answers: Record<string, string>) {
  return postThenReload(id, "survey", { answers });
}

export function nextStage(id: string) {
  return postThenReload(id, "next", {});
}

// 역할: 학습 단계 사건 기록(시각은 서버가 붙임); continue_reading이면 응답에 2부 글이 옴
// - timeout은 마감 시각 경계에서 서버와 화면 시계가 조금 어긋나 409가 날 수 있으므로 이미 처리된 것으로 봄
export async function postEvent(
  id: string,
  type: EventType,
  ref?: string,
): Promise<{ passage_part2?: string }> {
  try {
    return await call(`${base}/${id}/events`, ref ? { type, ref } : { type });
  } catch (error) {
    if (
      type === "timeout" &&
      error instanceof ExperimentError &&
      error.status === 409
    )
      return {};
    throw error;
  }
}

// 역할: 마감 시각(ms)까지 남은 초; 서버 remaining_seconds를 받은 순간을 기준으로 계산해 0 아래로 내려가지 않음
export function remainingAt(deadline: number, now: number): number {
  return Math.max(0, Math.ceil((deadline - now) / 1000));
}

export function formatClock(seconds: number): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(Math.floor(seconds / 60))}:${pad(seconds % 60)}`;
}

// 참가자 코드는 S1~S9만 허용; 이름 같은 개인정보가 들어오지 않게 함
export function validParticipant(code: string): boolean {
  return /^S[1-9]$/.test(code);
}

export function allAnswered(
  items: { id: string }[],
  answers: Record<string, string>,
): boolean {
  return items.every((item) => (answers[item.id] ?? "").trim() !== "");
}

// 역할: 새로고침·브라우저 재시작 뒤 같은 세션으로 돌아오도록 진행 중 세션 id를 브라우저에 보관
export function loadExperimentId(
  storage: Pick<Storage, "getItem"> = localStorage,
): string | null {
  try {
    const id = storage.getItem(storageKey);
    return id && /^[0-9a-f]{32}$/.test(id) ? id : null;
  } catch {
    return null;
  }
}

export function saveExperimentId(
  id: string | null,
  storage: Pick<Storage, "setItem" | "removeItem"> = localStorage,
) {
  if (id) storage.setItem(storageKey, id);
  else storage.removeItem(storageKey);
}
