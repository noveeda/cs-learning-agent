// 파일 역할: 모의 막힘 세션 API로 [막혔어요]·붙여넣기 진입, 진단→결과→보충→확인→서술→요약→복귀, 새로고침 재개, 503 재시도·409 복구를 브라우저에서 검증
import { readFileSync } from "node:fs";
import { test, expect, type Page, type Route } from "@playwright/test";

const conceptMap = JSON.parse(
  readFileSync(
    new URL("../../../ai-core/content/dsa/concept-map.json", import.meta.url),
    "utf8",
  ),
);
// 지도 파일에는 없고 AI core가 문항 은행에서 계산해 붙이는 막힘 지원 목록(현재 선형 구조 11개)
const stuckSupported = [
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
];
const stuckKey = "cs-stuck-sessions-v1";
const sessionId = "0123456789abcdef0123456789abcdef";
// "처음부터 다시"로 새로 만든 세션은 다른 ID를 받음
const restartedId = "fedcba9876543210fedcba9876543210";
const options = {
  A: "값을 넣은 순서대로 꺼낸다",
  B: "연산 규칙으로 정의하고 구현은 숨긴다",
  C: "항상 배열로만 구현한다",
  D: "원소를 정렬해 보관한다",
};
const linkedList = { id: "linked-list", title: "연결 리스트" };
const stack = { id: "stack", title: "스택" };
// 엔진(stuck.py)이 만드는 view와 같은 모양의 단계별 화면
const views = {
  q1: {
    kind: "question",
    purpose: "diagnosis",
    concept: { id: "adt", title: "추상 자료형과 구현" },
    item: {
      id: "adt-d1",
      format: "multiple_choice",
      question: "추상 자료형에 대한 설명으로 알맞은 것은?",
      options,
    },
  },
  q2: {
    kind: "question",
    purpose: "diagnosis",
    concept: linkedList,
    item: {
      id: "linked-list-d1",
      format: "short_exact",
      question: "노드가 다음 노드를 가리키려고 저장하는 값은?",
    },
  },
  diagnosis: {
    kind: "diagnosis_result",
    target: stack,
    gaps: [linkedList],
    evidence: [
      {
        concept: "adt",
        item: "adt-d1",
        result: "known",
        question: "추상 자료형에 대한 설명으로 알맞은 것은?",
        answer: "B",
        options,
      },
      {
        concept: "linked-list",
        item: "linked-list-d1",
        result: "gap",
        question: "노드가 다음 노드를 가리키려고 저장하는 값은?",
        answer: "인덱스",
      },
    ],
    opted_out: false,
  },
  remediation: {
    kind: "remediation",
    concept: linkedList,
    attempt: 1,
    text: "## 연결 리스트\n\n**노드**는 값과 다음 노드의 참조를 함께 가져요.",
    is_target: false,
    item: {
      id: "linked-list-c1",
      format: "multiple_choice",
      question: "맨 앞에 노드를 넣을 때 바꿔야 하는 참조는?",
      options: {
        A: "새 노드의 next",
        B: "마지막 노드",
        C: "배열 크기",
        D: "없음",
      },
    },
  },
  explanation: {
    kind: "question",
    purpose: "explanation",
    concept: linkedList,
    item: {
      id: "linked-list-e1",
      format: "explanation",
      question: "연결 리스트에서 맨 앞 삽입이 빠른 이유를 설명해 보세요.",
    },
  },
  result: {
    kind: "result",
    concept: linkedList,
    passed: true,
    rubric_met: [true, true],
  },
  summary: {
    kind: "summary",
    text: "연결 리스트의 **참조**를 채웠으니 스택 구현으로 돌아가요.",
    return_to: "#/learn?concept=stack",
    results: [
      { concept: linkedList, outcome: "passed", attempts: 1 },
      { concept: stack, outcome: "passed", attempts: 1 },
    ],
  },
};

