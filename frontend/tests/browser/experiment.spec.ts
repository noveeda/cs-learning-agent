/**
 * @file
 * @brief 가짜 실험 API로 #/experiment 화면의 남은 시간 복구·조건별 도구 분리·마감 처리·중복 참가자 재개를 실제 브라우저에서 검증
 */
import { readFileSync } from "node:fs";
import { test, expect, type Page } from "@playwright/test";

const conceptMap = JSON.parse(
  readFileSync(
    new URL("../../../ai-core/content/dsa/concept-map.json", import.meta.url),
    "utf8",
  ),
);
const id = "0123456789abcdef0123456789abcdef";

// Backend PR #74 ExperimentController.ok()와 같은 모양의 학습 단계 응답(GET과 모든 POST가 같은 모양)
const learn = (condition: "system" | "chat", remaining: number) => ({
  id,
  participant: "S1",
  stage: "learn1",
  concept: { id: "bst", title: "이진 탐색 트리" },
  condition,
  passage_part1:
    "이진 탐색 트리는 각 노드의 왼쪽 서브트리에는 더 작은 키가 있는 트리다.",
  passage_part2: null as string | null,
  deadline: "2026-11-23T05:15:00Z",
  remaining_seconds: remaining,
});

/**
 * @brief 실험 API를 흉내 냄; GET은 state.view를 돌려주고(테스트가 바꿔 서버 시각 경과를 흉내 냄), 보낸 사건을 기록
 * - 개발 모드 StrictMode는 effect를 두 번 실행해 GET이 두 번 올 수 있으므로 호출 순서가 아닌 상태로 응답
 * - 사건 응답도 Backend처럼 화면 상태 전체이며, continue_reading 뒤에는 2부가 채워진 상태를 돌려줌
 */
async function mockExperiment(page: Page, view: ReturnType<typeof learn>) {
  const events: { type: string; ref?: string }[] = [];
  const state = { view };
  await page.route("**/api/concept-map", (route) =>
    route.fulfill({ json: conceptMap }),
  );
  await page.route("**/api/conversations/*", (route) =>
    route.fulfill({ json: { messages: [] } }),
  );
  await page.route("**/api/experiments**", async (route) => {
    const request = route.request();
    if (request.url().endsWith("/events")) {
      const body = request.postDataJSON();
      events.push(body);
      if (body.type === "continue_reading")
        state.view = {
          ...state.view,
          passage_part2: "2부: 삽입은 루트부터 비교하며 내려간다.",
        };
      return route.fulfill({ json: state.view });
    }
    return route.fulfill({ json: state.view });
  });
  // 새로고침해도 같은 세션으로 돌아오도록 화면이 저장하는 세션 id를 미리 넣어 둠
  await page.addInitScript((value) => {
    localStorage.setItem("cs-experiment-session", value);
  }, id);
  return { events, state };
}

const clock = (page: Page) => page.getByLabel("남은 시간");

test("새로고침하면 남은 시간이 처음 값으로 돌아가지 않고 서버가 준 값을 따름", async ({
  page,
}) => {
  const { state } = await mockExperiment(page, learn("system", 600));
  await page.goto("/#/experiment");
  await expect(clock(page)).toHaveText(/^(10:00|09:59)$/);
  state.view = learn("system", 300);
  // 서버 시각으로 5분이 지난 뒤 새로고침했다고 가정: 화면 시계가 아니라 서버 값으로 다시 맞춤
  await page.reload();
  await expect(clock(page)).toHaveText(/^(05:00|04:59)$/);
  // 실험 중에는 학습 앱 메뉴·로드맵이 없음
  await expect(page.getByRole("navigation", { name: "주 메뉴" })).toHaveCount(
    0,
  );
  await expect(page.getByLabel("개념 연결 지도")).toHaveCount(0);
});

test("제안 시스템 조건: [막혔어요]로 붙여넣기 패널만 열리고 자유 채팅 입력은 없음", async ({
  page,
}) => {
  const { events } = await mockExperiment(page, learn("system", 900));
  await page.goto("/#/experiment");
  await expect(page.getByLabel("읽기 글")).toContainText("이진 탐색 트리는");
  await expect(page.getByLabel("질문", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "막혔어요", exact: true }).click();
  const panel = page.getByRole("complementary", { name: "막힘 풀기" });
  await expect(panel.getByRole("textbox")).toBeVisible();
  await expect
    .poll(() => events.map((event) => event.type))
    .toEqual(["help_open"]);
  await page.screenshot({
    path: test.info().outputPath("experiment-learn-system.png"),
  });
  await page.getByRole("button", { name: "계속 읽기" }).click();
  await expect(page.getByLabel("읽기 글")).toContainText("2부: 삽입은");
  await expect(page.getByRole("button", { name: "다음 단계" })).toBeVisible();
  expect(events.map((event) => event.type)).toEqual([
    "help_open",
    "continue_reading",
  ]);
});

