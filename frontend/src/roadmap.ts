/**
 * @file
 * @brief 여러 과목을 합친 개념 지도의 화면용 타입, 과목 목록·필터, 선수 관계 단계 배치, 학습 상태·세션별 진단 표시의 계산·저장 규칙
 */
import type { StuckSession } from "./stuck";

export type LearningStatus = "not-started" | "in-progress" | "completed";

/** @brief 직접 선수 개념과 그 근거; 상세 패널에서 "왜 먼저 봐야 하는지"를 보여 주는 데 사용 */
export type Prerequisite = { id: string; reason: string };

/**
 * @brief /api/concept-map에서 받은 개념 하나; 로드맵·상세·개념별 대화 화면이 공통으로 사용
 * - subject는 과목 ID(예: dsa, os), domain은 그 과목 이름(예: 자료구조·알고리즘); 학습 화면의 영역 표시에 재사용
 * - stuckSupported는 응답의 stuck_supported에서 계산한 [막혔어요] 지원 여부
 */
export type Concept = {
  id: string;
  title: string;
  subject: string;
  domain: string;
  description: string;
  objectives: string[];
  prerequisites: Prerequisite[];
  stuckSupported: boolean;
};

/** @brief 지도에 든 과목 하나; 로드맵의 과목 필터와 범례에 사용 */
export type Subject = { id: string; title: string };

/**
 * @brief 개념 목록에서 과목을 처음 나온 순서대로 한 번씩 모아 반환
 * - 개념이 과목 순서대로 오므로(AI core가 과목별로 이어 붙임) 이 순서가 곧 과목 순서
 */
export function listSubjects(concepts: Concept[]): Subject[] {
  const subjects = new Map<string, string>();
  for (const { subject, domain } of concepts)
    if (!subjects.has(subject)) subjects.set(subject, domain);
  return [...subjects].map(([id, title]) => ({ id, title }));
}

/**
 * @brief 한 과목의 개념만 남기고, 숨겨진 다른 과목 개념을 가리키는 선수 관계는 뺀 사본 반환("all"이면 원본 그대로)
 * - 지도 배치·선은 남은 관계만 쓰므로 끝이 없는 선이 그려지지 않음
 * - 원본 개념은 바꾸지 않아 상세 패널은 과목 사이 선수 관계(예: 운영체제 → 큐)를 계속 보여 줌
 */
export function filterBySubject(concepts: Concept[], subject: string): Concept[] {
  if (subject === "all") return concepts;
  const visible = concepts.filter((concept) => concept.subject === subject);
  const ids = new Set(visible.map((concept) => concept.id));
  return visible.map((concept) => ({
    ...concept,
    prerequisites: concept.prerequisites.filter((p) => ids.has(p.id)),
  }));
}

// 로드맵에 보이는 과목 안 단계의 끝(선수 관계가 없는 개념이 1단계)
// - 시연·학습 화면에 개념이 너무 많아 깊은 개념(현재 자료구조 18개)을 가리기 위한 표시 제한; AI core 지도·문항은 그대로 둠
export const MAX_VISIBLE_LEVEL = 5;

/**
 * @brief 과목 안 선수 관계 단계가 MAX_VISIBLE_LEVEL 이하인 개념만 남기고, 숨긴 개념을 가리키는 선수 관계는 뺀 사본 반환
 * - 단계는 같은 과목 선수 개념만으로 셈: 운영체제 개념이 자료구조 큐를 선수로 둬도 과목 안에서는 1단계
 * - 숨긴 개념의 후속 개념은 늘 더 깊어 함께 숨겨지므로, 남은 개념끼리의 선은 끊기지 않음
 * - 원본 개념은 바꾸지 않음
 */
export function hideDeepConcepts(concepts: Concept[]): Concept[] {
  const byId = new Map(concepts.map((concept) => [concept.id, concept]));
  const levels = new Map<string, number>();
  const level = (id: string): number => {
    if (!levels.has(id)) {
      const concept = byId.get(id)!;
      const inSubject = concept.prerequisites.filter((p) => byId.get(p.id)?.subject === concept.subject);
      levels.set(id, inSubject.length ? Math.max(...inSubject.map((p) => level(p.id))) + 1 : 1);
    }
    return levels.get(id)!;
  };
  const shown = concepts.filter((concept) => level(concept.id) <= MAX_VISIBLE_LEVEL);
  const ids = new Set(shown.map((concept) => concept.id));
  return shown.map((concept) => ({
    ...concept,
    prerequisites: concept.prerequisites.filter((p) => ids.has(p.id)),
  }));
}