// AI core 시뮬레이터(PR #44 simulate)가 만든 원소 8개 배열의 insert [2] 5 결과; 휴대폰 폭 축소 확인용 큰 장면
const cell = (id: string, label: string, x: number, tone = "normal") => ({
  id,
  label,
  x,
  y: 0,
  shape: "cell",
  tone,
});
const arrayInsert = {
  kind: "array",
  title: "배열 연산",
  frames: [
    {
      boxes: ["3", "8", "1", "9", "4", "7", "2", "6"].map((label, x) =>
        cell(`a${x}`, label, x),
      ),
      arrows: [],
      caption: "초기 배열 [3, 8, 1, 9, 4, 7, 2, 6]",
    },
    {
      boxes: [
        cell("a0", "3", 0),
        cell("a1", "8", 1),
        cell("a8", "5", 2, "new"),
        ...["1", "9", "4", "7", "2", "6"].map((label, i) =>
          cell(`a${i + 2}`, label, i + 3, "active"),
        ),
      ],
      arrows: [],
      caption: "insert [2] 5: 뒤 원소 6개를 한 칸씩 밀고 5를 넣습니다",
    },
  ],
};

// AI core 시뮬레이터(PR #65 simulate)가 만든 무방향 그래프 BFS(정점 5개·간선 5개, 시작 A) 결과를 같은 값으로 다시 구성
// - 가로 10칸이라 휴대폰 폭에서 크게 줄어드는 장면이고, 무방향 간선은 양쪽 방향 화살표 두 개로 옴
const graphNodes: [string, number, number][] = [
  ["A", 3, 0],
  ["B", 9, 2],
  ["C", 1, 3],
  ["D", 9, 3],
  ["E", 3, 5],
];
const graphEdges = [
  [0, 1],
  [0, 2],
  [1, 3],
  [1, 4],
  [2, 4],
].flatMap(([a, b]) => [
  { source: `n${a}`, target: `n${b}`, label: null },
  { source: `n${b}`, target: `n${a}`, label: null },
]);
const bfsQueues = [
  ["A"],
  ["B", "C"],
  ["C", "D", "E"],
  ["D", "E"],
  ["E"],
  [],
  [],
];
const bfsCaptions = [
  "시작 정점 A부터 BFS: 큐에 A 추가",
  "A 방문 → 큐 앞에서 꺼냄, 새 이웃 B, C 큐 뒤에 추가",
  "B 방문 → 큐 앞에서 꺼냄, 새 이웃 D, E 큐 뒤에 추가",
  "C 방문 → 큐 앞에서 꺼냄, 새로 넣을 이웃 없음",
  "D 방문 → 큐 앞에서 꺼냄, 새로 넣을 이웃 없음",
  "E 방문 → 큐 앞에서 꺼냄, 새로 넣을 이웃 없음",
  "BFS 끝: 방문 순서 A → B → C → D → E",
];
// 단계 step에서 방금 방문한 정점은 new, 그 전에 방문한 정점은 active로 칠함(마지막 단계는 모두 active)
const graphBfs = {
  kind: "graph_traversal",
  title: "BFS(무방향 그래프, 시작 A)",
  frames: bfsCaptions.map((caption, step) => {
    const visited = Math.min(step, graphNodes.length);
    const fresh = step >= 1 && step <= graphNodes.length ? step - 1 : -1;
    const tone = (i: number) =>
      i === fresh ? "new" : i < visited ? "active" : "normal";
    return {
      boxes: [
        ...graphNodes.map(([label, x, y], i) => ({
          id: `n${i}`,
          label,
          x,
          y,
          shape: "node",
          tone: tone(i),
        })),
        { id: "lv", label: "방문", x: 0, y: 6, shape: "label", tone: "normal" },
        ...graphNodes.slice(0, visited).map(([label], i) => ({
          id: `v${i}`,
          label,
          x: i + 1,
          y: 6,
          shape: "cell",
          tone: i === fresh ? "new" : "normal",
        })),
        {
          id: "lw",
          label: "queue",
          x: 0,
          y: 7,
          shape: "label",
          tone: "normal",
        },
        ...bfsQueues[step].map((label, i) => ({
          id: `q${i}`,
          label,
          x: i + 1,
          y: 7,
          shape: "cell",
          tone: "normal",
        })),
      ],
      arrows: graphEdges,
      caption,
    };
  }),
};

type Reply =
  { stage: string; view: object } | { status: number; message: string };