test("채팅 조건: 막힘 진입(막혔어요·붙여넣기)이 없고 채팅 입력만 있음", async ({
  page,
}) => {
  await mockExperiment(page, learn("chat", 900));
  await page.goto("/#/experiment");
  await expect(page.getByLabel("질문", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "막혔어요", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "막힌 문장 붙여넣기" }),
  ).toHaveCount(0);
  await page.screenshot({
    path: test.info().outputPath("experiment-learn-chat.png"),
  });
});

test("마감되면 도구를 닫고 timeout을 기록하며 계속 읽기는 누를 수 없음", async ({
  page,
}) => {
  const { events } = await mockExperiment(page, learn("chat", 2));
  await page.goto("/#/experiment");
  await expect(page.getByLabel("질문", { exact: true })).toBeVisible();
  // 서버 남은 2초 + 서버 초 버림을 감안한 1초 뒤 마감
  await expect(page.getByRole("button", { name: "계속 읽기" })).toBeDisabled({
    timeout: 6000,
  });
  await expect(page.getByLabel("질문", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "다음 단계" })).toBeVisible();
  await expect
    .poll(() => events.map((event) => event.type))
    .toEqual(["timeout"]);
  await page.screenshot({
    path: test.info().outputPath("experiment-timeout.png"),
  });
});

test("같은 참가자 코드로 다시 시작하면 덮어쓰지 않고 기존 세션 재개를 고르게 함", async ({
  page,
}) => {
  await page.route("**/api/concept-map", (route) =>
    route.fulfill({ json: conceptMap }),
  );
  const created: unknown[] = [];
  await page.route("**/api/experiments**", (route) => {
    const request = route.request();
    if (request.method() === "POST") {
      created.push(request.postDataJSON());
      return route.fulfill({
        status: 409,
        json: {
          message:
            "이미 같은 참가자 코드의 세션이 있습니다. 기존 세션을 이어서 진행해 주세요.",
          id,
        },
      });
    }
    return route.fulfill({
      json: {
        id,
        participant: "S3",
        stage: "pretest",
        pretest: [
          {
            id: "exp-bst-A1",
            concept: "bst",
            format: "multiple_choice",
            question: "이진 탐색 트리에서 가장 작은 키는 어디에 있나요?",
            options: {
              A: "루트",
              B: "가장 왼쪽 노드",
              C: "가장 오른쪽 노드",
              D: "임의의 리프",
            },
          },
        ],
      },
    });
  });
  await page.goto("/#/experiment");
  await expect(page.getByRole("button", { name: "실험 시작" })).toBeDisabled();
  await page.getByLabel("참가자 코드(S1~S99)").fill("s3");
  await page.getByLabel("수강 중").check();
  await page.getByLabel("bfs").check();
  await page.screenshot({
    path: test.info().outputPath("experiment-setup.png"),
  });
  await page.getByRole("button", { name: "실험 시작" }).click();
  expect(created).toEqual([
    { participant: "S3", grade: 3, dsa: "taking", exclude: ["bfs"] },
  ]);
  await expect(page.getByRole("alert")).toContainText("이미 같은 참가자 코드");
  await page.getByRole("button", { name: "기존 세션 재개" }).click();
  await expect(page.getByRole("heading", { name: "사전 시험" })).toBeVisible();
  await expect(page.getByText("가장 왼쪽 노드")).toBeVisible();
});

// Backend PR #74 응답 모양의 시험·설문 문항(정답·근거 없음); 설문은 backend/src/main/resources/experiment/survey.json과 같은 형식
const choice = (
  concept: string,
  form: string,
  n: number,
  question: string,
) => ({
  id: `exp-${concept}-${form}${n}`,
  concept,
  format: "multiple_choice",
  question,
  options: { A: "O(1)", B: "O(log n)", C: "O(n)", D: "O(n log n)" },
});
const short = (concept: string, form: string, n: number, question: string) => ({
  id: `exp-${concept}-${form}${n}`,
  concept,
  format: "short_exact",
  question,
});
const scale = Object.fromEntries(
  ["전혀 아니다", "아니다", "보통이다", "그렇다", "매우 그렇다"].map(
    (text, i) => [String(i + 1), text],
  ),
);
const views = {
  pretest: {
    id,
    participant: "S1",
    stage: "pretest",
    pretest: [
      choice(
        "bst",
        "A",
        1,
        "균형이 깨진 이진 탐색 트리에서 탐색의 최악 시간은?",
      ),
      short(
        "bst",
        "A",
        2,
        "키 5, 3, 8을 차례로 넣은 뒤 중위 순회 결과를 쉼표로 쓰세요.",
      ),
      choice("heap", "A", 1, "최소 힙에서 최솟값을 읽는 시간은?"),
      short("heap", "A", 2, "배열 [9, 4, 7]을 최소 힙으로 만들면 루트는?"),
    ],
  },
  posttest: {
    id,
    participant: "S1",
    stage: "posttest",
    posttest: [
      choice(
        "bst",
        "B",
        1,
        "이미 정렬된 키를 차례로 넣은 이진 탐색 트리의 높이는?",
      ),
      short("bst", "B", 2, "키 7, 2, 9, 1을 넣은 뒤 전위 순회 결과를 쓰세요."),
      {
        id: "exp-bst-B3",
        concept: "bst",
        format: "explanation",
        question:
          "이진 탐색 트리에서 삭제할 노드의 자식이 둘일 때 무엇으로 바꾸는지 설명하세요.",
      },
    ],
  },
  survey: {
    id,
    participant: "S1",
    stage: "survey",
    survey: [
      {
        id: "survey-1",
        format: "scale",
        question:
          "첫 번째 도구는 막힌 부분을 이해하는 데 얼마나 도움이 됐나요?",
        options: scale,
      },
      {
        id: "survey-3",
        format: "choice",
        question: "다음에 비슷한 상황에서 막히면 어느 도구를 쓰고 싶나요?",
        options: {
          first: "첫 번째 도구",
          second: "두 번째 도구",
          either: "상관없음",
        },
      },
      {
        id: "survey-4",
        format: "text",
        question: "글을 읽다가 어디서, 왜 막혔나요?",
      },
    ],
  },
  done: { id, participant: "S1", stage: "done" },
};

