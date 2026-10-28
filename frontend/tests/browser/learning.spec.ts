/**
 * @file
 * @brief 모의 API를 사용하는 실제 브라우저에서 대화·도식·오류·커리큘럼 전환을 검증
 */
import { readFileSync } from "node:fs";
import { test, expect, type Page } from "@playwright/test";

const conceptMap = JSON.parse(
  readFileSync(
    new URL("../../../ai-core/content/dsa/concept-map.json", import.meta.url),
    "utf8",
  ),
);

// 앱이 시작할 때마다 개념 지도를 요청하므로 개발 서버 프록시 뒤 실제 서버에 기대지 않게 고정 응답 제공
test.beforeEach(async ({ page }) => {
  await page.route("**/api/concept-map", (route) =>
    route.fulfill({ json: conceptMap }),
  );
});

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

/** @brief 실제 모델 생성 없이 고정 SVG 응답을 제공해 도식 UI만 검증 */
async function mockDiagram(page: Page) {
  await page.route("**/api/artifacts/diagram", (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="240"><text x="10" y="120">Process → Thread</text></svg>',
    }),
  );
}

/** @brief 원하는 문답 이력을 API 응답으로 주입해 화면 복구 시나리오 구성 */
async function mockConversation(page: Page, messages: unknown[] = []) {
  await page.route("**/api/conversations/*", (route) =>
    route.fulfill({ json: { messages } }),
  );
}

