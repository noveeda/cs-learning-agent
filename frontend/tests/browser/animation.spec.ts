/**
 * @file
 * @brief 목업 프레임으로 공통 재생기의 모션·강조·자동 재생·접근성과 채팅/막힘 패널 적용 검증
 */
import { readFileSync } from "node:fs";
import { test, expect, type Page } from "@playwright/test";
import type { AnimationData, Box } from "../../src/animation";

const conceptMap = JSON.parse(readFileSync(new URL("../../../ai-core/content/dsa/concept-map.json", import.meta.url), "utf8"));
/** @brief 자리 기준 id를 가진 큐 원소; 같은 값의 id가 바뀌는 BFS 프레임 검증용 */
const queueBox = (id: string, label: string, x: number, tone: Box["tone"] = "normal"): Box =>
  ({ id, label, x, y: 4, shape: "cell", tone });
const nodes: Box[] = [
  { id: "a", label: "A", x: 1, y: 0, shape: "node", tone: "active" },
  { id: "b", label: "B", x: 0, y: 2, shape: "node", tone: "normal" },
  { id: "c", label: "C", x: 2, y: 2, shape: "node", tone: "normal" },
  { id: "d", label: "D", x: 0, y: 3, shape: "node", tone: "normal" },
];
const animation: AnimationData = {
  kind: "graph_traversal", title: "BFS 큐의 이동",
  frames: [
    { boxes: [...nodes, queueBox("q0", "B", 1), queueBox("q1", "C", 2)], caption: "A 방문: B와 C를 큐에 넣습니다" },
    { boxes: [...nodes.map(node => node.id === "b" ? { ...node, tone: "new" as const } : node), queueBox("q0", "C", 1), queueBox("q1", "D", 2, "new")], caption: "B 방문: C를 앞으로 옮기고 D를 넣습니다" },
    { boxes: [...nodes, queueBox("q0", "D", 1)], caption: "C 방문: D만 큐에 남습니다" },
  ].map(frame => ({ ...frame, arrows: [{ source: "a", target: "b", head: false }, { source: "a", target: "c", head: false }] })),
};

/** @brief 외부 모델·저장 API만 목업하고 실제 채팅/막힘 경로로 재생기를 엶 */
async function openPlayer(page: Page, context: "chat" | "stuck" = "chat", data = animation) {
  await page.route("**/api/concept-map", route => route.fulfill({ json: { ...conceptMap, stuck_supported: ["linked-list"] } }));
  await page.route("**/api/conversations/*", route => route.fulfill({ json: { messages: context === "chat" ? [{
    role: "assistant", content: "BFS를 단계별로 살펴봅니다.", status: "complete",
    artifacts: [{ artifact_id: "motion-fixture", kind: "animation", animation: data }],
  }] : [] } }));
  await page.route("**/api/stuck/sessions**", route => route.fulfill({ json: {
    id: "0123456789abcdef0123456789abcdef", version: 1, stage: "check",
    entry: { kind: "roadmap", concept: "linked-list", return_to: "#/learn?concept=linked-list" },
    view: { kind: "remediation", concept: { id: "linked-list", title: "연결 리스트" }, attempt: 1, is_target: true,
      text: "계산된 BFS의 큐를 확인합니다.", animation: data,
      item: { id: "linked-list-c1", format: "short_exact", question: "앞 원소는?" },
    },
  } }));
  await page.goto(context === "stuck" ? "/#/roadmap" : "/");
  if (context === "stuck") {
    await page.getByRole("button", { name: "연결 리스트 · 미학습", exact: true }).click();
    await page.getByRole("button", { name: "학습 시작", exact: true }).click();
    await page.getByRole("button", { name: "막혔어요", exact: true }).click();
  }
  const player = page.locator(".animation-player");
  await expect(player.locator(".animation-step")).toHaveText("1 / 3");
  return player;
}