/** @brief 단계(layer)와 같은 줄 안의 가로 위치(x, 줄 가운데가 0) */
export type ConceptLayout = {
  layers: string[][];
  positions: Record<string, { layer: number; x: number }>;
};

/**
 * @brief 선수 관계를 위→아래 단계로 배치하고 같은 줄 안의 순서를 정해 반환
 * - 단계: 최장 경로 기준(선수 개념 중 가장 깊은 단계 + 1); 선수 개념이 늘 위쪽 줄에 있어 학습 방향이 위→아래로 읽힘
 * - 순서: 바리센터(연결된 개념들의 평균 가로 위치) 정렬을 아래·위로 번갈아 반복해 선 교차를 줄임
 * - AI core가 순환·없는 선수 개념을 거부한 지도를 받으므로 순환 처리는 하지 않음
 */
export function layoutConcepts(concepts: Concept[]): ConceptLayout {
  const byId = new Map(concepts.map((concept) => [concept.id, concept]));
  const layerOf = new Map<string, number>();
  const depth = (id: string): number => {
    if (!layerOf.has(id)) {
      const prerequisites = byId.get(id)!.prerequisites;
      layerOf.set(
        id,
        prerequisites.length
          ? Math.max(...prerequisites.map((p) => depth(p.id))) + 1
          : 0,
      );
    }
    return layerOf.get(id)!;
  };
  const layers: string[][] = [];
  for (const { id } of concepts) (layers[depth(id)] ??= []).push(id);

  const successors = new Map<string, string[]>();
  for (const concept of concepts)
    for (const p of concept.prerequisites)
      successors.set(p.id, [...(successors.get(p.id) ?? []), concept.id]);
  const x = new Map<string, number>();
  /** @brief 줄 순서대로 가운데 정렬한 가로 위치 부여(간격 1, 가운데 0) */
  const place = (row: string[]) =>
    row.forEach((id, index) => x.set(id, index - (row.length - 1) / 2));
  /** @brief 이웃 개념의 평균 가로 위치로 한 줄을 정렬; 이웃이 없으면 현재 위치를 유지해 불필요한 이동 방지 */
  const reorder = (row: string[], neighbors: (id: string) => string[]) => {
    const center = (id: string) => {
      const linked = neighbors(id);
      return linked.length
        ? linked.reduce((sum, item) => sum + x.get(item)!, 0) / linked.length
        : x.get(id)!;
    };
    const keys = new Map(row.map((id) => [id, center(id)]));
    // sort는 안정 정렬이라 같은 값이면 원래 순서를 유지해 결과가 매번 같음
    row.sort((a, b) => keys.get(a)! - keys.get(b)!);
    place(row);
  };
  layers.forEach(place);
  // 2회 왕복이면 현재 36개 지도에서 교차가 충분히 줄고, 더 반복해도 배치가 거의 바뀌지 않음
  for (let pass = 0; pass < 2; pass += 1) {
    for (const row of layers.slice(1))
      reorder(row, (id) => byId.get(id)!.prerequisites.map((p) => p.id));
    for (const row of layers.slice(0, -1).reverse())
      reorder(row, (id) => successors.get(id) ?? []);
  }

  const positions: ConceptLayout["positions"] = {};
  layers.forEach((row, layer) =>
    row.forEach((id) => (positions[id] = { layer, x: x.get(id)! })),
  );
  return { layers, positions };
}

export const statusLabels: Record<LearningStatus, string> = {
  "not-started": "미학습",
  "in-progress": "학습 중",
  completed: "완료",
};

/** @brief 브라우저에 보관하는 로드맵 상태; selectedId가 빈 문자열이면 지도 첫 개념을 선택한 것으로 봄 */
export type RoadmapState = {
  selectedId: string;
  statuses: Record<string, LearningStatus>;
  conversationIds: Record<string, string>;
};

// 옛 9개 개념 로드맵은 cs-roadmap-v1에 남겨 두고 읽지도 지우지도 않음; ID가 겹쳐 엉뚱한 기록이 붙는 것을 막기 위해 키를 분리
const storageKey = "cs-dsa-roadmap-v1";
// AI core 개념 ID 규칙과 같은 형식만 저장값 키로 인정
const conceptIdPattern = /^[a-z][a-z0-9-]{1,40}$/;

