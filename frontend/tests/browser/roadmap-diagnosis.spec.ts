/**
 * @file
 * @brief 모의 막힘 API로 과목 사이 진단 경로·이해 확인 판정·닫기·재시작·새로고침 복구와 실험 화면 격리를 검증
 */
import { readFileSync } from "node:fs";
import { test, expect, type Page } from "@playwright/test";

const dsa = JSON.parse(readFileSync(new URL("../../../ai-core/content/dsa/concept-map.json", import.meta.url), "utf8"));
const arch = JSON.parse(readFileSync(new URL("../../../ai-core/content/arch/concept-map.json", import.meta.url), "utf8"));
// 과목 사이 진단 경로: 캐시와 지역성(컴퓨터구조) ← 메모리 계층·배열 ← 메모리 주소(자료구조)
// - 6단계 이상 자료구조 개념(우선순위 큐 등)은 지도에서 숨기므로, 보이는 개념끼리 이어지는 경로로 검증
// - memory-address → array는 여러 단계를 건너는 긴 간선이라 다른 칸을 지나지 않는지도 함께 봄
const target = { id: "cache-locality", title: "캐시와 지역성" };
const gap = { id: "memory-address", title: "메모리 주소와 연속 배치" };
const sessionId = "0123456789abcdef0123456789abcdef";
const restartedId = "fedcba9876543210fedcba9876543210";
const question = {
  kind: "question", purpose: "diagnosis", concept: gap,
  item: { id: "memory-address-d1", format: "short_exact", question: "4바이트 원소 배열의 다음 원소 주소는?" },
};
const diagnosis = { kind: "diagnosis_result", target, gaps: [gap], evidence: [], opted_out: false };
const remediation = { kind: "remediation", concept: gap, attempt: 1, text: "연속 배치에서는 원소 크기만큼 주소가 늘어요.", is_target: false };
const passed = { kind: "result", concept: gap, passed: true };
const summary = {
  kind: "summary", text: "빈틈을 채웠어요.", return_to: "#/roadmap",
  results: [{ concept: gap, outcome: "passed", attempts: 1 }, { concept: target, outcome: "unresolved", attempts: 2 }],
};
const panel = (page: Page) => page.getByRole("complementary", { name: "막힘 풀기" });
const node = (page: Page, id: string) => page.locator(`.roadmap-node[data-concept="${id}"]`);
const path = (page: Page, from: string, to: string) => page.locator(`.prerequisite-edge[data-source="${from}"][data-target="${to}"]`);

test.beforeEach(async ({ page }) => {
  await page.route("**/api/concept-map", route => route.fulfill({ json: {
    ...dsa, id: "cs", title: "CS 전공", subjects: [{ id: "dsa", title: dsa.title }, { id: "arch", title: arch.title }],
    concepts: [...dsa.concepts.map((c: object) => ({ ...c, subject: "dsa" })), ...arch.concepts.map((c: object) => ({ ...c, subject: "arch" }))],
    stuck_supported: ["cache-locality", "memory-address"],
  } }));
  await page.route("**/api/conversations/*", route => route.fulfill({ json: { messages: [] } }));
});

/** @brief 외부 API만 대체하고 실제 패널·상태 저장·지도 렌더링을 함께 실행; GET은 직전 체크포인트를 재사용 */
async function mockSession(page: Page) {
  let session: any;
  let created = 0;
  let expired = false;
  let stepGate: Promise<void> | null = null;
  const steps = [
    { stage: "remediate", view: diagnosis }, { stage: "check", view: remediation },
    { stage: "return", view: passed }, { stage: "complete", view: summary },
  ];
  await page.route("**/api/stuck/sessions**", async route => {
    const request = route.request();
    if (request.method() === "GET")
      return expired ? route.fulfill({ status: 404, json: { message: "막힘 세션을 찾을 수 없습니다." } }) : route.fulfill({ json: session });
    const body = request.postDataJSON();
    if (!request.url().endsWith("/step")) {
      session = { id: created++ ? restartedId : sessionId, version: 1, stage: "diagnose", view: question, entry: body.entry };
    } else {
      if (body.version !== session.version) return route.fulfill({ status: 409, json: { message: "세션 상태가 변경됐습니다." } });
      session = { ...session, ...steps.shift(), version: session.version + 1 };
    }
    const response = session;
    if (request.url().endsWith("/step") && stepGate) await stepGate;
    return route.fulfill({ json: response });
  });
  return {
    expire() { expired = true; },
    holdNextStep() {
      let release!: () => void;
      stepGate = new Promise<void>(resolve => { release = resolve; });
      return () => { release(); stepGate = null; };
    },
  };
}

