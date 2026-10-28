/**
 * @file
 * @brief 모의 집계 응답으로 반 요약의 통계·빈 상태·오류 재시도·실험 화면 분리 검증
 */
import { test, expect, type Page } from "@playwright/test";

const queue = { id: "queue", title: "큐", subject: "dsa", subjectTitle: "자료구조" };
const cpu = { id: "cpu", title: "CPU 스케줄링", subject: "os", subjectTitle: "운영체제" };
const populated = {
  totalSessions: 4, diagnosedSessions: 3, earliestAt: 1793106000, latestAt: 1793113200,
  targets: [{ concept: cpu, count: 3 }], gaps: [{ concept: queue, count: 2 }],
  pairs: [{ target: cpu, gap: queue, count: 2, crossSubject: true }], crossSubjectPairs: 2,
  outcomes: [{ concept: queue, passed: 1, unresolved: 1 }],
};
const empty = { totalSessions: 0, diagnosedSessions: 0, earliestAt: null, latestAt: null,
  targets: [], gaps: [], pairs: [], crossSubjectPairs: 0, outcomes: [] };

/** @brief 지도 장애와 요약 화면을 분리하고 세션 응답은 실제 화면의 fetch 경계에서만 대체 */
async function mockMap(page: Page) {
  await page.route("**/api/concept-map", route => route.fulfill({ status: 503, json: { message: "지도 중단" } }));
  await page.route("**/api/conversations/**", route => route.fulfill({ json: { messages: [] } }));
}

/** @brief 집계 계약이 보이는 표와 표본 단위가 실제 사용자에게 표시되는지 확인 */
test("shows sample size, prerequisite bars, cross-subject pairs and remediation counts", async ({ page }) => {
  await mockMap(page);
  await page.route("**/api/stuck/summary", route => route.fulfill({ json: populated }));
  await page.goto("/#/class");
  await expect(page.getByRole("heading", { name: "반 막힘 요약", exact: true })).toBeVisible();
  await expect(page.getByText("막힘 세션 4개 기준", { exact: true })).toBeVisible();
  await expect(page.getByText(/표본이 적습니다/)).toBeVisible();
  await expect(page.getByRole("region", { name: "많이 모르는 선수 개념" })).toContainText("자료구조");
  await expect(page.getByRole("region", { name: "막힌 개념 → 원인 개념" })).toContainText("CPU 스케줄링");
  await expect(page.getByRole("region", { name: "보충 결과" }).getByRole("row").last()).toHaveText(/큐.*자료구조.*1.*1/);
  await expect(page.getByRole("button", { name: "반 요약", exact: true })).toHaveAttribute("aria-current", "page");
});

/** @brief 빈 배열을 차트나 가짜 통계로 채우지 않고 기록이 없음을 명시 */
test("shows honest empty state and supports navigation", async ({ page }) => {
  await mockMap(page);
  await page.route("**/api/stuck/summary", route => route.fulfill({ json: empty }));
  await page.goto("/#/learn");
  await page.getByRole("button", { name: "반 요약", exact: true }).click();
  await expect(page).toHaveURL(/#\/class$/);
  await expect(page.getByText("막힘 세션 0개 기준", { exact: true })).toBeVisible();
  await expect(page.getByText(/아직 저장된 막힘 세션이 없습니다/)).toBeVisible();
  await expect(page.getByRole("region", { name: "많이 모르는 선수 개념" })).toContainText("진단 빈틈 기록이 없습니다");
});

/** @brief 실패를 빈 통계로 오인하지 않고 다시 요청할 수 있게 함 */
test("recovers from summary errors on retry", async ({ page }) => {
  await mockMap(page);
  // StrictMode의 초기 이중 요청에도 오류를 유지하고 실제 재시도 버튼 뒤에만 복구
  let failing = true;
  await page.route("**/api/stuck/summary", route => {
    return failing ? route.fulfill({ status: 503, json: { message: "집계를 불러오지 못했습니다." } })
      : route.fulfill({ json: populated });
  });
  await page.goto("/#/class");
  await expect(page.getByRole("alert")).toContainText("집계를 불러오지 못했습니다");
  failing = false;
  await page.getByRole("button", { name: "다시 시도" }).click();
  await expect(page.getByText("막힘 세션 4개 기준", { exact: true })).toBeVisible();
});

/** @brief 교수자 요약 메뉴와 내용이 참가자의 비교 실험에 섞이지 않는지 확인 */
test("experiment route hides class navigation and never loads summary", async ({ page }) => {
  let requests = 0;
  await page.route("**/api/stuck/summary", route => { requests += 1; return route.fulfill({ json: populated }); });
  await page.goto("/#/experiment");
  await expect(page.getByRole("button", { name: "반 요약", exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "반 막힘 요약", exact: true })).toHaveCount(0);
  expect(requests).toBe(0);
});

/** @brief 적은 기록을 반 전체 이해도로 오인하지 않도록 10세션 미만에 안내 표시 */
test("small sample notice follows the session count", async ({ page }) => {
  await mockMap(page);
  let count = 9;
  await page.route("**/api/stuck/summary", route => route.fulfill({ json: { ...populated, totalSessions: count } }));
  await page.goto("/#/class");
  await expect(page.getByText(/표본이 적습니다/)).toBeVisible();
  count = 10;
  await page.getByRole("button", { name: "새로고침", exact: true }).click();
  await expect(page.getByText("막힘 세션 10개 기준", { exact: true })).toBeVisible();
  await expect(page.getByText(/표본이 적습니다/)).toHaveCount(0);
});