test("사전 시험·사후 시험·설문은 한 화면 목록으로 받고 확인 뒤 한 번만 제출하며 서버 단계로 넘어감", async ({
  page,
}) => {
  const state: { view: object } = { view: views.pretest };
  const posted: { path: string; body: unknown }[] = [];
  await page.route("**/api/concept-map", (route) =>
    route.fulfill({ json: conceptMap }),
  );
  // 제출을 받으면 Backend처럼 서버가 다음 단계를 정함(사전 → 학습 1, 사후 → 설문, 설문 → 끝)
  await page.route("**/api/experiments**", (route) => {
    const request = route.request();
    if (request.method() === "POST") {
      const path = new URL(request.url()).pathname.split("/").pop()!;
      posted.push({ path, body: request.postDataJSON() });
      state.view =
        path === "pretest"
          ? learn("system", 900)
          : path === "posttest"
            ? views.survey
            : views.done;
    }
    return route.fulfill({ json: state.view });
  });
  await page.addInitScript((value) => {
    localStorage.setItem("cs-experiment-session", value);
  }, id);

  await page.goto("/#/experiment");
  await expect(page.getByRole("heading", { name: "사전 시험" })).toBeVisible();
  await expect(page.getByRole("listitem")).toHaveCount(4);
  await page
    .getByRole("radiogroup", { name: /균형이 깨진/ })
    .getByLabel("C. O(n)")
    .check();
  await page.getByRole("textbox", { name: /중위 순회/ }).fill("3,5,8");
  await page.screenshot({
    path: test.info().outputPath("experiment-pretest.png"),
    fullPage: true,
  });
  // 첫 번째 누름은 확인 안내만(제출 뒤 수정 불가), 두 번째 누름에 제출
  await page.getByRole("button", { name: "제출" }).click();
  await expect(page.getByRole("status")).toContainText("비어 있는 답이 2개");
  expect(posted).toEqual([]);
  await page.getByRole("button", { name: "제출 확정" }).click();
  await expect(
    page.getByRole("heading", { name: "개념 1 학습" }),
  ).toBeVisible();
  expect(posted).toEqual([
    {
      path: "pretest",
      body: { answers: { "exp-bst-A1": "C", "exp-bst-A2": "3,5,8" } },
    },
  ]);

  state.view = views.posttest;
  await page.reload();
  await expect(page.getByRole("heading", { name: "사후 시험" })).toBeVisible();
  await page
    .getByRole("textbox", { name: /자식이 둘일 때/ })
    .fill("오른쪽 서브트리의 가장 작은 노드로 바꾼다.");
  await page.screenshot({
    path: test.info().outputPath("experiment-posttest.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "제출" }).click();
  await page.getByRole("button", { name: "제출 확정" }).click();
  await expect(page.getByRole("heading", { name: "설문" })).toBeVisible();
  // 사후 시험 입력은 사라져 제출한 답을 고칠 수 없음
  await expect(
    page.getByRole("textbox", { name: /자식이 둘일 때/ }),
  ).toHaveCount(0);

  // 척도 문항은 서버가 준 보기 글(전혀 아니다~매우 그렇다)로 보여 줌
  await page
    .getByRole("radiogroup", { name: /첫 번째 도구는/ })
    .getByLabel("그렇다", { exact: true })
    .check();
  await page.getByLabel("두 번째 도구", { exact: true }).check();
  await page.screenshot({
    path: test.info().outputPath("experiment-survey.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "제출" }).click();
  await page.getByRole("button", { name: "제출 확정" }).click();
  await expect(
    page.getByRole("heading", { name: "끝", exact: true }),
  ).toBeVisible();
  expect(posted.slice(1)).toEqual([
    {
      path: "posttest",
      body: {
        answers: { "exp-bst-B3": "오른쪽 서브트리의 가장 작은 노드로 바꾼다." },
      },
    },
    {
      path: "survey",
      body: { answers: { "survey-1": "4", "survey-3": "second" } },
    },
  ]);
});