// 역할: Spring 막힘 세션 API를 흉내 냄; 버전이 맞을 때만 다음 화면으로 넘기고 오류 응답은 상태를 바꾸지 않음
async function mockStuck(page: Page, start: Reply, steps: Reply[]) {
  const calls: { method: string; body?: any }[] = [];
  let session: {
    id: string;
    version: number;
    stage: string;
    view: object;
    entry: object;
  } | null = null;
  // 역할: 오류 응답 또는 새 세션 상태를 돌려줌
  const fulfill = (
    route: Route,
    reply: Reply,
    apply: (next: { stage: string; view: object }) => void,
  ) => {
    if ("status" in reply)
      return route.fulfill({
        status: reply.status,
        json: { message: reply.message },
      });
    apply(reply);
    return route.fulfill({ json: session });
  };
  await page.route("**/api/stuck/sessions**", async (route) => {
    const request = route.request();
    const body = request.postDataJSON();
    calls.push({ method: request.method(), body });
    if (request.method() === "GET") return route.fulfill({ json: session });
    if (!request.url().endsWith("/step"))
      return fulfill(route, start, (next) => {
        const created = calls.filter((call) => call.body?.entry).length;
        session = {
          id: created > 1 ? restartedId : sessionId,
          version: 1,
          ...next,
          entry: body.entry,
        };
      });
    if (body.version !== session!.version)
      return route.fulfill({
        status: 409,
        json: { message: "세션 상태가 변경됐습니다." },
      });
    return fulfill(route, steps.shift()!, (next) => {
      session = { ...session!, ...next, version: session!.version + 1 };
    });
  });
  return calls;
}

test.beforeEach(async ({ page }) => {
  await page.route("**/api/concept-map", (route) =>
    route.fulfill({ json: { ...conceptMap, stuck_supported: stuckSupported } }),
  );
  await page.route("**/api/conversations/*", (route) =>
    route.fulfill({ json: { messages: [] } }),
  );
});