/** @brief 외부 저장값이 배열이나 null이 아닌 객체인지 확인하는 타입 가드 */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * @brief 형식이 맞는 개념 ID·유효한 상태·대화 ID만 복구; 손상된 저장값은 기본값으로 대체
 * - 지도에 없는 ID라도 형식이 맞으면 보존: 화면은 지도에 있는 개념만 쓰므로 무시되고, 기록은 지워지지 않음
 */
export function loadRoadmap(storage?: Pick<Storage, "getItem">): RoadmapState {
  const state: RoadmapState = {
    selectedId: "",
    statuses: {},
    conversationIds: {},
  };
  try {
    const saved: unknown = JSON.parse(
      (storage ?? localStorage).getItem(storageKey) ?? "null",
    );
    if (!isRecord(saved) || saved.version !== 1) return state;
    if (
      typeof saved.selectedId === "string" &&
      conceptIdPattern.test(saved.selectedId)
    )
      state.selectedId = saved.selectedId;
    const statuses = isRecord(saved.statuses) ? saved.statuses : {};
    for (const [id, status] of Object.entries(statuses)) {
      if (
        conceptIdPattern.test(id) &&
        (status === "not-started" ||
          status === "in-progress" ||
          status === "completed")
      )
        state.statuses[id] = status;
    }
    const conversationIds = isRecord(saved.conversationIds)
      ? saved.conversationIds
      : {};
    for (const [id, conversationId] of Object.entries(conversationIds)) {
      if (
        conceptIdPattern.test(id) &&
        typeof conversationId === "string" &&
        /^[0-9a-f]{32}$/.test(conversationId)
      )
        state.conversationIds[id] = conversationId;
    }
  } catch {
    // Storage access and damaged browser data must not prevent opening the roadmap.
  }
  return state;
}

/** @brief 형식 버전과 상태를 함께 저장; 실패 예외는 호출 화면에서 안내하도록 전달 */
export function saveRoadmap(
  state: RoadmapState,
  storage: Pick<Storage, "setItem"> = localStorage,
): void {
  storage.setItem(storageKey, JSON.stringify({ version: 1, ...state }));
}

/** @brief 지도 진단 표시의 최소 복구 정보; 답안·설명 없이 세션과 개념 ID·통과 여부만 보관 */
export type RoadmapDiagnosis = {
  sessionId: string;
  targetId: string;
  gapIds: string[];
  passedIds: string[];
};

/**
 * @brief 세션 화면에서 지도용 진단 상태 갱신; 다른 세션의 진단이 섞이지 않도록 ID를 먼저 확인
 * - 보충·확인 문항 화면에는 원래 빈틈 목록이 없어 이전 진단을 유지
 * - 통과는 result·summary의 실제 판정으로만 바꾸며 사용자의 일반 학습 상태와 분리
 */
export function advanceDiagnosis(
  previous: RoadmapDiagnosis | null,
  session: StuckSession | null,
): RoadmapDiagnosis | null {
  if (!session) return null;
  const view = session.view;
  if (view.kind === "diagnosis_result")
    return {
      sessionId: session.id,
      targetId: view.target.id,
      gapIds: [...new Set(view.gaps.map((gap) => gap.id))],
      passedIds: [],
    };
  if (
    previous?.sessionId !== session.id ||
    view.kind === "out_of_scope" ||
    (view.kind === "question" && view.purpose === "diagnosis")
  ) return null;
  if (view.kind === "summary")
    return {
      ...previous,
      passedIds: previous.gapIds.filter((id) =>
        view.results.some((result) => result.concept.id === id && result.outcome === "passed"),
      ),
    };
  if (view.kind === "result" || view.kind === "unresolved")
    return {
      ...previous,
      passedIds: [
        ...previous.passedIds.filter((id) => id !== view.concept.id),
        ...(view.kind === "result" && view.passed && previous.gapIds.includes(view.concept.id)
          ? [view.concept.id] : []),
      ],
    };
  return previous;
}

/**
 * @brief 지도에 존재하는 빈틈·통과·목표 개념과 빈틈→목표의 모든 실제 선수 간선 계산
 * - 목표의 선수 집합 안에서만 후속 간선을 따라가 무관한 가지·빈틈의 이전 선수·가상 직통선을 제외
 * - 과목 필터가 숨긴 개념·간선은 입력 지도에 없으므로 강조 대상으로 만들지 않음
 */
