// 파일 역할: 기본 CS 개념·선수 관계·영역 간 연관 관계와 브라우저 학습 상태 저장 규칙
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
    position: { x: 22, y: 75 },
  },
  {
    id: "scheduling",
    title: "CPU 스케줄링",
    domain: "운영체제",
    description:
      "실행할 프로세스를 선택하는 기준과 스케줄링 알고리즘을 비교합니다.",
    prerequisites: ["process"],
    position: { x: 40, y: 75 },
  },
  {
    id: "memory",
    title: "가상 메모리",
    domain: "운영체제",
    description:
      "주소 공간, 페이지와 프레임을 통해 메모리 관리 원리를 배웁니다.",
    prerequisites: ["process"],
    position: { x: 31, y: 90 },
  },
  {
    id: "array-list",
    title: "배열과 연결 리스트",
    domain: "자료구조",
    description: "데이터 배치 방식에 따른 접근·삽입·삭제 비용을 비교합니다.",
    prerequisites: [],
    position: { x: 16, y: 23 },
  },
  {
    id: "stack-queue",
    title: "스택과 큐",
    domain: "자료구조",
    description: "LIFO와 FIFO의 차이를 이해하고 적절한 활용 상황을 살펴봅니다.",
    prerequisites: ["array-list"],
    position: { x: 34, y: 23 },
  },
  {
    id: "tree-graph",
    title: "트리와 그래프",
    domain: "자료구조",
    description: "노드와 간선으로 관계를 표현하고 기본 탐색 방법을 익힙니다.",
    prerequisites: ["stack-queue"],
    position: { x: 25, y: 38 },
  },
  {
    id: "network-layers",
    title: "네트워크 계층",
    domain: "네트워크",
    description: "TCP/IP 계층별 역할과 데이터가 전달되는 과정을 이해합니다.",
    prerequisites: [],
    position: { x: 68, y: 27 },
  },
  {
    id: "tcp-udp",
    title: "TCP와 UDP",
    domain: "네트워크",
    description: "연결, 신뢰성, 전송 비용을 기준으로 두 프로토콜을 비교합니다.",
    prerequisites: ["network-layers"],
    position: { x: 86, y: 27 },
  },
  {
    id: "http",
    title: "HTTP 요청과 응답",
    domain: "네트워크",
    description: "웹 통신의 요청·응답 구조와 메서드, 상태 코드를 배웁니다.",
    prerequisites: ["tcp-udp"],
    position: { x: 77, y: 42 },
  },
];

// These associations connect applications of concepts across domains, not a required study order.
export const conceptRelations: {
  source: string;
  target: string;
  description: string;
  bend: { x: number; y: number };
}[] = [
  {
    source: "scheduling",
    target: "stack-queue",
    bend: { x: 39, y: 50 },
    description:
      "FCFS·라운드 로빈 스케줄링은 준비 큐에서 프로세스의 실행 순서를 관리합니다.",
  },
  {
    source: "process",
    target: "tcp-udp",
    bend: { x: 80, y: -40 },
    description: "프로세스는 운영체제의 소켓을 통해 TCP·UDP 통신을 수행합니다.",
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

// 역할: 외부 저장값이 배열이나 null이 아닌 객체인지 확인하는 타입 가드
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// 역할: 알려진 개념·유효한 상태·대화 ID만 복구; 손상된 저장값은 기본값으로 대체
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

// 역할: 형식 버전과 상태를 함께 저장; 실패 예외는 호출 화면에서 안내하도록 전달
export function saveRoadmap(
  state: RoadmapState,
  storage: Pick<Storage, "setItem"> = localStorage,
): void {
  storage.setItem(storageKey, JSON.stringify({ version: 1, ...state }));
}