/** @brief 실제 지도에서 캐시와 지역성을 선택해 진단 결과까지 진행 */
async function diagnose(page: Page) {
  await page.goto("/#/roadmap");
  await page.getByRole("group", { name: "과목", exact: true }).getByRole("button", { name: arch.title, exact: true }).click();
  await expect(node(page, "memory-address")).toHaveCount(0);
  await node(page, "cache-locality").click();
  await page.getByRole("button", { name: "막혔어요", exact: true }).click();
  await panel(page).getByLabel("4바이트 원소 배열의 다음 원소 주소는?").fill("1004");
  await panel(page).getByRole("button", { name: "답 제출", exact: true }).click();
  await expect(panel(page).locator(".stuck-gaps")).toContainText("메모리 주소와 연속 배치");
}

/** @brief 진단의 실제 선수 경로를 강조하고 문항을 지난 뒤에도 표시·통과 판정을 복원하는지 검증 */
test("캐시와 지역성의 자료구조 빈틈과 실제 선수 경로를 표시하고 보충·통과 단계 새로고침에서 복구함", async ({ page }) => {
  await mockSession(page);
  await diagnose(page);
  await expect(node(page, "memory-address")).toContainText("모름");
  await expect(node(page, "memory-address")).toHaveAttribute("data-diagnosis", "gap");
  await expect(node(page, "cache-locality")).toHaveAttribute("data-diagnosis-target", "true");
  await expect(page.getByText("진단에서 찾은 모르는 개념", { exact: true })).toBeVisible();
  await expect(path(page, "memory-address", "memory-hierarchy")).toHaveAttribute("data-diagnosis", "true");
  await expect(path(page, "memory-hierarchy", "cache-locality")).toHaveAttribute("data-diagnosis", "true");
  await expect(path(page, "memory-address", "array")).toHaveAttribute("data-diagnosis", "true");
  await expect(path(page, "array", "cache-locality")).toHaveAttribute("data-diagnosis", "true");
  // 목표로 이어지지 않는 가지(명령어 사이클)와 빈틈보다 앞선 선(복잡도 분석 → 배열)은 강조하지 않음
  await expect(path(page, "memory-address", "instruction-cycle")).not.toHaveAttribute("data-diagnosis");
  await expect(path(page, "complexity-analysis", "array")).not.toHaveAttribute("data-diagnosis");
  // 긴 직접 간선이 중간 단계의 다른 칸을 지나면 그 개념도 선수 경로인 것처럼 보이므로 차단
  const crossings = await page.evaluate(() => {
    const nodes = [...document.querySelectorAll<HTMLElement>(".roadmap-node")];
    return [...document.querySelectorAll<SVGPathElement>(".prerequisite-edge[data-diagnosis]")].flatMap(edge => {
      const others = nodes.filter(node => ![edge.dataset.source, edge.dataset.target].includes(node.dataset.concept));
      const matrix = edge.getScreenCTM()!;
      const hits = new Set<string>();
      for (let distance = 0; distance < edge.getTotalLength(); distance += 2) {
        const point = edge.getPointAtLength(distance).matrixTransform(matrix);
        for (const node of others) {
          const rect = node.getBoundingClientRect();
          if (point.x > rect.left && point.x < rect.right && point.y > rect.top && point.y < rect.bottom) hits.add(node.dataset.concept!);
        }
      }
      return [...hits];
    });
  });
  expect(crossings).toEqual([]);
  // 호버 중에도 진단 경로를 흐리게 만들지 않음
  await node(page, "big-o").hover();
  await expect(node(page, "memory-address")).toHaveCSS("opacity", "1");
  await expect(path(page, "memory-address", "memory-hierarchy")).toHaveCSS("opacity", "1");
  await panel(page).getByRole("button", { name: "보충 시작" }).click();
  await page.reload();
  await expect(panel(page)).toContainText("원소 크기만큼");
  await expect(node(page, "memory-address")).toContainText("모름");
  await panel(page).getByRole("button", { name: "계속", exact: true }).click();
  await expect(node(page, "memory-address")).toContainText("확인 통과");
  await expect(node(page, "memory-address")).toHaveAttribute("data-diagnosis", "passed");
  // 진단 통과는 사용자가 지정하는 일반 학습 상태를 바꾸지 않음
  await expect(node(page, "memory-address")).toHaveAttribute("aria-label", /미학습/);
  await page.reload();
  await expect(node(page, "memory-address")).toContainText("확인 통과");
  await panel(page).getByRole("button", { name: "다음", exact: true }).click();
  await expect(node(page, "memory-address")).toContainText("확인 통과");
  await panel(page).getByRole("button", { name: "원래 학습으로 돌아가기" }).click();
  await expect(page.locator(".roadmap-node[data-diagnosis], .roadmap-node[data-diagnosis-target], .prerequisite-edge[data-diagnosis]")).toHaveCount(0);
});