export function diagnosisHighlights(concepts: Concept[], diagnosis: RoadmapDiagnosis | null) {
  const byId = new Map(concepts.map((concept) => [concept.id, concept]));
  const gapIds = diagnosis?.gapIds.filter((id) => byId.has(id)) ?? [];
  const passed = new Set(gapIds.filter((id) => diagnosis!.passedIds.includes(id)));
  const gaps = new Set(gapIds.filter((id) => !passed.has(id)));
  const target = diagnosis && byId.has(diagnosis.targetId) ? diagnosis.targetId : null;
  const edges = new Set<string>();
  const ancestors = new Set<string>();
  const pending = target ? [target] : [];
  while (pending.length) {
    const id = pending.pop()!;
    if (ancestors.has(id)) continue;
    ancestors.add(id);
    for (const prerequisite of byId.get(id)!.prerequisites)
      if (byId.has(prerequisite.id)) pending.push(prerequisite.id);
  }
  const successors = new Map<string, string[]>();
  for (const concept of concepts)
    for (const prerequisite of concept.prerequisites)
      if (byId.has(prerequisite.id))
        successors.set(prerequisite.id, [...(successors.get(prerequisite.id) ?? []), concept.id]);
  const visited = new Set<string>();
  /** @brief 목표로 이어지는 후속 간선만 수집; 합쳐지는 경로는 한 번만 탐색 */
  function trace(id: string) {
    if (visited.has(id)) return;
    visited.add(id);
    for (const next of successors.get(id) ?? [])
      if (ancestors.has(next)) {
        edges.add(`${id}:${next}`);
        trace(next);
      }
  }
  for (const id of gapIds) if (ancestors.has(id)) trace(id);
  return { gaps, passed, target, edges };
}

// 기존 막힘 목록과 별도로 최소 표시 정보만 저장; 복구는 해당 세션의 GET 성공 뒤에만 허용
const diagnosisStorageKey = "cs-roadmap-diagnoses-v1";

/** @brief 저장소 접근 실패·손상·형식 버전 변경 때 빈 진단 목록 반환 */
function readDiagnoses(storage: Pick<Storage, "getItem">): Record<string, unknown> {
  try {
    const saved: unknown = JSON.parse(storage.getItem(diagnosisStorageKey) ?? "null");
    return isRecord(saved) && saved.version === 1 && isRecord(saved.sessions) ? saved.sessions : {};
  } catch {
    return {};
  }
}

/** @brief 세션 ID가 일치하고 개념 ID·통과 목록이 유효한 표시 정보인지 검증 */
function validDiagnosis(value: unknown, sessionId: string): value is RoadmapDiagnosis {
  if (!isRecord(value)) return false;
  const gapIds = value.gapIds;
  return value.sessionId === sessionId &&
    /^[0-9a-f]{32}$/.test(sessionId) &&
    typeof value.targetId === "string" && conceptIdPattern.test(value.targetId) &&
    Array.isArray(gapIds) && gapIds.every((id) => typeof id === "string" && conceptIdPattern.test(id)) &&
    Array.isArray(value.passedIds) && value.passedIds.every((id) => gapIds.includes(id));
}

/** @brief 현재 GET으로 재개한 세션의 표시 정보만 복구; 다른 세션·손상값·저장소 차단은 표시 없음 */
export function loadDiagnosis(sessionId: string, storage?: Pick<Storage, "getItem">): RoadmapDiagnosis | null {
  try {
    const value = readDiagnoses(storage ?? localStorage)[sessionId];
    return validDiagnosis(value, sessionId)
      ? { sessionId, targetId: value.targetId, gapIds: [...value.gapIds], passedIds: [...value.passedIds] }
      : null;
  } catch {
    return null;
  }
}

/** @brief 진행 중 세션의 진단만 보존하며 현재 표시를 저장; 저장 실패는 App에서 새로고침 복구 한계를 알림 */
export function saveDiagnosis(
  diagnosis: RoadmapDiagnosis,
  activeSessionIds: string[],
  storage: Pick<Storage, "getItem" | "setItem"> = localStorage,
): void {
  const saved = readDiagnoses(storage);
  const sessions = Object.fromEntries(Object.entries(saved).filter(([id, value]) =>
    activeSessionIds.includes(id) && validDiagnosis(value, id),
  ));
  sessions[diagnosis.sessionId] = diagnosis;
  storage.setItem(diagnosisStorageKey, JSON.stringify({ version: 1, sessions }));
}
