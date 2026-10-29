/**
 * @file
 * @brief 문항 도식 렌더러의 도형·선·표 그리기, 선 끝 처리, 모바일 폭, 어두운 테마, 정지 화면 여부를 실제 브라우저에서 검증
 */
import { test, expect, type Page } from "@playwright/test";
import type { FigureData } from "../../src/ItemFigure";

// 지문 "간선이 1→2:6, 1→3:2, 3→2:3, 2→3:1"을 옮긴 그래프와 Sale 표; 반대 방향 선(2→3, 3→2) 포함
const figure: FigureData = {
  nodes: [
    { id: "n1", label: "1", shape: "circle", x: 0, y: 1 },
    { id: "n2", label: "2", shape: "circle", x: 2, y: 0 },
    { id: "n3", label: "3", shape: "circle", x: 2, y: 2 },
    { id: "db", label: "DB", shape: "cylinder", x: 4, y: 0 },
    { id: "q", label: "큐", shape: "text", x: 4, y: 2 },
  ],
  edges: [
    { from: "n1", to: "n2", label: "6" },
    { from: "n1", to: "n3", label: "2" },
    { from: "n3", to: "n2", label: "3" },
    { from: "n2", to: "n3", label: "1" },
    { from: "n2", to: "db", directed: false },
  ],
  tables: [{ id: "t", name: "Sale", columns: ["shop", "amount"], rows: [["a", "4"], ["b", "7"]], keys: ["shop"], x: 0, y: 3 }],
};

/** @brief 고정 페이지를 열고 도식을 그림; 폭을 주면 그 화면 폭으로 엶 */
async function render(page: Page, data: FigureData = figure, width = 1440) {
  await page.setViewportSize({ width, height: 900 });
  await page.goto("/tests/browser/figure.html");
  await page.waitForFunction(() => (window as unknown as { figureReady?: boolean }).figureReady);
  await page.evaluate((value) => (window as unknown as { renderFigure: (f: unknown) => Promise<void> }).renderFigure(value), data);
  return page.locator(".item-figure svg");
}

/** @brief 데이터의 도형·선·표가 빠짐없이 그려지고, 방향 없는 선에는 화살표가 없는지 확인 */
test("draws every node, edge and table cell; arrowheads only on directed edges", async ({ page }) => {
  const svg = await render(page);
  await expect(svg).toHaveAttribute("role", "img");
  await expect(svg.locator(".figure-node")).toHaveCount(5);
  await expect(svg.locator(".figure-edge line")).toHaveCount(5);
  await expect(svg.locator(".figure-edge line[marker-end]")).toHaveCount(4);
  await expect(svg.locator(".figure-table text")).toHaveText(["Sale", "shop", "amount", "a", "4", "b", "7"]);
  await expect(svg.locator(".figure-key")).toHaveText("shop");
});

/** @brief 선이 도형 안까지 들어가지 않고, 반대 방향 두 선이 한 줄로 겹치지 않는지 확인 */
test("clips edges at shape borders and separates reverse edges", async ({ page }) => {
  const svg = await render(page);
  const lines = await svg.locator(".figure-edge line").evaluateAll((els) => els.map((el) =>
    ["x1", "y1", "x2", "y2"].map((name) => Number(el.getAttribute(name)))));
  // n1 원(중심 50,108, 반지름 22) 밖에서 시작해야 함
  const [x1, y1] = lines[0];
  expect(Math.hypot(x1 - 50, y1 - 108)).toBeGreaterThan(22);
  // 3→2와 2→3은 같은 두 점을 잇지만 서로 다른 x 위치로 비켜 그려짐
  expect(Math.abs(lines[2][0] - lines[3][0])).toBeGreaterThan(8);
});

/** @brief 360px 화면에서도 페이지 전체는 가로로 넘치지 않고, 도식만 최소 폭을 지키며 안에서 밀어 볼 수 있는지 확인 */
test("fits mobile width without page overflow", async ({ page }) => {
  const svg = await render(page, figure, 360);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  const width = (await svg.boundingBox())!.width;
  expect(width).toBeGreaterThanOrEqual(320);
});

/** @brief 어두운 테마 색 변수로 바꾸면 글자·테두리가 바탕과 구별되는 색으로 따라 바뀌는지 확인 */
test("follows dark theme tokens", async ({ page }) => {
  const svg = await render(page);
  await page.evaluate(() => {
    const style = document.documentElement.style;
    style.setProperty("--color-surface", "#1d1f1b");
    style.setProperty("--color-text", "#ecede8");
    style.setProperty("--color-muted", "#a5a99f");
    style.setProperty("--color-background", "#121411");
  });
  const colors = await svg.locator(".figure-node circle").first().evaluate((el) => {
    const text = el.parentElement!.querySelector("text")!;
    return { fill: getComputedStyle(el).fill, stroke: getComputedStyle(el).stroke, text: getComputedStyle(text).fill };
  });
  expect(colors).toEqual({ fill: "rgb(29, 31, 27)", stroke: "rgb(236, 237, 232)", text: "rgb(236, 237, 232)" });
});

/** @brief 정지 도식이어야 하므로 CSS 애니메이션·전환이 하나도 없는지 확인 */
test("is a still image without animations", async ({ page }) => {
  await render(page);
  expect(await page.evaluate(() => document.getAnimations().length)).toBe(0);
});

/** @brief 긴 표 값(IP 접두사)은 열 폭을 넓혀 칸 안에 그리고, 이름 없는 표·빈 칸은 빈 글 없이 그리는지 확인 */
test("widens long table columns and draws unnamed tables and empty cells", async ({ page }) => {
  const svg = await render(page, {
    nodes: [{ id: "c0", label: "", shape: "cell", x: 0, y: 3 }, { id: "c1", label: "", shape: "cell", x: 0.5, y: 3 }],
    tables: [{ id: "r", columns: ["목적지", "다음 홉"], rows: [["10.20.8.0/21", "R2"]], x: 0, y: 0 }],
  });
  await expect(svg.locator(".figure-table-name")).toHaveCount(0);
  const widths = await svg.locator(".figure-table-head").evaluateAll((els) => els.map((el) => Number(el.getAttribute("width"))));
  expect(widths[0]).toBeGreaterThan(80);
  expect(widths[1]).toBe(80);
  const fits = await svg.locator(".figure-table text").evaluateAll((els) => els.every((el) => {
    const cell = el.previousElementSibling as SVGRectElement;
    return (el as SVGTextElement).getBBox().width <= Number(cell.getAttribute("width"));
  }));
  expect(fits).toBe(true);
  await expect(svg.locator(".shape-cell rect")).toHaveCount(2);
});
