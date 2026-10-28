/**
 * @file
 * @brief 브라우저에서 개념 지도 표시·선택·학습 상태 복구·관계 강조·로딩 실패·키보드 및 모바일 동작을 검증
 */
import { readFileSync } from "node:fs";
import { test, expect, type Page } from "@playwright/test";

// 실제 API가 중계하는 검수 완료 지도를 그대로 응답해 36개 배치를 검증
const conceptMap = JSON.parse(
  readFileSync(
    new URL("../../../ai-core/content/dsa/concept-map.json", import.meta.url),
    "utf8",
  ),
);
const roadmapKey = "cs-dsa-roadmap-v1";
const firstTitle = "메모리 주소와 연속 배치";
const stackTitle = "스택";
const question = (title: string) => `${title} 개념을 예시와 함께 설명해줘`;
/** @brief 개념 제목과 학습 상태로 지도 노드 버튼 찾기 */
const node = (page: Page, title: string, status = "미학습") =>
  page.getByRole("button", { name: `${title} · ${status}`, exact: true });

test.beforeEach(async ({ page }) => {
  await page.route("**/api/conversations/*", (route) =>
    route.fulfill({ json: { messages: [] } }),
  );
  await page.route("**/api/concept-map", (route) =>
    route.fulfill({ json: conceptMap }),
  );
});

