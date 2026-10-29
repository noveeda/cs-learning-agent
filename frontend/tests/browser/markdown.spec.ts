/**
 * @file
 * @brief 실제 대화·스트리밍·막힘 보충·복귀 요약에서 공통 마크다운과 좁은 화면 스크롤 검증
 */
import { readFileSync } from "node:fs";
import { test, expect, type Locator } from "@playwright/test";

const conceptMap = JSON.parse(readFileSync(
  new URL("../../../ai-core/content/dsa/concept-map.json", import.meta.url), "utf8",
));
const answer = String.raw`**재귀(Recursion)**란 자신을 호출하는 함수입니다. **반복문(Iteration)**으로도 계산할 수 있습니다.

계산: $n!$, $5! = 5 \times 4$.

| 방식 | 장점 | 단점 | 공간 |
| :--- | :--- | :--- | ---: |
| 재귀 | 구조가 간결함 | 호출 비용 | O(n) |

$$
n! = n \times (n - 1)!
$$

예제 코드: ` + '`$n!$ **재귀(Recursion)**란`';

// 실제 UI를 그리되 모델·운영 저장소에 의존하지 않는 고정 API 응답 사용
test.beforeEach(async ({ page }) => {
  await page.route("**/api/concept-map", (route) =>
    route.fulfill({ json: { ...conceptMap, stuck_supported: ["linked-list"] } }),
  );
  await page.route("**/api/conversations/*", (route) =>
    route.fulfill({ json: { messages: [] } }),
  );
});

/** @brief 네 출력 위치에서 표·수식·한글 강조·코드 원문이 모두 유지되는지 확인 */
async function expectMarkdown(content: Locator) {
  await expect(content.locator("table")).toHaveCount(1);
  await expect(content.locator("th")).toHaveText(["방식", "장점", "단점", "공간"]);
  await expect(content.locator(".katex")).toHaveCount(3);
  await expect(content.locator(".katex-display")).toHaveCount(1);
  await expect(content.locator("strong")).toHaveText(["재귀(Recursion)", "반복문(Iteration)"]);
  await expect(content.locator("code")).toHaveText("$n!$ **재귀(Recursion)**란");
  await expect(content.locator(".katex-error")).toHaveCount(0);
}

test("완성 답변 복구: 표·수식·강조를 표시하고 390px에서 표만 가로 스크롤", async ({ page }) => {
  await page.route("**/api/conversations/*", (route) => route.fulfill({ json: {
    messages: [{ role: "assistant", content: answer, status: "complete" }],
  } }));
  await page.goto("/");
  const content = page.locator(".message.assistant .message-content");
  await expectMarkdown(content);
  for (const width of [1440, 1024, 390]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  const scroller = content.getByRole("region", { name: "표 (가로 스크롤)" });
  expect(await scroller.evaluate((node) => node.scrollWidth > node.clientWidth)).toBe(true);
  await scroller.focus();
  await page.keyboard.press("ArrowRight");
  await expect.poll(() => scroller.evaluate((node) => node.scrollLeft)).toBeGreaterThan(0);
});

test("스트리밍 중과 최종 답변에서 같은 표·수식·강조를 표시", async ({ page }) => {
  await page.goto("/");
  const input = page.getByLabel("질문", { exact: true });
  await expect(input).toBeEnabled();
  // 네트워크 경계에서 스트림 종료만 제어해 실제 draft 상태가 화면에 남도록 함
  await page.evaluate((text) => {
    const originalFetch = window.fetch;
    window.fetch = async (url, init) => {
      if (url !== "/api/chat") return originalFetch(url, init);
      const encoder = new TextEncoder();
      return new Response(new ReadableStream({
        start(controller) {
          const emit = (event: object) => controller.enqueue(encoder.encode(JSON.stringify(event) + "\n"));
          emit({ type: "text_start" });
          emit({ type: "text_delta", text });
          (window as Window & { finishMarkdownStream?: () => void }).finishMarkdownStream = () => {
            emit({ type: "answer", text });
            emit({ type: "done" });
            controller.close();
          };
        },
      }), { headers: { "Content-Type": "application/x-ndjson" } });
    };
  }, answer);
  await input.fill("재귀 개념을 예시와 함께 설명해줘");
  await input.press("Enter");
  await expectMarkdown(page.locator(".message.streaming .message-content"));
  await expect(input).toBeDisabled();
  await page.evaluate(() => (window as Window & { finishMarkdownStream: () => void }).finishMarkdownStream());
  await expect(page.locator(".message.streaming")).toHaveCount(0);
  await expectMarkdown(page.locator(".message.assistant .message-content"));
  await expect(input).toBeEnabled();
});

for (const kind of ["remediation", "summary"] as const) {
  test(`막힘 ${kind}: 공통 표·수식·강조와 패널 내부 스크롤`, async ({ page }) => {
    const concept = { id: "linked-list", title: "연결 리스트" };
    const view = kind === "remediation"
      ? { kind, text: answer, concept, attempt: 1, is_target: true }
      : { kind, text: answer, results: [], return_to: "#/learn?concept=linked-list" };
    await page.route("**/api/stuck/sessions", (route) => route.fulfill({ json: {
      id: "0123456789abcdef0123456789abcdef", version: 1,
      stage: kind === "remediation" ? "check" : "complete", view,
      entry: { kind: "roadmap", concept: "linked-list", return_to: "#/learn?concept=linked-list" },
    } }));
    // 저장된 대화가 없는 개념 직링크는 로드맵으로 돌아가므로 실제 학습 시작으로 대화 ID를 먼저 저장
    await page.goto("/#/roadmap");
    await page.getByRole("button", { name: "연결 리스트 · 미학습", exact: true }).click();
    await page.getByRole("button", { name: "학습 시작", exact: true }).click();
    await page.getByRole("button", { name: "막혔어요", exact: true }).click();
    const content = page.locator(".stuck-text");
    await expectMarkdown(content);
    const scroller = content.locator(".markdown-table");
    expect(await scroller.evaluate((node) => node.scrollWidth > node.clientWidth)).toBe(true);
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expectMarkdown(content);
  });
}

// 하네스 답은 각주 이름이 늘 p1부터라 같은 대화의 두 답이 같은 각주 이름을 씀
// 검증 목적: 두 번째 답의 각주 번호와 되돌아가기 링크가 첫 답이 아니라 자기 답 안으로 이동하는지 확인
test("두 답의 각주 링크는 각자 자기 답의 각주로 이동", async ({ page }) => {
  const footnoted = (text: string) => `${text}[^p1]\n\n[^p1]: > ${text} 원문`;
  await page.route("**/api/conversations/*", (route) => route.fulfill({ json: { messages: [
    { role: "user", content: "스택", status: "complete" },
    { role: "assistant", content: footnoted("첫 답"), status: "complete" },
    { role: "user", content: "큐", status: "complete" },
    { role: "assistant", content: footnoted("둘째 답"), status: "complete" },
  ] } }));
  await page.goto("/");
  const second = page.locator(".message.assistant").nth(1);
  await second.locator("a[data-footnote-ref]").click();
  await expect(second.locator("li:target")).toContainText("둘째 답 원문");
  await expect(page.locator(".message.assistant").first().locator(":target")).toHaveCount(0);
  await expect(second.getByRole("heading", { name: "각주" })).toHaveCount(1);
  await second.getByRole("link", { name: "본문으로 돌아가기" }).click();
  await expect(second.locator("a[data-footnote-ref]:target")).toHaveCount(1);
});