/** @brief 자동 재생 간격·마지막 정지·재시작 검증; 타이머 중복·범위 초과 방지 */
test("자동 재생: 단계 간격·마지막 정지·처음부터 다시 재생", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-10-28T00:00:00Z") });
  await page.clock.pauseAt(new Date("2026-10-28T00:00:01Z"));
  const player = await openPlayer(page);
  await player.getByRole("button", { name: "자동 재생", exact: true }).click();
  await expect(player.getByRole("button", { name: "자동 재생 일시정지" })).toHaveAttribute("aria-pressed", "true");
  await page.clock.runFor(1499);
  await expect(player.locator(".animation-step")).toHaveText("1 / 3");
  await page.clock.runFor(1);
  await expect(player.locator(".animation-step")).toHaveText("2 / 3");
  await page.clock.runFor(1500);
  await expect(player.locator(".animation-step")).toHaveText("3 / 3");
  const replay = player.getByRole("button", { name: "처음부터 자동 재생" });
  await expect(replay).toHaveAttribute("aria-pressed", "false");
  await page.clock.runFor(6000);
  await expect(player.locator(".animation-step")).toHaveText("3 / 3");
  await replay.click();
  await expect(player.locator(".animation-step")).toHaveText("1 / 3");
  await page.clock.runFor(1500);
  await expect(player.locator(".animation-step")).toHaveText("2 / 3");
});

/** @brief 일시정지·수동 이동 뒤 남은 타이머가 단계를 몰래 바꾸는 회귀 방지 */
test("일시정지와 좌우 키 수동 이동은 자동 진행을 멈춤", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-10-28T00:00:00Z") });
  await page.clock.pauseAt(new Date("2026-10-28T00:00:01Z"));
  const player = await openPlayer(page);
  await player.getByRole("button", { name: "자동 재생", exact: true }).click();
  await page.clock.runFor(800);
  await player.getByRole("button", { name: "자동 재생 일시정지" }).click();
  await page.clock.runFor(4000);
  await expect(player.locator(".animation-step")).toHaveText("1 / 3");
  await player.getByRole("button", { name: "자동 재생", exact: true }).click();
  await player.focus();
  await page.keyboard.press("ArrowRight");
  await page.clock.runFor(4000);
  await expect(player.locator(".animation-step")).toHaveText("2 / 3");
  await page.keyboard.press("ArrowLeft");
  await expect(player.locator(".animation-step")).toHaveText("1 / 3");
});

/** @brief C의 DOM 유지와 중간 위치·등장/퇴장 투명도를 실제 애니메이션에서 검증 */
test("큐의 같은 값은 이동하고 새 값·빠진 값은 나타나고 사라짐", async ({ page }) => {
  const player = await openPlayer(page);
  await player.locator(".shape-cell", { hasText: /^C$/ }).evaluate(node => { (node as SVGGElement).dataset.identityProbe = "moving-c"; });
  await player.getByRole("button", { name: "다음 단계" }).click();
  const state = await player.evaluate(root => {
    const item = root.querySelector<SVGGElement>('[data-identity-probe="moving-c"]');
    const move = item?.getAnimations().find(a => a instanceof CSSTransition && a.transitionProperty === "transform");
    if (move) { move.pause(); move.currentTime = 180; }
    const opacityAtHalf = (label: string) => {
      const box = [...root.querySelectorAll<SVGGElement>(".shape-cell")].find(node => node.textContent === label);
      const content = box?.querySelector(".animation-box-content");
      content?.getAnimations().forEach(a => { a.pause(); a.currentTime = 180; });
      return content ? Number(getComputedStyle(content).opacity) : null;
    };
    return { id: item?.dataset.boxId, x: item ? new DOMMatrix(getComputedStyle(item).transform).e : null,
      entering: opacityAtHalf("D"), exiting: opacityAtHalf("B") };
  });
  expect(state.id).toBe("q0");
  expect(state.x).toBeGreaterThan(64);
  expect(state.x).toBeLessThan(128);
  expect(state.entering).toBeGreaterThan(0);
  expect(state.entering).toBeLessThan(1);
  expect(state.exiting).toBeGreaterThan(0);
  expect(state.exiting).toBeLessThan(1);
});