/** @brief 학습 화면에서 로드맵 메뉴를 열어 사용자 진입 경로 재현 */
async function openRoadmap(page: Page) {
  await page
    .locator(".sidebar")
    .getByRole("button", { name: "로드맵", exact: true })
    .click();
  await expect(page).toHaveURL(/#\/roadmap$/);
}

/** @brief 현재 개념의 학습 진입 버튼을 눌러 연결된 대화 화면으로 이동 */
async function startLearning(page: Page) {
  await page.getByRole("button", { name: /^학습 (시작|이어가기)$/ }).click();
  await expect(page.getByLabel("질문", { exact: true })).toBeEnabled();
}

test("개념 선택·상태를 새로고침 후 복구하고 완료 상태로 학습을 시작함", async ({
  page,
}) => {
  let requests = 0;
  await page.route("**/api/chat", (route) => {
    requests += 1;
    return route.fulfill({ status: 500 });
  });
  await page.goto("/#/roadmap");
  await expect(page.locator(".roadmap-node")).toHaveCount(36);
  await node(page, stackTitle).click();
  const detail = page.getByRole("region", { name: "선택한 개념" });
  await expect(
    detail.getByRole("heading", { name: stackTitle, exact: true }),
  ).toBeVisible();
  await page
    .getByLabel("학습 상태", { exact: true })
    .selectOption({ label: "완료" });
  await page.reload();
  await expect(
    detail.getByRole("heading", { name: stackTitle, exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("학습 상태", { exact: true })).toHaveValue(
    "completed",
  );
  await expect(node(page, stackTitle, "완료")).toBeVisible();
  await startLearning(page);
  await expect(page).toHaveURL(/#\/learn\?concept=stack$/);
  await expect(page.getByLabel("질문", { exact: true })).toHaveValue(
    question(stackTitle),
  );
  await expect
    .poll(() =>
      page.evaluate(
        (key) => JSON.parse(localStorage.getItem(key)!).statuses.stack,
        roadmapKey,
      ),
    )
    .toBe("completed");
  expect(requests).toBe(0);
});

test("상세에 학습 목표·선수 개념 근거·후속 개념을 보여 주고 이동함", async ({
  page,
}) => {
  await page.goto("/#/roadmap");
  await node(page, stackTitle).click();
  const detail = page.getByRole("region", { name: "선택한 개념" });
  const objectives = conceptMap.concepts.find(
    (concept: { id: string }) => concept.id === "stack",
  ).objectives as string[];
  for (const objective of objectives)
    await expect(detail.locator(".concept-objectives")).toContainText(
      objective,
    );
  await expect(detail).toContainText("7단계");
  await expect(detail).toContainText("추상 자료형");
  await detail
    .getByRole("button", { name: "깊이 우선 탐색", exact: true })
    .click();
  await expect(detail.getByRole("heading", { level: 2 })).toHaveText(
    "깊이 우선 탐색",
  );
  // DFS는 선수 개념이 셋: 각 버튼 아래에 지도에 적힌 근거를 함께 표시
  await expect(
    detail.locator(".concept-links").first().locator("li"),
  ).toHaveCount(3);
  await detail.getByRole("button", { name: "재귀", exact: true }).click();
  await expect(detail).toContainText("기초 개념부터 바로 시작할 수 있어요.");
});

test("자유 학습·개념별 대화를 분리하고 새 학습은 해당 개념만 교체함", async ({
  page,
}) => {
  const calls: { question: string; conversation_id: string }[] = [];
  const conversations = new Map<
    string,
    { role: string; content: string; status: string }[]
  >();
  await page.route("**/api/conversations/*", (route) => {
    const id = new URL(route.request().url()).pathname.split("/").at(-1)!;
    return route.fulfill({ json: { messages: conversations.get(id) || [] } });
  });
  await page.route("**/api/chat", (route) => {
    const body = route.request().postDataJSON();
    calls.push(body);
    const answer = `${body.question} 답변`;
    conversations.set(body.conversation_id, [
      { role: "user", content: body.question, status: "complete" },
      { role: "assistant", content: answer, status: "complete" },
    ]);
    return route.fulfill({
      contentType: "application/x-ndjson",
      body: `${JSON.stringify({ type: "answer", text: answer })}\n{"type":"done"}\n`,
    });
  });
  await page.goto("/");
  const input = page.getByLabel("질문", { exact: true });
  await expect(input).toBeEnabled();
  await input.fill("자유 질문");
  await page.getByRole("button", { name: "질문 보내기" }).click();
  await expect(input).toBeEnabled();
  await openRoadmap(page);
  await startLearning(page);
  await expect(input).toHaveValue(question(firstTitle));
  expect(calls).toHaveLength(1);
  await page.getByRole("button", { name: "질문 보내기" }).click();
  await expect(input).toBeEnabled();
  await openRoadmap(page);
  await expect(page.getByLabel("학습 상태", { exact: true })).toHaveValue(
    "in-progress",
  );
  await node(page, stackTitle).click();
  await startLearning(page);
  await page.getByRole("button", { name: "질문 보내기" }).click();
  await expect(input).toBeEnabled();
  expect(calls).toHaveLength(3);
  expect(new Set(calls.map((call) => call.conversation_id)).size).toBe(3);
  await page.reload();
  await expect(page.getByRole("log", { name: "대화 기록" })).toContainText(
    `${question(stackTitle)} 답변`,
  );
  await openRoadmap(page);
  await node(page, firstTitle, "학습 중").click();
  await startLearning(page);
  await expect(page.getByRole("log", { name: "대화 기록" })).toContainText(
    `${question(firstTitle)} 답변`,
  );
  await page.getByRole("button", { name: "새 학습", exact: true }).click();
  await expect(input).toBeEnabled();
  await expect(page.getByRole("log", { name: "대화 기록" })).toHaveCount(0);
  const saved = await page.evaluate(
    (key) => ({
      roadmap: JSON.parse(localStorage.getItem(key)!),
      free: localStorage.getItem("cs-conversation"),
    }),
    roadmapKey,
  );
  expect(saved.roadmap.conversationIds["memory-address"]).not.toBe(
    calls[1].conversation_id,
  );
  expect(saved.roadmap.conversationIds.stack).toBe(calls[2].conversation_id);
  expect(saved.free).toBe(calls[0].conversation_id);
  await page
    .locator(".sidebar")
    .getByRole("button", { name: "학습하기", exact: true })
    .click();
  await expect(page.getByRole("log", { name: "대화 기록" })).toContainText(
    "자유 질문 답변",
  );
});

for (const width of [320, 390]) {
  test(`${width}px 모바일에서 단계별 목록으로 개념 선택·학습 시작과 화면 너비 유지`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/#/roadmap");
    await expect(page.locator(".roadmap-edges")).toBeHidden();
    await expect(
      page.getByRole("group", { name: "1단계", exact: true }),
    ).toBeVisible();
    await expect(node(page, stackTitle)).toContainText("먼저: 추상 자료형");
    await node(page, stackTitle).click();
    await expect(
      page
        .getByRole("region", { name: "선택한 개념" })
        .getByRole("heading", { name: stackTitle, exact: true }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await startLearning(page);
    await expect(page.getByLabel("질문", { exact: true })).toHaveValue(
      question(stackTitle),
    );
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });
}

test("응답 중 직접 주소 변경을 막고 원래 개념에 답변을 유지함", async ({
  page,
}) => {
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/chat", async (route) => {
    await held;
    await route.fulfill({
      contentType: "application/x-ndjson",
      body: '{"type":"answer","text":"메모리 주소 답변"}\n{"type":"done"}\n',
    });
  });
  await page.goto("/#/roadmap");
  await startLearning(page);
  await page.getByRole("button", { name: "질문 보내기" }).click();
  await expect(page.getByLabel("질문", { exact: true })).toBeDisabled();
  await expect(
    page
      .locator(".sidebar")
      .getByRole("button", { name: "로드맵", exact: true }),
  ).toBeDisabled();
  await page.evaluate(() => history.back());
  await expect(page.getByRole("alert")).toContainText("응답을 기다린 뒤");
  await expect(page).toHaveURL(/#\/learn\?concept=memory-address$/);
  await page.evaluate(() => {
    location.hash = "/learn?concept=stack";
  });
  await expect(page).toHaveURL(/#\/learn\?concept=memory-address$/);
  release();
  await expect(page.getByLabel("질문", { exact: true })).toBeEnabled();
  await expect(page.getByRole("log", { name: "대화 기록" })).toContainText(
    "메모리 주소 답변",
  );
});

test("저장 실패를 알리고 선택·학습 상태·이동을 적용하지 않음", async ({
  page,
}) => {
  await page.goto("/#/roadmap");
  await expect(page.getByLabel("학습 상태", { exact: true })).toHaveValue(
    "not-started",
  );
  await page.evaluate((key) => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function (name, value) {
      if (name === key)
        throw new DOMException("저장 공간 부족", "QuotaExceededError");
      return original.call(this, name, value);
    };
  }, roadmapKey);
  await page
    .getByLabel("학습 상태", { exact: true })
    .selectOption({ label: "완료" });
  await expect(page.getByRole("alert")).toContainText("저장하지 못했습니다");
  await expect(page.getByLabel("학습 상태", { exact: true })).toHaveValue(
    "not-started",
  );
  await node(page, stackTitle).click();
  await expect(
    page
      .getByRole("region", { name: "선택한 개념" })
      .getByRole("heading", { name: firstTitle, exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "학습 시작", exact: true }).click();
  await expect(page).toHaveURL(/#\/roadmap$/);
});

test("데스크톱 지도는 선수 개념을 항상 위 단계에 두고 1440px 카드 안에 들어감", async ({
  page,
}) => {
  await page.goto("/#/roadmap");
  await expect(page.locator(".roadmap-node")).toHaveCount(36);
  await expect(page.locator(".roadmap-layer")).toHaveCount(10);
  await expect(page.locator(".prerequisite-edge")).toHaveCount(51);
  const boxes = Object.fromEntries(
    await page.locator(".roadmap-node").evaluateAll((nodes) =>
      nodes.map((item) => {
        const box = item.getBoundingClientRect();
        return [item.getAttribute("data-concept"), box];
      }),
    ),
  );
  for (const concept of conceptMap.concepts as {
    id: string;
    prerequisites: { id: string }[];
  }[])
    for (const p of concept.prerequisites)
      expect(boxes[p.id].bottom).toBeLessThan(boxes[concept.id].top);
  // 노드끼리 겹치지 않음
  const list = Object.values(boxes) as DOMRect[];
  for (const [i, a] of list.entries())
    for (const b of list.slice(i + 1))
      expect(
        a.right <= b.left ||
          b.right <= a.left ||
          a.bottom <= b.top ||
          b.bottom <= a.top,
      ).toBe(true);
  expect(
    await page
      .locator(".roadmap-scroll")
      .evaluate((item) => item.scrollWidth <= item.clientWidth),
  ).toBe(true);
});

test("노드 호버는 직접 선수·후속 개념과 화살표 선을 강조하고 클릭 선택을 바꾸지 않음", async ({
  page,
}) => {
  await page.goto("/#/roadmap");
  // 웹 글꼴이 늦게 적용되면 지도가 아래로 밀려 마우스가 노드 밖에 남으므로 글꼴 적용 뒤 호버
  await page.evaluate(() => document.fonts.ready);
  const stack = node(page, stackTitle);
  const first = node(page, firstTitle);
  // 선택 개념(첫 개념)의 선은 탐색하지 않아도 강조
  await expect(
    page.locator('.prerequisite-edge[data-emphasis="related"]'),
  ).toHaveCount(2);
  await stack.hover();
  await expect(stack).toHaveAttribute("data-active", "true");
  await expect(node(page, "추상 자료형과 구현")).toHaveAttribute(
    "data-emphasis",
    "related",
  );
  await expect(node(page, "깊이 우선 탐색")).toHaveAttribute(
    "data-emphasis",
    "related",
  );
  await expect(node(page, "해시 함수")).toHaveCSS("opacity", "0.3");
  const strong = page.locator('.prerequisite-edge[data-emphasis="related"]');
  await expect(strong).toHaveCount(2);
  expect(
    await strong.evaluateAll((lines) =>
      lines.map((line) => [
        line.getAttribute("data-source"),
        line.getAttribute("data-target"),
        line.getAttribute("marker-end"),
      ]),
    ),
  ).toEqual([
    ["adt", "stack", "url(#roadmap-arrow)"],
    ["stack", "dfs", "url(#roadmap-arrow)"],
  ]);
  await expect(first).toHaveAttribute("aria-pressed", "true");
  await expect(
    page
      .getByRole("region", { name: "선택한 개념" })
      .getByRole("heading", { level: 2 }),
  ).toHaveText(firstTitle);
  await page.getByRole("heading", { name: "로드맵", exact: true }).hover();
  await expect(stack).toHaveAttribute("data-active", "false");
  await expect(node(page, "해시 함수")).toHaveCSS("opacity", "1");
});

test("키보드 초점도 관계를 강조하고 움직임 줄이기 설정을 따름", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/#/roadmap");
  const reference = node(page, "참조와 포인터");
  await node(page, "점근 표기법").focus();
  await page.keyboard.press("Shift+Tab");
  await expect(reference).toBeFocused();
  await expect(reference).toHaveAttribute("data-active", "true");
  await expect(node(page, firstTitle)).toHaveAttribute(
    "data-emphasis",
    "related",
  );
  await expect(node(page, "연결 리스트")).toHaveAttribute(
    "data-emphasis",
    "related",
  );
  await expect(reference).toHaveCSS("transition-duration", "0s");
  const dimensions = await reference.evaluate((item) => ({
    width: item.getBoundingClientRect().width,
    cssWidth: parseFloat(getComputedStyle(item).width),
  }));
  expect(dimensions.width).toBe(dimensions.cssWidth);
  await page.keyboard.press("Tab");
  await expect(reference).toHaveAttribute("data-active", "false");
});

test("개념 지도 로딩 실패를 안내하고 다시 시도하면 지도를 그림", async ({
  page,
}) => {
  let fail = true;
  await page.route("**/api/concept-map", (route) =>
    fail
      ? route.fulfill({
          status: 502,
          json: {
            message:
              "개념 지도를 불러오지 못했습니다. AI core 상태를 확인해 주세요.",
          },
        })
      : route.fulfill({ json: conceptMap }),
  );
  await page.goto("/#/roadmap");
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("AI core 상태를 확인해 주세요.");
  await expect(page.locator(".roadmap-node")).toHaveCount(0);
  fail = false;
  await alert.getByRole("button", { name: "다시 시도" }).click();
  await expect(page.locator(".roadmap-node")).toHaveCount(36);
});

test("지도를 받는 동안 로딩 안내를 보여 줌", async ({ page }) => {
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/concept-map", async (route) => {
    await held;
    await route.fulfill({ json: conceptMap });
  });
  await page.goto("/#/roadmap");
  await expect(page.getByRole("status")).toContainText(
    "개념 지도를 불러오는 중",
  );
  release();
  await expect(page.locator(".roadmap-node")).toHaveCount(36);
});

test("옛 9개 개념 저장값과 개념 주소는 무시하되 지우지 않음", async ({
  page,
}) => {
  const old = JSON.stringify({
    version: 1,
    selectedId: "process",
    statuses: { process: "completed" },
    conversationIds: { process: "0123456789abcdef0123456789abcdef" },
  });
  await page.addInitScript((value) => {
    if (!localStorage.getItem("cs-roadmap-v1"))
      localStorage.setItem("cs-roadmap-v1", value);
  }, old);
  await page.goto("/#/learn?concept=process");
  await expect(page).toHaveURL(/concept=process$/);
  await expect(
    page.getByRole("heading", { name: "로드맵", exact: true }),
  ).toBeVisible();
  await expect(node(page, firstTitle)).toHaveAttribute("aria-pressed", "true");
  await node(page, stackTitle).click();
  expect(await page.evaluate(() => localStorage.getItem("cs-roadmap-v1"))).toBe(
    old,
  );
});

/**
 * @brief 여러 과목을 합친 지도 응답(MVP 4 형식)의 목업; 자료구조 지도 뒤에 운영체제 개념 2개를 붙임
 * - process-state → queue는 과목 사이 선수 관계라 운영체제만 걸렀을 때 선이 사라져야 함
 */
const mixedMap = {
  ...conceptMap,
  id: "cs",
  title: "CS 전공",
  subjects: [
    { id: "dsa", title: "자료구조·알고리즘" },
    { id: "os", title: "운영체제" },
  ],
  concepts: [
    ...conceptMap.concepts.map((concept: object) => ({
      ...concept,
      subject: "dsa",
    })),
    {
      id: "process-state",
      title: "프로세스와 상태",
      description: "실행 중인 프로그램과 그 상태 변화",
      objectives: ["프로세스 상태 전이를 설명할 수 있다."],
      prerequisites: [{ id: "queue", reason: "준비 큐가 큐다." }],
      subject: "os",
    },
    {
      id: "fcfs",
      title: "FCFS 스케줄링",
      description: "도착 순서대로 CPU를 배정",
      objectives: ["FCFS 대기 시간을 계산할 수 있다."],
      prerequisites: [{ id: "process-state", reason: "상태를 알아야 함" }],
      subject: "os",
    },
  ],
};

test("여러 과목 지도는 과목 표식·필터를 보여 주고 과목 사이 선수 개념으로 이동함", async ({
  page,
}) => {
  await page.route("**/api/concept-map", (route) =>
    route.fulfill({ json: mixedMap }),
  );
  await page.goto("/#/roadmap");
  await expect(page.locator(".roadmap-node")).toHaveCount(38);
  await expect(page.locator('.roadmap-node[data-subject="os"]')).toHaveCount(2);
  const filter = page.getByRole("group", { name: "과목" });
  await filter.getByRole("button", { name: "운영체제" }).click();
  // 숨긴 자료구조 개념(queue)을 가리키던 선은 그리지 않음
  await expect(page.locator(".roadmap-node")).toHaveCount(2);
  await expect(page.locator(".prerequisite-edge")).toHaveCount(1);
  await expect(
    page.locator('.prerequisite-edge[data-source="queue"]'),
  ).toHaveCount(0);
  await node(page, "프로세스와 상태 · 운영체제").click();
  const detail = page.getByRole("region", { name: "선택한 개념" });
  await expect(detail).toContainText("운영체제 · 1단계");
  // 상세 패널은 과목 사이 선수 개념을 그대로 보여 주고, 누르면 필터를 풀어 그 개념을 지도에 보여 줌
  await detail.getByRole("button", { name: "큐", exact: true }).click();
  await expect(filter.getByRole("button", { name: "전체" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.locator(".roadmap-node")).toHaveCount(38);
  await expect(node(page, "큐 · 자료구조·알고리즘")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
});