// 역할: 로드맵에서 스택을 골라 학습 화면으로 들어감(개념별 대화 저장 후 이동)
async function openStackLesson(page: Page) {
  await page.goto("/#/roadmap");
  await page
    .getByRole("button", { name: "스택 · 미학습", exact: true })
    .click();
  await page.getByRole("button", { name: "학습 시작", exact: true }).click();
  await expect(page).toHaveURL(/#\/learn\?concept=stack$/);
}

const panel = (page: Page) =>
  page.getByRole("complementary", { name: "막힘 풀기" });

test("E1 [막혔어요]: 진단→결과→보충→확인→서술→요약→원래 학습으로 복귀", async ({
  page,
}) => {
  const calls = await mockStuck(page, { stage: "diagnose", view: views.q1 }, [
    { stage: "diagnose", view: views.q2 },
    { stage: "remediate", view: views.diagnosis },
    { stage: "check", view: views.remediation },
    { stage: "explain", view: views.explanation },
    { stage: "return", view: views.result },
    { stage: "complete", view: views.summary },
  ]);
  await openStackLesson(page);
  const question = page.getByLabel("질문", { exact: true });
  await question.fill("내가 쓰던 질문");
  await page.getByRole("button", { name: "막혔어요", exact: true }).click();
  const sheet = panel(page);
  await sheet.getByRole("radio", { name: /B\. 연산 규칙/ }).check();
  await sheet.getByRole("button", { name: "답 제출" }).click();
  await sheet
    .getByLabel("노드가 다음 노드를 가리키려고 저장하는 값은?")
    .fill("인덱스");
  await sheet.getByRole("button", { name: "답 제출" }).click();
  // 진단 결과: 빈틈 목록과 어떤 문항에 어떤 답을 해서 판단했는지
  await expect(sheet.locator(".stuck-gaps")).toHaveText("연결 리스트");
  const evidence = sheet.locator(".stuck-evidence > li");
  await expect(evidence.nth(0)).toContainText("추상 자료형과 구현");
  await expect(evidence.nth(0)).toContainText("맞힘");
  await expect(evidence.nth(0)).toContainText(
    "B. 연산 규칙으로 정의하고 구현은 숨긴다 ← 내 답",
  );
  await expect(evidence.nth(1)).toContainText("빈틈");
  await expect(evidence.nth(1)).toContainText("내 답: 인덱스");
  // 넓은 화면에서는 대화가 패널에 가리지 않고 옆에 남아 있음
  const chat = await page
    .getByRole("region", { name: "학습 대화" })
    .boundingBox();
  const side = await sheet.boundingBox();
  expect(chat!.x + chat!.width).toBeLessThanOrEqual(side!.x);
  await sheet.getByRole("button", { name: "보충 시작" }).click();
  await expect(
    sheet.getByRole("heading", { name: "연결 리스트", level: 2 }),
  ).toBeVisible();
  await expect(sheet.locator("strong", { hasText: "노드" })).toBeVisible();
  // animation 필드가 없는 보충 화면은 재생기 없이 텍스트만 보여 줌
  await expect(sheet.locator(".animation-player")).toHaveCount(0);
  await sheet.getByRole("radio", { name: /A\. 새 노드의 next/ }).check();
  await sheet.getByRole("button", { name: "답 제출" }).click();
  await sheet
    .getByLabel("연결 리스트에서 맨 앞 삽입이 빠른 이유를 설명해 보세요.")
    .fill("참조 하나만 바꾸면 되기 때문입니다.");
  await sheet.getByRole("button", { name: "설명 제출" }).click();
  await expect(sheet).toContainText("이해 확인을 통과했어요.");
  await expect(sheet).toContainText("서술 기준 2/2개");
  await sheet.getByRole("button", { name: "다음", exact: true }).click();
  await expect(sheet.locator(".stuck-results")).toContainText("이해 확인 통과");
  await expect(
    page.evaluate(
      (key) => JSON.parse(localStorage.getItem(key)!).sessions,
      stuckKey,
    ),
  ).resolves.toEqual({});
  await sheet.getByRole("button", { name: "원래 학습으로 돌아가기" }).click();
  await expect(sheet).toHaveCount(0);
  await expect(page).toHaveURL(/#\/learn\?concept=stack$/);
  await expect(question).toHaveValue("내가 쓰던 질문");
  expect(calls.map((call) => call.body)).toEqual([
    {
      entry: {
        kind: "roadmap",
        concept: "stack",
        return_to: "#/learn?concept=stack",
      },
    },
    { version: 1, input: { type: "answer", item: "adt-d1", answer: "B" } },
    {
      version: 2,
      input: { type: "answer", item: "linked-list-d1", answer: "인덱스" },
    },
    { version: 3, input: { type: "continue" } },
    {
      version: 4,
      input: { type: "answer", item: "linked-list-c1", answer: "A" },
    },
    {
      version: 5,
      input: {
        type: "answer",
        item: "linked-list-e1",
        answer: "참조 하나만 바꾸면 되기 때문입니다.",
      },
    },
    { version: 6, input: { type: "continue" } },
  ]);
});

test("이미 알아요·바로 설명은 문항 없이 해당 입력만 보냄", async ({ page }) => {
  const calls = await mockStuck(page, { stage: "diagnose", view: views.q1 }, [
    { stage: "diagnose", view: views.q2 },
    {
      stage: "remediate",
      view: {
        ...views.diagnosis,
        gaps: [],
        // 진단 문항이 없는 선수 개념은 판단하지 않고 no_items로 기록됨
        evidence: [
          {
            concept: "reference",
            item: null,
            result: "no_items",
            question: null,
            answer: null,
          },
        ],
        opted_out: true,
      },
    },
  ]);
  await openStackLesson(page);
  await page.getByRole("button", { name: "막혔어요", exact: true }).click();
  await panel(page).getByRole("button", { name: "이미 알아요" }).click();
  await panel(page).getByRole("button", { name: "바로 설명" }).click();
  await expect(panel(page)).toContainText(
    "진단을 멈추고 바로 설명으로 넘어가요.",
  );
  await expect(panel(page).locator(".stuck-evidence > li")).toHaveText(
    "참조와 포인터진단 문항 없음",
  );
  expect(calls.slice(1).map((call) => call.body.input)).toEqual([
    { type: "skip_known", item: "adt-d1" },
    { type: "opt_out" },
  ]);
});

test("E2 붙여넣기: 지원 범위 밖 안내 후 원래 학습으로 돌아감", async ({
  page,
}) => {
  const calls = await mockStuck(
    page,
    {
      stage: "out_of_scope",
      view: { kind: "out_of_scope", return_to: "#/learn?concept=stack" },
    },
    [],
  );
  await openStackLesson(page);
  await page.getByRole("button", { name: "막힌 문장 붙여넣기" }).click();
  const box = panel(page).getByRole("textbox");
  await expect(box).toHaveAttribute("maxlength", "2000");
  await box.fill("다익스트라에서 우선순위 큐를 쓰는 이유가 뭐죠?");
  await expect(panel(page)).toContainText("26/2000자");
  await panel(page).getByRole("button", { name: "이 문장으로 시작" }).click();
  await expect(panel(page)).toContainText("지원하는 개념");
  // 지원 개념 수는 문항 은행에 따라 늘어나므로 안내에 개수를 박아 두지 않음
  await expect(panel(page)).toContainText("문항이 준비된 개념");
  await expect(panel(page)).not.toContainText("11개");
  expect(calls[0].body).toEqual({
    entry: {
      kind: "excerpt",
      excerpt: "다익스트라에서 우선순위 큐를 쓰는 이유가 뭐죠?",
      return_to: "#/learn?concept=stack",
    },
  });
  await panel(page)
    .getByRole("button", { name: "원래 학습으로 돌아가기" })
    .click();
  await expect(panel(page)).toHaveCount(0);
  expect(
    await page.evaluate((key) => localStorage.getItem(key), stuckKey),
  ).toContain('"sessions":{}');
});

test("새로고침하면 열려 있던 막힘 세션을 같은 단계에서 재개하고, 닫아도 이어서 풀 수 있음", async ({
  page,
}) => {
  const calls = await mockStuck(page, { stage: "diagnose", view: views.q1 }, [
    { stage: "diagnose", view: views.q2 },
  ]);
  await openStackLesson(page);
  await page.getByRole("button", { name: "막혔어요", exact: true }).click();
  await panel(page).getByRole("radio", { name: /B\./ }).check();
  await panel(page).getByRole("button", { name: "답 제출" }).click();
  await expect(panel(page)).toContainText("노드가 다음 노드를");
  await page.reload();
  await expect(panel(page)).toContainText("노드가 다음 노드를");
  expect(calls.at(-1)).toEqual({ method: "GET", body: null });
  await panel(page).getByRole("button", { name: "막힘 패널 닫기" }).click();
  await expect(panel(page)).toHaveCount(0);
  await page.reload();
  await expect(panel(page)).toHaveCount(0);
  await page.getByRole("button", { name: "막힘 이어서 풀기" }).click();
  await expect(panel(page)).toContainText("노드가 다음 노드를");
  // 이어서 열 때는 새 세션을 만들지 않고 저장된 세션을 불러옴
  expect(
    calls.filter((call) => call.method === "POST" && call.body.entry),
  ).toHaveLength(1);
});

test("503은 화면을 유지하고 같은 버전으로 다시 시도하며, 요청 중 중복 클릭을 막음", async ({
  page,
}) => {
  const calls = await mockStuck(
    page,
    { stage: "remediate", view: views.diagnosis },
    [
      {
        status: 503,
        message:
          "지금은 이 단계를 처리할 수 없습니다. 잠시 후 다시 시도해 주세요.",
      },
      { stage: "check", view: views.remediation },
    ],
  );
  await openStackLesson(page);
  await page.getByRole("button", { name: "막혔어요", exact: true }).click();
  await panel(page).getByRole("button", { name: "보충 시작" }).click();
  const alert = panel(page).getByRole("alert");
  await expect(alert).toContainText("잠시 후 다시 시도해 주세요.");
  await expect(panel(page).locator(".stuck-gaps")).toHaveText("연결 리스트");
  let release!: () => void;
  const held = new Promise<void>((resolve) => (release = resolve));
  await page.route("**/api/stuck/sessions/*/step", async (route) => {
    await held;
    await route.fallback();
  });
  await alert.getByRole("button", { name: "다시 시도" }).click();
  await expect(panel(page).getByRole("status")).toContainText("수십 초");
  await expect(
    panel(page).getByRole("button", { name: "보충 시작" }),
  ).toBeDisabled();
  release();
  await expect(panel(page).locator(".stuck-text")).toContainText("노드는 값과");
  expect(calls.slice(1).map((call) => call.body)).toEqual([
    { version: 1, input: { type: "continue" } },
    { version: 1, input: { type: "continue" } },
  ]);
});

test("409를 받으면 저장된 최신 단계를 다시 불러와 보여 줌", async ({
  page,
}) => {
  await mockStuck(page, { stage: "diagnose", view: views.q1 }, []);
  await page.route("**/api/stuck/sessions/*/step", (route) =>
    route.fulfill({
      status: 409,
      json: { message: "같은 세션의 이전 요청을 처리 중입니다." },
    }),
  );
  await page.route(`**/api/stuck/sessions/${sessionId}`, (route) =>
    route.fulfill({
      json: {
        id: sessionId,
        version: 2,
        stage: "diagnose",
        view: views.q2,
        entry: {
          kind: "roadmap",
          concept: "stack",
          return_to: "#/learn?concept=stack",
        },
      },
    }),
  );
  await openStackLesson(page);
  await page.getByRole("button", { name: "막혔어요", exact: true }).click();
  await panel(page).getByRole("radio", { name: /B\./ }).check();
  await panel(page).getByRole("button", { name: "답 제출" }).click();
  await expect(panel(page)).toContainText("최신 화면을 불러왔어요");
  await expect(panel(page)).toContainText("노드가 다음 노드를");
});

test("로드맵 상세: MVP 개념은 [막혔어요], 나머지는 지원 안내", async ({
  page,
}) => {
  await mockStuck(page, { stage: "diagnose", view: views.q1 }, []);
  await page.goto("/#/roadmap");
  const detail = page.getByRole("region", { name: "선택한 개념" });
  await page
    .getByRole("button", { name: "너비 우선 탐색 · 미학습", exact: true })
    .click();
  await expect(detail.getByRole("button", { name: "막혔어요" })).toHaveCount(0);
  await expect(detail).toContainText("아직 [막혔어요]를 지원하지 않는 개념");
  await page.getByRole("button", { name: "큐 · 미학습", exact: true }).click();
  await detail.getByRole("button", { name: "막혔어요", exact: true }).click();
  await expect(panel(page)).toContainText("추상 자료형에 대한 설명");
  expect(
    await page.evaluate(
      (key) => JSON.parse(localStorage.getItem(key)!),
      stuckKey,
    ),
  ).toEqual({
    version: 1,
    sessions: { "roadmap:queue": sessionId },
    active: "roadmap:queue",
  });
});

test("390px 모바일에서는 전체 화면 시트로 열리고 가로 스크롤이 없음", async ({
  page,
}) => {
  await mockStuck(page, { stage: "remediate", view: views.diagnosis }, []);
  await page.setViewportSize({ width: 390, height: 844 });
  await openStackLesson(page);
  await page.getByRole("button", { name: "막혔어요", exact: true }).click();
  const box = await panel(page).boundingBox();
  expect(box).toEqual({ x: 0, y: 0, width: 390, height: 844 });
  await expect(panel(page).locator(".stuck-evidence > li")).toHaveCount(2);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("보충 애니메이션: 375px에서 가로 스크롤 없이 축소되고 다음·이전·키보드로 단계가 바뀜", async ({
  page,
}) => {
  await mockStuck(
    page,
    {
      stage: "check",
      view: { ...views.remediation, animation: arrayInsert },
    },
    [],
  );
  await page.setViewportSize({ width: 375, height: 812 });
  await openStackLesson(page);
  await page.getByRole("button", { name: "막혔어요", exact: true }).click();
  const player = panel(page).locator(".animation-player");
  await expect(player).toContainText("초기 배열 [3, 8, 1, 9, 4, 7, 2, 6]");
  await expect(player).toContainText("1 / 2");
  await expect(
    player.getByRole("button", { name: "이전 단계" }),
  ).toBeDisabled();
  // 원래 크기(9칸)보다 좁은 화면이라 SVG가 패널 폭 안으로 줄어듦
  const canvas = await player.locator(".animation-canvas").boundingBox();
  expect(canvas!.x).toBeGreaterThanOrEqual(0);
  expect(canvas!.x + canvas!.width).toBeLessThanOrEqual(375);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= 375),
  ).toBe(true);
  // 줄어든 뒤 화면에 보이는 상자 글자 크기(viewBox 글자 크기 × 축소 비율)가 12px 이상이어야 숫자를 읽을 수 있음
  const shown = await player.locator(".animation-canvas").evaluate((svg) => {
    const text = svg.querySelector(".shape-cell text")!;
    const scale =
      svg.getBoundingClientRect().width /
      (svg as SVGSVGElement).viewBox.baseVal.width;
    return parseFloat(getComputedStyle(text).fontSize) * scale;
  });
  expect(shown).toBeGreaterThanOrEqual(12);
  // 검수용 화면 기록(test-results는 Git 제외)
  await page.screenshot({ path: test.info().outputPath("animation-375.png") });
  await player.getByRole("button", { name: "다음 단계" }).click();
  await expect(player).toContainText("insert [2] 5: 뒤 원소 6개를");
  await expect(player).toContainText("2 / 2");
  await expect(
    player.getByRole("button", { name: "다음 단계" }),
  ).toBeDisabled();
  // 새로 넣은 5는 new로 강조되고, 같은 id의 1(a2)은 2칸에서 3칸(64px × 3)으로 옮겨 그려짐
  await expect(player.locator(".animation-box", { hasText: "5" })).toHaveClass(
    /tone-new/,
  );
  await expect(
    player.locator(".animation-box", { hasText: /^1$/ }),
  ).toHaveAttribute("style", /translate\(192px, 0px\)/);
  await player.focus();
  await page.keyboard.press("ArrowLeft");
  await expect(player).toContainText("1 / 2");
});

test("그래프 BFS 장면: 375px에서 가로 스크롤 없이 축소되고 다음·이전으로 방문·큐 상태가 바뀜", async ({
  page,
}) => {
  await mockStuck(
    page,
    { stage: "check", view: { ...views.remediation, animation: graphBfs } },
    [],
  );
  await page.setViewportSize({ width: 375, height: 812 });
  await openStackLesson(page);
  await page.getByRole("button", { name: "막혔어요", exact: true }).click();
  const player = panel(page).locator(".animation-player");
  await expect(player).toContainText("시작 정점 A부터 BFS");
  await expect(player).toContainText("1 / 7");
  // 가로 10칸(640) 장면도 패널 폭 안으로 줄고 페이지에 가로 스크롤이 생기지 않음
  const canvas = await player.locator(".animation-canvas").boundingBox();
  expect(canvas!.x).toBeGreaterThanOrEqual(0);
  expect(canvas!.x + canvas!.width).toBeLessThanOrEqual(375);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= 375),
  ).toBe(true);
  // 줄어든 뒤에도 정점 이름이 12px 이상으로 읽혀야 함
  const shown = await player.locator(".animation-canvas").evaluate((svg) => {
    const text = svg.querySelector(".shape-node text")!;
    const scale =
      svg.getBoundingClientRect().width /
      (svg as SVGSVGElement).viewBox.baseVal.width;
    return parseFloat(getComputedStyle(text).fontSize) * scale;
  });
  expect(shown).toBeGreaterThanOrEqual(12);
  // 무방향 간선 5개는 양쪽 방향 화살표 10개로 그려짐(재생기는 받은 화살표를 그대로 그림)
  await expect(player.locator(".animation-arrows line")).toHaveCount(10);
  await player.getByRole("button", { name: "다음 단계" }).click();
  await expect(player).toContainText("A 방문 → 큐 앞에서 꺼냄");
  await expect(player).toContainText("2 / 7");
  // A가 방금 방문한 정점으로 강조되고, 큐 칸은 B·C로 바뀜
  await expect(
    player.locator(".animation-box.shape-node", { hasText: "A" }),
  ).toHaveClass(/tone-new/);
  await expect(
    player.locator(".animation-box.shape-cell", { hasText: "C" }),
  ).toHaveCount(1);
  // 검수용 화면 기록(test-results는 Git 제외)
  await page.screenshot({ path: test.info().outputPath("graph-bfs-375.png") });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.screenshot({ path: test.info().outputPath("graph-bfs-1440.png") });
  await player.getByRole("button", { name: "이전 단계" }).click();
  await expect(player).toContainText("1 / 7");
  await expect(
    player.locator(".animation-box.shape-node", { hasText: "A" }),
  ).toHaveClass(/tone-normal/);
});