/** @brief 빠른 왕복에서 오래된 퇴장 타이머가 복귀한 원소를 지우는 회귀 방지 */
test("빠른 다음·이전 왕복 뒤에는 해당 단계 상자만 남음", async ({ page }) => {
  const player = await openPlayer(page);
  await player.getByRole("button", { name: "다음 단계" }).click();
  await player.getByRole("button", { name: "이전 단계" }).click();
  await player.getByRole("button", { name: "다음 단계" }).click();
  await expect(player.locator(".shape-cell")).toHaveText(["C", "D"]);
  await expect(player.locator('[aria-hidden="true"].animation-box')).toHaveCount(0);
  await expect(player.locator(".animation-step")).toHaveText("2 / 3");
});

/** @brief 축소 화면에서도 강조 테두리 3px·대비 3:1을 유지하고 tone을 구별하는지 검증 */
test("강조는 두꺼운 고대비 파랑, 새 원소는 구별되는 호박색으로 표시", async ({ page }) => {
  const player = await openPlayer(page);
  await player.getByRole("button", { name: "다음 단계" }).click();
  const active = player.locator(".tone-active rect").first();
  const fresh = player.locator(".tone-new rect").first();
  const colors = await active.evaluate(rect => {
    const style = getComputedStyle(rect);
    const luminance = (s: string) => s.match(/[\d.]+/g)!.slice(0, 3).map(Number).reduce((total, c, i) => {
      const v = c / 255;
      return total + (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4) * [0.2126, 0.7152, 0.0722][i];
    }, 0);
    const contrast = (a: string, b: string) => (Math.max(luminance(a), luminance(b)) + 0.05) / (Math.min(luminance(a), luminance(b)) + 0.05);
    return { width: parseFloat(style.strokeWidth), vectorEffect: style.vectorEffect, fill: style.fill,
      contrast: contrast(style.stroke, getComputedStyle(rect.closest(".animation-player")!).backgroundColor),
      innerContrast: contrast(style.stroke, style.fill) };
  });
  expect(colors.width).toBeGreaterThanOrEqual(3);
  expect(colors.vectorEffect).toBe("non-scaling-stroke");
  expect(colors.contrast).toBeGreaterThanOrEqual(3);
  expect(colors.innerContrast).toBeGreaterThanOrEqual(3);
  expect(await fresh.evaluate(rect => getComputedStyle(rect).fill)).not.toBe(colors.fill);
});

/** @brief 모든 모션을 끄면 최종 상태만 바로 보이고, 설정 변경에도 퇴장 잔상이 안 남는지 검증 */
test("움직임 줄이기에서는 이동·등장·퇴장이 즉시 완료됨", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const player = await openPlayer(page);
  await player.getByRole("button", { name: "다음 단계" }).click();
  await expect(player.locator(".shape-cell")).toHaveText(["C", "D"]);
  expect(await player.locator(".animation-canvas").evaluate(svg => svg.getAnimations({ subtree: true }).length)).toBe(0);
  await expect(player.locator('.shape-cell[data-box-id="q0"]')).toHaveCSS("transform", "matrix(1, 0, 0, 1, 64, 224)");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await player.getByRole("button", { name: "다음 단계" }).click();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(player.locator(".shape-cell")).toHaveText(["D"]);
  expect(await player.locator(".animation-canvas").evaluate(svg => svg.getAnimations({ subtree: true }).length)).toBe(0);
});

for (const context of ["chat", "stuck"] as const) {
  /** @brief 두 패널의 공통 재생기·자동 재생·375px 제어 범위 검증 */
  test(`${context} 패널: 375px에서 자동 재생과 제어 버튼이 잘리지 않음`, async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 900 });
    const player = await openPlayer(page, context);
    await player.getByRole("button", { name: "자동 재생", exact: true }).click();
    await expect(player.getByRole("button", { name: "자동 재생 일시정지" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    for (const button of await player.locator("button").all()) {
      const rect = await button.boundingBox();
      expect(rect!.x).toBeGreaterThanOrEqual(0);
      expect(rect!.x + rect!.width).toBeLessThanOrEqual(375);
    }
  });
}
