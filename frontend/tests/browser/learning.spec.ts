import { test, expect, type Page } from "@playwright/test";

const artifact = {
  artifact_id: "diagram",
  url: "/api/artifacts/diagram",
  diagram: { title: "프로세스와 스레드" },
};
const restored = [
  { role: "user", content: "도식으로 설명해줘", status: "complete" },
  {
    role: "assistant",
    content: "프로세스 안에 스레드가 있습니다.",
    status: "complete",
    artifacts: [artifact],
  },
];

async function mockDiagram(page: Page) {
  await page.route("**/api/artifacts/diagram", (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="240"><text x="10" y="120">Process → Thread</text></svg>',
    }),
  );
}

async function mockConversation(page: Page, messages: unknown[] = []) {
  await page.route("**/api/conversations/*", (route) =>
    route.fulfill({ json: { messages } }),
  );
}

test("준비 중 메뉴와 추천 질문, 모바일 메뉴의 닫기·초점 복원", async ({
  page,
}) => {
  await mockConversation(page);
  await page.goto("/");
  await expect(page.getByLabel("질문", { exact: true })).toBeEnabled();
  await expect(page.locator(".sidebar button:disabled")).toHaveCount(6);
  await expect(page.locator(".sidebar small")).toHaveText(
    Array(6).fill("준비 중"),
  );
  await page
    .getByRole("button", {
      name: "프로세스와 스레드는 어떻게 달라?",
      exact: true,
    })
    .click();
  await expect(page.getByLabel("질문", { exact: true })).toHaveValue(
    "프로세스와 스레드는 어떻게 달라?",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "메뉴 열기" }).click();
  const menu = page.getByRole("dialog", { name: "주 메뉴", exact: true });
  await expect(menu).toBeVisible();
  await expect(menu.locator("button:disabled")).toHaveCount(6);
  await page.keyboard.press("Escape");
  await expect(menu).not.toBeVisible();
  await expect(page.getByRole("button", { name: "메뉴 열기" })).toBeFocused();
});

test("대화·도식 복구, 확대·저장, 화면 크기 변경과 새 학습", async ({
  page,
}) => {
  await mockConversation(page, restored);
  await mockDiagram(page);
  await page.goto("/");
  await expect(page.locator(".messages article")).toHaveCount(2);
  await expect(
    page.getByRole("img", { name: artifact.diagram.title }),
  ).toBeVisible();
  await page.getByRole("button", { name: "도식 확대", exact: true }).click();
  await expect(page.locator(".diagram-stage img")).toHaveCSS("width", "940px");
  await page.getByRole("button", { name: "도식 화면에 맞춤" }).click();
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("link", { name: "SVG 저장" }).click();
  expect((await downloadEvent).suggestedFilename()).toBe(
    "프로세스와 스레드.svg",
  );
  await page.reload();
  await expect(page.locator(".messages article")).toHaveCount(2);
  for (const width of [1024, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    if (width <= 850) {
      const dialog = page.getByRole("dialog", { name: artifact.diagram.title });
      await expect(dialog).toBeVisible();
      await expect
        .poll(() => dialog.evaluate((d) => d.matches(":modal")))
        .toBe(true);
    }
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(page.locator(".artifact-panel")).toHaveCount(0);
  await page
    .getByRole("button", { name: "도식 열기", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("dialog", { name: artifact.diagram.title }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page.unroute("**/api/conversations/*");
  await mockConversation(page);
  await page.getByRole("button", { name: "새 학습", exact: true }).click();
  await expect(page.getByLabel("질문", { exact: true })).toBeEnabled();
  await expect(page.locator(".messages article")).toHaveCount(0);
  await expect(page.locator(".artifact-panel")).toHaveCount(0);
});

test("응답 중 중복 전송 방지와 실패 시 질문·부분 답변 유지", async ({
  page,
}) => {
  await mockConversation(page);
  let release: () => void;
  const responseReady = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/chat", async (route) => {
    const request = route.request().postDataJSON();
    expect(request.question).toBe("실패 확인");
    await responseReady;
    await route.fulfill({
      contentType: "application/x-ndjson",
      body:
        [
          { type: "text_start" },
          { type: "text_delta", text: "부분 답변" },
          { type: "error", message: "검증용 실패" },
        ]
          .map((event) => JSON.stringify(event))
          .join("\n") + "\n",
    });
  });
  await page.goto("/");
  const input = page.getByLabel("질문", { exact: true });
  await expect(input).toBeEnabled();
  await input.fill("실패 확인");
  await input.press("Shift+Enter");
  await expect(input).toBeEnabled();
  await input.fill("실패 확인");
  await input.press("Enter");
  await expect(input).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "새 학습", exact: true }),
  ).toBeDisabled();
  release();
  await expect(page.getByRole("alert")).toContainText("검증용 실패");
  await expect(input).toHaveValue("실패 확인");
  await expect(input).toBeEnabled();
  await expect(page.locator(".message.assistant")).toContainText("부분 답변");
});

test("불러오기 실패 후 재시도하며 기존 대화를 덮어쓰지 않음", async ({
  page,
}) => {
  let failed = true;
  await page.route("**/api/conversations/*", (route) =>
    failed
      ? route.fulfill({ status: 503, body: "{}" })
      : route.fulfill({ json: { messages: restored } }),
  );
  await mockDiagram(page);
  await page.goto("/");
  await expect(page.getByLabel("질문", { exact: true })).toBeDisabled();
  failed = false;
  await page.getByRole("button", { name: "다시 불러오기" }).click();
  await expect(page.getByLabel("질문", { exact: true })).toBeEnabled();
  await expect(page.locator(".messages article")).toHaveCount(2);
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("커리큘럼과 학습 화면을 전환해도 입력을 보존한다", async ({ page }) => {
  await mockConversation(page);
  await page.goto("/");
  await page.getByRole("button", { name: "커리큘럼 초안", exact: true }).click();
  await page.getByLabel("학습 주제", { exact: true }).fill("운영체제 전체");
  await page.getByRole("button", { name: "학습 대화로", exact: true }).click();
  await expect(page.getByLabel("질문", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "커리큘럼 초안", exact: true }).click();
  await expect(page.getByLabel("학습 주제", { exact: true })).toHaveValue("운영체제 전체");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "학습 대화로", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