/** @brief 닫을 때 표시를 지우고 같은 세션 재개 시 복원하되 재시작하면 이전 진단이 새 세션에 붙지 않는지 검증 */
test("닫기·새로고침에는 표시가 없고 이어서 풀기에서만 복원하며 처음부터 다시는 표시를 지움", async ({ page }) => {
  await mockSession(page);
  await diagnose(page);
  await expect(node(page, "memory-address")).toContainText("모름");
  await panel(page).getByRole("button", { name: "막힘 패널 닫기" }).click();
  await expect(page.locator("[data-diagnosis], [data-diagnosis-target]")).toHaveCount(0);
  await page.reload();
  await expect(page.locator("[data-diagnosis], [data-diagnosis-target]")).toHaveCount(0);
  await page.getByRole("button", { name: "막힘 이어서 풀기" }).click();
  await expect(node(page, "memory-address")).toContainText("모름");
  await panel(page).getByRole("button", { name: "처음부터 다시", exact: true }).click();
  await expect(node(page, "memory-address")).toContainText("모름");
  await panel(page).getByRole("group", { name: "처음부터 다시 시작할까요?" }).getByRole("button", { name: "다시 시작", exact: true }).click();
  await expect(panel(page).getByLabel("4바이트 원소 배열의 다음 원소 주소는?")).toBeVisible();
  await expect(page.locator("[data-diagnosis], [data-diagnosis-target]")).toHaveCount(0);
  await page.reload();
  await expect(panel(page).getByLabel("4바이트 원소 배열의 다음 원소 주소는?")).toBeVisible();
  await expect(page.locator("[data-diagnosis], [data-diagnosis-target]")).toHaveCount(0);
});

/** @brief 없어진 서버 세션의 저장 진단을 새로고침 화면에 잘못 표시하지 않는지 검증 */
test("서버 세션이 없어지면 저장된 진단 표시를 복구하지 않음", async ({ page }) => {
  const server = await mockSession(page);
  await diagnose(page);
  await expect(node(page, "memory-address")).toContainText("모름");
  server.expire();
  await page.reload();
  await expect(panel(page).getByRole("alert")).toContainText("막힘 세션을 찾을 수 없습니다");
  await expect(page.locator("[data-diagnosis], [data-diagnosis-target]")).toHaveCount(0);
});

/** @brief 실험 진입이 학습 앱과 저장된 막힘 진단을 함께 마운트하지 않는지 검증 */
test("진단 세션이 저장되어 있어도 실험 화면에는 지도나 막힘 패널이 나타나지 않음", async ({ page }) => {
  await mockSession(page);
  await diagnose(page);
  await expect(node(page, "memory-address")).toContainText("모름");
  await page.goto("/#/experiment");
  await expect(page.locator(".roadmap-node, .stuck-panel")).toHaveCount(0);
  await expect(page.locator("[data-diagnosis], [data-diagnosis-target]")).toHaveCount(0);
});


/** @brief 닫힌 패널의 지연 응답이 새로 연 다른 개념의 지도에 이전 진단을 붙이지 않는지 검증 */
test("닫힌 패널의 늦은 진단 응답은 새 패널의 지도 표시를 덮어쓰지 않음", async ({ page }) => {
  const server = await mockSession(page);
  await page.goto("/#/roadmap");
  await node(page, "cache-locality").click();
  await page.getByRole("button", { name: "막혔어요", exact: true }).click();
  await panel(page).getByLabel("4바이트 원소 배열의 다음 원소 주소는?").fill("1004");
  const release = server.holdNextStep();
  const submitted = page.waitForRequest(request => request.url().endsWith("/step"));
  await panel(page).getByRole("button", { name: "답 제출", exact: true }).click();
  await submitted;
  await panel(page).getByRole("button", { name: "막힘 패널 닫기" }).click();
  await node(page, "memory-address").click();
  await page.getByRole("button", { name: "막혔어요", exact: true }).click();
  await expect(panel(page).getByLabel("4바이트 원소 배열의 다음 원소 주소는?")).toBeVisible();
  const delivered = page.waitForResponse(response => response.url().includes(`/sessions/${sessionId}/step`));
  release();
  await (await delivered).finished();
  // 응답 처리 뒤 두 번의 화면 갱신까지 기다려 이전 콜백이 뒤늦게 실행되는 경우도 관찰
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(page.locator("[data-diagnosis], [data-diagnosis-target]")).toHaveCount(0);
});
