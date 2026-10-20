export type LearningStatus = "not-started" | "in-progress" | "completed";

export type Concept = {
  id: string;
  title: string;
  domain: string;
  description: string;
  prerequisites: string[];
  position: { x: number; y: number };
};

export const concepts: Concept[] = [
  {
    id: "process",
    title: "프로세스와 스레드",
    domain: "운영체제",
    description: "프로그램의 실행 단위와 자원 공유 방식을 이해합니다.",
    prerequisites: [],
    position: { x: 12, y: 64 },
  },
  {
    id: "scheduling",
    title: "CPU 스케줄링",
    domain: "운영체제",
    description:
      "실행할 프로세스를 선택하는 기준과 스케줄링 알고리즘을 비교합니다.",
    prerequisites: ["process"],
    position: { x: 18, y: 88 },
  },
  {
    id: "memory",
    title: "가상 메모리",
    domain: "운영체제",
    description:
      "주소 공간, 페이지와 프레임을 통해 메모리 관리 원리를 배웁니다.",
    prerequisites: ["process"],
    position: { x: 45, y: 90 },
  },
  {
    id: "array-list",
    title: "배열과 연결 리스트",
    domain: "자료구조",
    description: "데이터 배치 방식에 따른 접근·삽입·삭제 비용을 비교합니다.",
    prerequisites: [],
    position: { x: 14, y: 13 },
  },
  {
    id: "stack-queue",
    title: "스택과 큐",
    domain: "자료구조",
    description: "LIFO와 FIFO의 차이를 이해하고 적절한 활용 상황을 살펴봅니다.",
    prerequisites: ["array-list"],
    position: { x: 10, y: 40 },
  },
  {
    id: "tree-graph",
    title: "트리와 그래프",
    domain: "자료구조",
    description: "노드와 간선으로 관계를 표현하고 기본 탐색 방법을 익힙니다.",
    prerequisites: ["stack-queue"],
    position: { x: 31, y: 11 },
  },
  {
    id: "network-layers",
    title: "네트워크 계층",
    domain: "네트워크",
    description: "TCP/IP 계층별 역할과 데이터가 전달되는 과정을 이해합니다.",
    prerequisites: [],
    position: { x: 70, y: 15 },
  },
  {
    id: "tcp-udp",
    title: "TCP와 UDP",
    domain: "네트워크",
    description: "연결, 신뢰성, 전송 비용을 기준으로 두 프로토콜을 비교합니다.",
    prerequisites: ["network-layers"],
    position: { x: 91, y: 35 },
  },
  {
    id: "http",
    title: "HTTP 요청과 응답",
    domain: "네트워크",
    description: "웹 통신의 요청·응답 구조와 메서드, 상태 코드를 배웁니다.",
    prerequisites: ["tcp-udp"],
    position: { x: 88, y: 72 },
  },
];

export const statusLabels: Record<LearningStatus, string> = {
  "not-started": "미학습",
  "in-progress": "학습 중",
  completed: "완료",
};

export type RoadmapState = {
  selectedId: string;
  statuses: Record<string, LearningStatus>;
  conversationIds: Record<string, string>;
};

const storageKey = "cs-roadmap-v1";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function loadRoadmap(storage?: Pick<Storage, "getItem">): RoadmapState {
  const state: RoadmapState = {
    selectedId: concepts[0].id,
    statuses: {},
    conversationIds: {},
  };
  try {
    const saved: unknown = JSON.parse(
      (storage ?? localStorage).getItem(storageKey) ?? "null",
    );
    if (!isRecord(saved) || saved.version !== 1) return state;
    if (concepts.some((concept) => concept.id === saved.selectedId))
      state.selectedId = saved.selectedId as string;
    for (const { id } of concepts) {
      const status = isRecord(saved.statuses) ? saved.statuses[id] : undefined;
      if (
        status === "not-started" ||
        status === "in-progress" ||
        status === "completed"
      )
        state.statuses[id] = status;
      const conversationId = isRecord(saved.conversationIds)
        ? saved.conversationIds[id]
        : undefined;
      if (
        typeof conversationId === "string" &&
        /^[0-9a-f]{32}$/.test(conversationId)
      ) {
        state.conversationIds[id] = conversationId;
      }
    }
  } catch {
    // Storage access and damaged browser data must not prevent opening the roadmap.
  }
  return state;
}

export function saveRoadmap(
  state: RoadmapState,
  storage: Pick<Storage, "setItem"> = localStorage,
): void {
  storage.setItem(storageKey, JSON.stringify({ version: 1, ...state }));
}
