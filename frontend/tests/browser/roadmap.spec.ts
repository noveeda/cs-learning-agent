import { test, expect, type Page } from "@playwright/test";

const roadmapKey = "cs-roadmap-v1";
const processTitle = "프로세스와 스레드";
const memoryTitle = "가상 메모리";
const question = (title: string) => `${title} 개념을 예시와 함께 설명해줘`;

test.beforeEach(async ({ page }) => {
  await page.route("**/api/conversations/*", (route) =>
    route.fulfill({ json: { messages: [] } }),
  );
});

async function openRoadmap(page: Page) {
  await page
    .locator(".sidebar")
    .getByRole("button", { name: "로드맵", exact: true })
    .click();
  await expect(page).toHaveURL(/#\/roadmap$/);
}

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
  await expect(page.locator(".roadmap-node")).toHaveCount(9);
  await page
    .getByRole("button", { name: `${memoryTitle} · 미학습`, exact: true })
    .click();
  const detail = page.getByRole("region", { name: "선택한 개념" });
  await expect(
    detail.getByRole("heading", { name: memoryTitle, exact: true }),
  ).toBeVisible();
  await page
    .getByLabel("학습 상태", { exact: true })
    .selectOption({ label: "완료" });
  await page.reload();
  await expect(
    detail.getByRole("heading", { name: memoryTitle, exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("학습 상태", { exact: true })).toHaveValue(
    "completed",
  );
  await startLearning(page);
  await expect(page).toHaveURL(/#\/learn\?concept=memory$/);
  await expect(page.getByLabel("질문", { exact: true })).toHaveValue(
    question(memoryTitle),
  );
  await expect
    .poll(() =>
      page.evaluate(
        (key) => JSON.parse(localStorage.getItem(key)!).statuses.memory,
        roadmapKey,
      ),
    )
    .toBe("completed");
  expect(requests).toBe(0);
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
  await expect(input).toHaveValue(question(processTitle));
  expect(calls).toHaveLength(1);
  await page.getByRole("button", { name: "질문 보내기" }).click();
  await expect(input).toBeEnabled();
  await openRoadmap(page);
  await expect(page.getByLabel("학습 상태", { exact: true })).toHaveValue(
    "in-progress",
  );
  await page
    .getByRole("button", { name: `${memoryTitle} · 미학습`, exact: true })
    .click();
  await startLearning(page);
  await page.getByRole("button", { name: "질문 보내기" }).click();
  await expect(input).toBeEnabled();
  expect(calls).toHaveLength(3);
  expect(new Set(calls.map((call) => call.conversation_id)).size).toBe(3);
  await page.reload();
  await expect(page.getByRole("log", { name: "대화 기록" })).toContainText(
    `${question(memoryTitle)} 답변`,
  );
  await openRoadmap(page);
  await page
    .getByRole("button", { name: `${processTitle} · 학습 중`, exact: true })
    .click();
  await startLearning(page);
  await expect(page.getByRole("log", { name: "대화 기록" })).toContainText(
    `${question(processTitle)} 답변`,
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
  expect(saved.roadmap.conversationIds.process).not.toBe(
    calls[1].conversation_id,
  );
  expect(saved.roadmap.conversationIds.memory).toBe(calls[2].conversation_id);
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
  test(`${width}px 모바일에서 개념 선택·학습 시작과 화면 너비 유지`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/#/roadmap");
    await page
      .getByRole("button", { name: `${memoryTitle} · 미학습`, exact: true })
      .click();
    await expect(
      page
        .getByRole("region", { name: "선택한 개념" })
        .getByRole("heading", { name: memoryTitle, exact: true }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await startLearning(page);
    await expect(page.getByLabel("질문", { exact: true })).toHaveValue(
      question(memoryTitle),
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
      body: '{"type":"answer","text":"프로세스 답변"}\n{"type":"done"}\n',
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
  await expect(page).toHaveURL(/#\/learn\?concept=process$/);
  await page.evaluate(() => {
    location.hash = "/learn?concept=memory";
  });
  await expect(page).toHaveURL(/#\/learn\?concept=process$/);
  release();
  await expect(page.getByLabel("질문", { exact: true })).toBeEnabled();
  await expect(page.getByRole("log", { name: "대화 기록" })).toContainText(
    "프로세스 답변",
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
  await page
    .getByRole("button", { name: `${memoryTitle} · 미학습`, exact: true })
    .click();
  await expect(
    page
      .getByRole("region", { name: "선택한 개념" })
      .getByRole("heading", { name: processTitle, exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "학습 시작", exact: true }).click();
  await expect(page).toHaveURL(/#\/roadmap$/);
});

test("데스크톱 로드맵은 중앙에서 영역별 원형 노드로 뻗는 방사형 배치", async ({
  page,
}) => {
  await page.goto("/#/roadmap");
  const root = page.locator(".roadmap-root");
  await expect(root).toHaveText("내 로드맵기본 CS");
  await expect(page.locator(".roadmap-domain")).toHaveCount(3);
  await expect(page.locator(".domain-edge")).toHaveCount(3);
  await expect(page.locator(".roadmap-node")).toHaveCount(9);
  const center = await root.boundingBox();
  expect(center).not.toBeNull();
  const positions = await page.locator(".roadmap-node").evaluateAll((nodes) =>
    nodes.map((node) => {
      const box = node.getBoundingClientRect();
      return {
        x: box.x + box.width / 2,
        y: box.y + box.height / 2,
        width: box.width,
        height: box.height,
        radius: getComputedStyle(node).borderRadius,
      };
    }),
  );
  const cx = center!.x + center!.width / 2;
  const cy = center!.y + center!.height / 2;
  expect(positions.some((node) => node.x < cx && node.y < cy)).toBe(true);
  expect(positions.some((node) => node.x > cx && node.y < cy)).toBe(true);
  expect(positions.some((node) => node.x < cx && node.y > cy)).toBe(true);
  expect(positions.some((node) => node.x > cx && node.y > cy)).toBe(true);
  for (const position of positions) {
    expect(position.width).toBe(position.height);
    expect(position.radius).toBe("50%");
  }
  await page
    .getByRole("button", { name: "가상 메모리 · 미학습", exact: true })
    .click();
  await expect(page.locator(".prerequisite-edge")).toHaveCount(1);
});