test("구현된 메뉴만 표시, 추천 질문, 모바일 메뉴의 닫기·초점 복원", async ({
  page,
}) => {
  await mockConversation(page);
  await page.goto("/");
  await expect(page.getByLabel("질문", { exact: true })).toBeEnabled();
  // 발표·실험에서 동작하지 않는 메뉴로 혼란이 없게, 구현된 화면(로드맵·학습하기·반 요약)만 메뉴에 보임
  await expect(
    page.getByRole("navigation", { name: "주 메뉴" }).first().getByRole("button"),
  ).toHaveText(["로드맵", "학습하기", "반 요약"]);
  await expect(page.locator(".sidebar small")).toHaveCount(0);
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
  await expect(menu.getByRole("button", { name: "준비 중" })).toHaveCount(0);
  await expect(menu.getByText("준비 중")).toHaveCount(0);
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

// AI core create_animation 결과(PR #44 실제 출력 형식)와 같은 애니메이션 artifact; SVG 경로(url)가 없음
const stackAnimation = {
  ok: true,
  artifact_id: "a".repeat(32),
  kind: "animation",
  animation: {
    kind: "stack",
    title: "스택 push·pop",
    frames: [
      { boxes: [], arrows: [], caption: "빈 스택에서 시작합니다" },
      {
        boxes: [
          { id: "s0", label: "1", x: 0, y: 1, shape: "cell", tone: "new" },
          {
            id: "top",
            label: "top",
            x: 1,
            y: 1,
            shape: "label",
            tone: "normal",
          },
        ],
        arrows: [{ source: "top", target: "s0", label: null }],
        caption: "push 1: 1을 top 위에 올립니다",
      },
    ],
  },
  request: { kind: "stack", ops: [{ op: "push", value: 1 }] },
};

test("애니메이션 artifact: 복구·단계 이동·수정 요청에 artifact_id 전송·새 결과로 교체", async ({
  page,
}) => {
  await mockConversation(page, [
    { role: "user", content: "스택 과정을 보여줘", status: "complete" },
    {
      role: "assistant",
      content: "push 과정입니다.",
      status: "complete",
      artifacts: [{ type: "artifact", ...stackAnimation }],
    },
  ]);
  const queue = {
    ...stackAnimation,
    artifact_id: "c".repeat(32),
    animation: {
      kind: "queue",
      title: "원형 큐 enqueue·dequeue",
      frames: [{ boxes: [], arrows: [], caption: "빈 큐에서 시작합니다" }],
    },
  };
  await page.route("**/api/chat", async (route) => {
    // 활성 애니메이션을 고친 결과를 받으려면 도식과 같은 방식으로 artifact_id를 보내야 함
    expect(route.request().postDataJSON().artifact_id).toBe(
      stackAnimation.artifact_id,
    );
    await route.fulfill({
      contentType: "application/x-ndjson",
      body:
        [
          { type: "artifact", ...queue },
          { type: "answer", text: "큐로 바꿨습니다." },
          { type: "done" },
        ]
          .map((event) => JSON.stringify(event))
          .join("\n") + "\n",
    });
  });
  // SVG 요청이 생기면 도식 패널로 잘못 그린 것이므로 마지막에 0건인지 확인
  const svgRequests: string[] = [];
  await page.route("**/api/artifacts/**", (route) => {
    svgRequests.push(route.request().url());
    return route.abort();
  });
  await page.goto("/");
  const panel = page.getByRole("complementary", { name: "생성된 애니메이션" });
  await expect(panel).toContainText("빈 스택에서 시작합니다");
  await expect(page.locator(".diagram-stage img")).toHaveCount(0);
  await expect(page.locator(".artifact-chip")).toContainText("스택 push·pop");
  await panel.getByRole("button", { name: "다음 단계" }).click();
  await expect(panel).toContainText("push 1: 1을 top 위에 올립니다");
  // head를 지정하지 않은 기존 장면의 화살표(top → 원소)는 화살촉을 그대로 그림
  await expect(panel.locator(".animation-arrows line[marker-end]")).toHaveCount(
    1,
  );
  // 세로 가운데 정렬이면 짧은 장면 위로 빈 점무늬 영역이 크게 남으므로 재생기가 무대 위쪽에 붙어 있는지 확인
  const stage = await panel.locator(".diagram-stage").boundingBox();
  const player = await panel.locator(".animation-player").boundingBox();
  expect(player!.y - stage!.y).toBeLessThan(40);
  // 입력 안내는 다음 결과의 종류를 모르므로 두 종류를 함께 알림
  await expect(page.locator(".composer-footer")).toContainText(
    "도식·애니메이션",
  );
  // 검수용 화면 기록(test-results는 Git 제외)
  await panel.screenshot({
    path: test.info().outputPath("chat-animation.png"),
  });
  const input = page.getByLabel("질문", { exact: true });
  await input.fill("큐로 바꿔줘");
  await input.press("Enter");
  await expect(panel).toContainText("원형 큐 enqueue·dequeue");
  await expect(panel).toContainText("빈 큐에서 시작합니다");
  // 좁은 화면에서도 가로 스크롤 없이 겹쳐 보이고 닫으면 입력창으로 돌아감
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(panel).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await panel.getByRole("button", { name: "애니메이션 닫기" }).click();
  await expect(page.locator(".artifact-panel")).toHaveCount(0);
  await expect(input).toBeFocused();
  // 닫은 뒤 다시 여는 버튼도 "도식"이 아니라 종류에 맞는 이름을 씀
  await expect(
    page.getByRole("button", { name: "애니메이션 열기" }).first(),
  ).toBeVisible();
  expect(svgRequests).toEqual([]);
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

test("개념 대화와 로드맵을 유지하며 커리큘럼 입력을 보존한다", async ({ page }) => {
  await mockConversation(page);
  await page.goto("/#/roadmap");
  await page.getByRole("button", { name: "학습 시작", exact: true }).click();
  await expect(page).toHaveURL(/#\/learn\?concept=memory-address$/);
  const question = page.getByLabel("질문", { exact: true });
  await expect(question).toBeEnabled();
  await question.fill("프로세스의 주소 공간을 설명해줘");
  await page.getByRole("button", { name: "커리큘럼 초안", exact: true }).click();
  await page.getByLabel("학습 주제", { exact: true }).fill("운영체제 기초");
  await page.getByRole("button", { name: "학습 대화로", exact: true }).click();
  await expect(question).toHaveValue("프로세스의 주소 공간을 설명해줘");
  await expect(page).toHaveURL(/#\/learn\?concept=memory-address$/);
  await page.locator(".sidebar").getByRole("button", { name: "로드맵", exact: true }).click();
  await expect(page.getByRole("heading", { name: "로드맵", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "커리큘럼 초안", exact: true }).click();
  await expect(page.getByLabel("학습 주제", { exact: true })).toHaveValue("운영체제 기초");
  await page.getByRole("button", { name: "로드맵으로", exact: true }).click();
  await expect(page.getByRole("heading", { name: "로드맵", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "커리큘럼 초안", exact: true }).click();
  await page.locator(".sidebar").getByRole("button", { name: "학습하기", exact: true }).click();
  await expect(question).toBeVisible();
  await expect(page.getByLabel("학습 주제", { exact: true })).toBeHidden();
});

// 하네스(CS_HARNESS=1) 답과 같은 모양: 문장마다 각주 [^p1], 확인 필요 문장 끝 ⚠️, 각주 정의는 원문 인용 + 출처
const harnessAnswer = [
  "단일 연결 리스트는 각 노드가 다음 노드를 가리킨다.[^p1] 맨 앞 삽입은 상수 시간에 끝난다.[^p1] ⚠️ **확인 필요**",
  "",
  "[^p1]: > A singly-linked list is a sequence of nodes. Each node stores a data value and a reference to the next node.",
  "",
  "    Open Data Structures, Pat Morin, 3.1 SLList, [CC BY 2.5](https://creativecommons.org/licenses/by/2.5/), 발췌",
  "",
  "**출처**",
  "",
  "- Open Data Structures, Pat Morin, 3.1 SLList, [CC BY 2.5](https://creativecommons.org/licenses/by/2.5/), 발췌",
].join("\n");
const figureId = "c".repeat(32);
const figure = {
  type: "artifact", kind: "figure", artifact_id: figureId, url: `/api/artifacts/${figureId}`,
  figure: {
    title: "Open Data Structures · 3.1",
    caption: "Figure 3.1: A sequence of operations on an SLList",
    source: "Open Data Structures, Pat Morin, 3.1, CC BY 2.5, 발췌",
  },
};
// 1×1 PNG; 그림 패널이 PNG를 오류 없이 띄우는지만 확인하면 되므로 내용은 상관없음
const pixelPng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=", "base64");

// 검증 목적: 하네스 진행 단계가 스트림 도중 문구로 보이고, 완료 답의 각주·⚠️ 확인 필요·교재 그림(캡션·출처·PNG 저장)이 보이며
// 마지막에 오는 grounding artifact가 그림을 가리지 않는지 확인
test("근거 하네스 답: 진행 단계 표시, 각주·확인 필요, 교재 그림 캡션·출처", async ({ page }) => {
  await mockConversation(page);
  await page.route(`**/api/artifacts/${figureId}`, (route) =>
    route.fulfill({ contentType: "image/png", body: pixelPng }),
  );
  // route.fulfill은 본문을 한 번에 보내므로, 진행 단계를 하나씩 보려면 /api/chat 응답 스트림을 테스트가 직접 흘려보냄
  await page.addInitScript(() => {
    const original = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      if (String(input) !== "/api/chat") return original(input, init);
      return new Response(new ReadableStream({
        start(controller) {
          (window as unknown as { chat: ReadableStreamDefaultController }).chat = controller;
        },
      }));
    };
  });
  /** @brief 이벤트 몇 개를 NDJSON 줄로 스트림에 넣음; close면 스트림을 닫음 */
  const push = (events: unknown[], close = false) =>
    page.evaluate(([lines, end]) => {
      const chat = (window as unknown as { chat: ReadableStreamDefaultController }).chat;
      chat.enqueue(new TextEncoder().encode(lines as string));
      if (end) chat.close();
    }, [events.map((e) => JSON.stringify(e) + "\n").join(""), close] as const);

  await page.goto("/");
  const input = page.getByLabel("질문", { exact: true });
  await input.fill("연결 리스트를 설명해줘");
  await input.press("Enter");
  await expect.poll(() => page.evaluate(() => "chat" in window)).toBe(true);
  await push([{ type: "status", stage: "scope" }]);
  await expect(page.locator(".status")).toHaveText("질문 범위 확인 중");
  await push([{ type: "status", stage: "search" }, { type: "status", stage: "write" },
    { type: "status", stage: "check", index: 2, total: 2 }]);
  await expect(page.locator(".status")).toHaveText("확인 중 (2/2)");
  await push([
    { type: "text_start" },
    { type: "text_delta", text: harnessAnswer.split("\n")[0] },
    figure,
    { type: "artifact", kind: "grounding", artifact_id: "b".repeat(32),
      grounding: { concepts: ["linked-list"], passage_ids: ["ods#3.1#p1"], verified: [] } },
    { type: "answer", text: harnessAnswer },
    { type: "done" },
  ], true);

  const answer = page.locator(".message.assistant").last();
  await expect(answer.locator("sup a[data-footnote-ref]")).toHaveCount(2);
  await expect(answer.locator("section[data-footnotes] blockquote")).toContainText("A singly-linked list");
  await expect(answer).toContainText("⚠️ 확인 필요");
  await expect(answer.getByRole("link", { name: "CC BY 2.5" }).first()).toBeVisible();
  const panel = page.getByRole("complementary", { name: "교재 그림" });
  await expect(panel.getByRole("heading", { name: figure.figure.title })).toBeVisible();
  await expect(panel).toContainText(figure.figure.caption);
  await expect(panel).toContainText(figure.figure.source);
  await expect(panel.getByRole("img", { name: figure.figure.title })).toBeVisible();
  const download = page.waitForEvent("download");
  await panel.getByRole("link", { name: "PNG 저장" }).click();
  expect((await download).suggestedFilename()).toBe(`${figure.figure.title}.png`);
  // 답 아래 artifact 단추가 grounding이 아니라 그림을 가리킴
  await expect(answer.locator(".artifact-chip")).toHaveText(figure.figure.title);
});