test("움직임 줄이기 설정이면 상자 이동 transition을 끔", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockStuck(
    page,
    {
      stage: "check",
      view: { ...views.remediation, animation: arrayInsert },
    },
    [],
  );
  await openStackLesson(page);
  await page.getByRole("button", { name: "막혔어요", exact: true }).click();
  const box = panel(page).locator(".animation-box").first();
  await expect(box).toBeVisible();
  expect(
    await box.evaluate((node) => getComputedStyle(node).transitionDuration),
  ).toBe("0s");
});

test("처음부터 다시: 확인 뒤에만 진행 중 세션을 버리고 같은 진입으로 새 세션을 시작함", async ({
  page,
}) => {
  const calls = await mockStuck(page, { stage: "diagnose", view: views.q1 }, [
    { stage: "diagnose", view: views.q2 },
  ]);
  await openStackLesson(page);
  await page.getByRole("button", { name: "막혔어요", exact: true }).click();
  const sheet = panel(page);
  await sheet.getByRole("radio", { name: /B\./ }).check();
  await sheet.getByRole("button", { name: "답 제출" }).click();
  await expect(sheet).toContainText("노드가 다음 노드를");
  // 실수 방지: 취소하면 아무 요청 없이 그대로 남음
  await sheet.getByRole("button", { name: "처음부터 다시" }).click();
  const confirm = sheet.getByRole("group", {
    name: "처음부터 다시 시작할까요?",
  });
  await confirm.getByRole("button", { name: "취소" }).click();
  await expect(confirm).toHaveCount(0);
  await expect(sheet).toContainText("노드가 다음 노드를");
  expect(calls).toHaveLength(2);
  await sheet.getByRole("button", { name: "처음부터 다시" }).click();
  await sheet
    .getByRole("group", { name: "처음부터 다시 시작할까요?" })
    .getByRole("button", { name: "다시 시작" })
    .click();
  await expect(sheet).toContainText("추상 자료형에 대한 설명");
  expect(calls.slice(2)).toEqual([
    {
      method: "POST",
      body: {
        entry: {
          kind: "roadmap",
          concept: "stack",
          return_to: "#/learn?concept=stack",
        },
      },
    },
  ]);
  expect(
    await page.evaluate(
      (key) => JSON.parse(localStorage.getItem(key)!),
      stuckKey,
    ),
  ).toEqual({
    version: 1,
    sessions: { "roadmap:stack": restartedId },
    active: "roadmap:stack",
  });
  // 새로고침 재개도 새 세션을 불러오고, 버린 세션은 조회하지 않음
  await page.reload();
  await expect(sheet).toContainText("추상 자료형에 대한 설명");
  expect(calls.filter((call) => call.method === "GET")).toHaveLength(1);
  expect(
    await page.evaluate(() =>
      performance
        .getEntriesByType("resource")
        .map((entry) => entry.name)
        .filter((name) => name.includes("/api/stuck/sessions/")),
    ),
  ).toEqual([expect.stringContaining(restartedId)]);
});

test("붙여넣기 세션의 처음부터 다시는 이전 문장을 채운 입력창으로 돌아감", async ({
  page,
}) => {
  const calls = await mockStuck(
    page,
    { stage: "diagnose", view: views.q1 },
    [],
  );
  await openStackLesson(page);
  await page.getByRole("button", { name: "막힌 문장 붙여넣기" }).click();
  const sheet = panel(page);
  await sheet
    .getByRole("textbox")
    .fill("스택을 배열로 구현하는 부분이 헷갈려요");
  await sheet.getByRole("button", { name: "이 문장으로 시작" }).click();
  await expect(sheet).toContainText("추상 자료형에 대한 설명");
  await sheet.getByRole("button", { name: "처음부터 다시" }).click();
  await sheet.getByRole("button", { name: "다시 시작" }).click();
  await expect(sheet.getByRole("textbox")).toHaveValue(
    "스택을 배열로 구현하는 부분이 헷갈려요",
  );
  expect(
    await page.evaluate(
      (key) => JSON.parse(localStorage.getItem(key)!).sessions,
      stuckKey,
    ),
  ).toEqual({});
  expect(calls).toHaveLength(1);
});
