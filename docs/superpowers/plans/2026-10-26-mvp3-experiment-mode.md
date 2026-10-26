# MVP 3 Task 6: 비교 실험 모드 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> 이 저장소에서는 Orca 코디네이터가 Task를 워커에게 나눠 맡긴다. 워커는 `orca-superpowers` 워커 편을 따른다.

**Goal:** 학생 4~5명이 1인 1회(약 1시간) 대면으로 비교 실험을 할 수 있게 한다. 각 학생은 두 개념을 한 번은 제안 시스템으로, 한 번은 같은 모델의 일반 채팅으로 학습한다. 이 실험으로 O1(이해), O2(원래 학습 복귀), O3(시간)을 측정한다.

**Architecture:**
- Spring에 실험 세션 API와 로컬 JSON 저장소를 새로 둔다. 기존 막힘 세션·채팅 저장소 패턴을 따른다.
  - 실험 자료(읽기 글, 사전·사후 문항)는 Spring 리소스로 둔다.
  - 정답 키는 브라우저에 보내지 않는다.
- 화면은 `#/experiment` 경로에 진행자 화면과 참가자 화면을 새로 만든다.
  - 제안 시스템 조건은 기존 `StuckPanel`(붙여넣기 진입)을 쓴다.
  - 채팅 조건은 기존 `ChatSession`을 쓴다. 두 조건 모두 운영 모델(gemma)이 응답한다.
- 시간과 사건은 서버 시각으로 기록한다.
- 채점과 지표 계산은 실험이 끝난 뒤 `ai-core/eval/experiment_report.py`로 한다. 자동 채점 문항은 키로 채점하고, 서술 문항은 석사 과정생의 눈가림 채점을 쓴다.

**Tech Stack:** Spring Boot(기존 패턴), React 19 + TypeScript(기존 컴포넌트 재사용), Python 평가 스크립트(`ai-core/eval/metrics.py` 재사용).

**Spec:** `docs/superpowers/specs/2026-10-26-completion-roadmap-design.md`(3-3 MVP 3 항목 4, MVP 4 항목 2, 2-4절 사람 실험), `docs/project-direction.md` Level 2(O1~O3, 증명 방법 2), 대화에서 정한 실험 절차(아래 1절).

## 1. 실험 절차 (사용자와 합의한 내용)

| 단계 | 내용 | 시간 상한 |
| --- | --- | --- |
| ① 사전 시험 | 후보 개념 8개 × 자동 채점 문항 2개 = 16문항. 점수가 낮은 개념 2개(A, B)를 고른다 | 12분 |
| ② 개념 1 학습 | 읽기 글 1부를 읽다가 막히면 배정된 도구로 해결한다. "계속 읽기"를 누르면 2부가 열린다 | 15분 |
| ③ 개념 2 학습 | 같은 방식, 다른 도구 | 15분 |
| ④ 사후 시험 | 두 개념 각각 사전과 다른 형(A/B)의 자동 채점 2문항 + 서술 1문항 | 10분 |
| ⑤ 설문 | 짧은 문항 5개 이내 | 5분 |

- **배정 규칙(상쇄)**
  - 참가자 번호가 홀수면 개념 1은 제안 시스템, 짝수면 개념 1은 채팅으로 한다.
  - 사전 시험 형은 번호를 2로 나눈 몫이 짝수면 A형(사후 B형), 홀수면 B형(사후 A형)이다.
  - 개념 1·2는 사전 점수가 낮은 순서로 정한다. 점수가 같으면 자료 파일 순서를 따른다.
- **지표**
  - O1: 개념별 사전 대비 사후 자동 채점 정답 수 변화와 사후 서술 점수. 조건별로 낸다.
  - O2: 시간 안에 "계속 읽기"를 눌렀는가.
  - O3: 첫 도움 요청부터 "계속 읽기"까지 걸린 시간.
- **보고:** 참가자가 4~5명이라 유의성 검정은 하지 않는다. 학생별 표, 조건 간 차이의 중앙값과 범위, 사례로 보고한다(설계 2-4).
- **제외할 사람:** 개발자, 채점자(석사 과정생), 자료구조 미수강자. 학생이 서술 답 수집에 이미 쓴 개념은 그 학생의 후보 개념에서 뺀다(진행자 화면에서 선택).

## Global Constraints

- MVP 2·3 계획의 Global Constraints를 따른다.
- **참가자 식별:** `S1`~`S9` 코드만 받는다. 이름, 연락처, 성적은 받지 않는다. 받는 배경 정보는 학년과 자료구조 수강 여부(수강함 / 수강 중)뿐이다.
- **저장 위치:** 실험 기록은 `backend/data/experiments/`(git-ignored)에 둔다. 내보낸 파일과 채점 시트는 `ai-core/outputs/experiment/`에 둔다. 실험이 끝나고 보고서를 만든 뒤 원문 기록은 지운다(설계 2-4).
- **정답 키:** 실험 API 응답과 화면에 정답 키, 루브릭, 근거가 나오면 안 된다.
- **같은 모델:** 두 조건 모두 운영 8000의 gemma를 쓴다. 채팅 조건은 기존 채팅 경로를 그대로 쓰고 실험용 프롬프트를 덧붙이지 않는다.
- **실험 자료:** 데이터 PR이며 사용자 승인 뒤 병합한다.
  - 문항은 문항 은행(`ai-core/content/dsa/items-*.jsonl`)과 겹치지 않게 만든다. 학습 중 확인 문항으로 사후 시험을 미리 연습하게 되면 안 되기 때문이다.
  - CS-300 유사도 검사를 거친다.
- 실험 진행 중에는 운영 8000에 대량 측정을 돌리지 않는다. 진행 일정은 코디네이터가 워커들에게 알린다.

## Review Focus

1. **새로고침이나 브라우저 재시작:** 실험 세션이 같은 단계와 남은 시간으로 복구돼야 한다. 시간은 서버 기록 기준이라 새로고침으로 늘어나지 않아야 한다. (Task 2·3)
2. **시간 초과:** 15분이 지나면 도구 창은 닫히고 "계속 읽기"를 누를 수 없다. O2 미복귀로 기록하고 다음 단계로 간다. 초과 직전에 보낸 답이나 채팅 응답이 기록을 깨면 안 된다. (Task 2·3)
3. **사전 시험에서 모르는 개념이 2개 미만인 학생:** 점수가 가장 낮은 2개를 고르고, 기록에 "사전 점수 높음"을 표시한다. 보고서에서 따로 본다. (Task 2·5)
4. **조건 누출:** 채팅 조건 화면에 [막혔어요] 버튼이나 로드맵이 보이면 안 된다. 제안 시스템 조건 화면에는 자유 채팅이 보이면 안 된다. (Task 3)
5. **진행자 실수로 같은 참가자 코드를 다시 만든 경우:** 기존 세션을 덮어쓰지 않고 409로 막는다. 진행자 화면에서 기존 세션 재개를 고를 수 있어야 한다. (Task 2·3)

## 파일 구조

| 파일 | 책임 | Task |
| --- | --- | --- |
| `backend/src/main/resources/experiment/materials.json` (신규, 데이터) | 후보 개념 8개의 읽기 글(1부·2부), 사전·사후 A/B형 문항과 정답 키, 서술 루브릭 | 1 |
| `backend/src/main/java/com/csagent/backend/ExperimentMaterials.java`, `ExperimentStore.java`, `ExperimentController.java` (신규) | 자료 로딩(키 분리), 세션 저장, API | 2 |
| `frontend/src/experiment.ts`, `ExperimentRunner.tsx`, `experiment.css` (신규), `main.tsx`(경로 추가) | 진행자 시작 화면, 단계별 참가자 화면, 타이머 | 3 |
| `ai-core/eval/experiment_report.py` (신규) | 내보낸 기록과 키로 O1~O3 계산, 서술 채점 시트 생성·병합 | 4 |
| `docs/experiment/consent.md`, `docs/experiment/protocol.md` (신규) | 동의서·안내문·설문 문항, 진행자 절차서 | 5 |

---

### Task 1: 실험 자료 (데이터 PR)

**Files:** Create `backend/src/main/resources/experiment/materials.json`. Test `backend/src/test/java/com/csagent/backend/ExperimentMaterialsTest.java`(형식 검증. Task 2의 로더를 쓰므로 Task 2 병합 뒤에 테스트를 추가하거나 같은 PR에서 로더와 함께).

**Interfaces:**
- 형식
  ```
  {version, concepts: [{id, title,
    passage: {part1, part2},
    pre_post: {A: [item, item, explanation], B: [item, item, explanation]}}]}
  ```
  - item은 문항 은행 Item과 같은 필드다(`id, format, question, options?, answer, rationale`, 서술은 `rubric`). ID는 `exp-<개념>-<A|B><번호>`다.
- 개념 8개(Claude 판단, 사용자 검수에서 바꿀 수 있음): hash-table, bst, heap, quick-sort, merge-sort, bfs, topological-sort, dynamic-programming. 선수 개념이 2단계 이상이라 막힘이 생기기 쉬운 개념으로 골랐다.
- 읽기 글
  - 1부(400~600자)는 그 개념의 교재식 설명이다. 선수 개념을 설명 없이 쓰는 문장이 적어도 하나 있다(막힘 유발).
  - 2부(200~400자)는 1부를 이해해야 읽히는 다음 내용이다.
- A형과 B형은 같은 학습 목표를 다른 상황으로 묻는다. 난이도를 맞추고, 정답 위치는 고르게 둔다.
- 단답 정답은 테스트로 계산해 대조한다(MVP 2 Task 8 방식).
- 검사: 문항 은행·CS-300과 유사 문항 0건(`ai-core/eval/cs300_overlap.py`와 같은 비교).
- PR 본문에 개념별 글과 문항 원문 표를 넣는다(지문 원문 표 형식).
- [ ] 정답 계산 테스트 작성 → FAIL 확인 → 자료 작성 → PASS → 데이터 PR(사용자 승인 전 병합 금지)

### Task 2: Spring 실험 API

**Files:** Create `ExperimentMaterials.java`, `ExperimentStore.java`, `ExperimentController.java`. Test `backend/src/test/java/com/csagent/backend/ExperimentControllerTest.java`.

**Interfaces** (모두 `/api/experiments`, 응답은 `Cache-Control: no-store`, 오류는 `{"message": 한국어}`)
- `POST /` `{participant: "S1".."S9", grade: 1..4, dsa: "taken"|"taking", exclude: [conceptId]}`
  - 새 세션 `{id(32 hex), participant, stage: "pretest", pretest: [화면용 문항(키 없음)]}`를 만든다.
  - 같은 participant가 있으면 409와 기존 id를 돌려준다(Review Focus 5).
- `GET /{id}`: 현재 단계와 화면 데이터를 돌려준다. 학습 단계면 `remaining_seconds`(서버 시각 기준)를 포함한다.
- `POST /{id}/pretest` `{answers: {itemId: answer}}`
  - 서버가 키로 채점해 개념 1·2와 조건을 1절 규칙대로 배정한다.
  - stage를 `learn1`로 바꾸고 `{concept, condition: "system"|"chat", passage_part1, deadline}`을 돌려준다.
- `POST /{id}/events` `{type: "help_open"|"stuck_session"|"chat_conversation"|"continue_reading"|"timeout", ref?}`
  - 서버 시각으로 기록한다. `continue_reading`이면 2부 글을 돌려준다.
  - 마감 뒤 이벤트는 거부한다(409).
- `POST /{id}/next`: learn1 → learn2 → posttest → survey → done으로 넘긴다. 마감 전이라도 2부를 읽었으면 넘어갈 수 있다.
- `POST /{id}/posttest` `{answers}`, `POST /{id}/survey` `{answers}`
- `GET /export`(진행자용, 로컬에서만 접근): 모든 세션의 기록 JSON. 이름 필드는 없다.
- 저장: `experiment.storage-dir`(기본 `./data/experiments`). 쓰기 방식(원자적 교체)과 동시 요청 잠금은 `StuckSessionStore` 패턴을 따른다.
- [ ] **Step 1: 실패하는 테스트**
  - `pretestHidesKeys`
  - `assignmentFollowsParity`: S1, S2, S3, S4의 조건과 형 배정이 1절 표와 같다.
  - `fewUnknownConceptsFlagged`(Review Focus 3)
  - `deadlineFromServerClock`, `eventsAfterDeadlineRejected`(Review Focus 1·2)
  - `duplicateParticipantConflict`(Review Focus 5)
  - `exportHasNoNames`
- [ ] **Step 2~4:** 실패 확인 → 구현 → `./gradlew test bootJar`
- [ ] **Step 5:** PR(백엔드 파일 4~5개). Task 1 자료가 병합 전이면 테스트용 자료 파일(`src/test/resources`)로 검증한다.

### Task 3: 실험 화면

**Files:** Create `frontend/src/experiment.ts`, `frontend/src/ExperimentRunner.tsx`, `frontend/src/experiment.css`. Modify `frontend/src/main.tsx`(경로 `#/experiment`). Test `frontend/tests/experiment.test.mjs`, `frontend/tests/browser/experiment.spec.ts`.

**Interfaces:**
- 진행자 시작 화면: 참가자 코드, 학년, 수강 여부, 제외할 개념을 받는다. 기존 세션이 있으면 재개를 고르게 한다.
- 참가자 화면
  - 실험 중에는 로드맵, 개념 목록, 다른 메뉴를 숨긴다.
  - 단계 제목과 남은 시간(서버 `remaining_seconds` 기준)만 보인다.
- 학습 단계
  - 왼쪽에 읽기 글, 오른쪽에 도구를 둔다. 휴대폰 폭은 고려하지 않는다(대면, 노트북 진행).
  - `system`: "막혔어요" 버튼을 누르면 `StuckPanel`이 붙여넣기 진입으로 열린다. 학생이 막힌 문장을 붙여넣는다. 시작한 막힘 세션 id를 `stuck_session` 이벤트로 보낸다.
  - `chat`: 새 대화 id로 `ChatSession`을 연다. 첫 메시지 때 `help_open`과 `chat_conversation` 이벤트를 보낸다.
  - 두 조건 모두 도구를 처음 열 때 `help_open`을 보낸다.
  - "계속 읽기"를 누르면 `continue_reading`을 보내고 2부를 보여 준다.
  - 마감이 되면 도구를 닫고 `timeout`을 보낸 뒤 다음 단계 버튼만 남긴다.
- 사전·사후 시험과 설문은 한 화면 목록 형식이다. 제출 후에는 수정하지 못한다.
- [ ] **Step 1: 실패하는 테스트**
  - 남은 시간 표시가 서버 값을 따른다(새로고침 시뮬레이션).
  - 조건별 화면 요소(Review Focus 4): chat 화면에 [막혔어요]가 없고, system 화면에 자유 채팅 입력이 없다.
  - 마감 뒤 "계속 읽기"가 비활성화된다.
- [ ] **Step 2~4:** 실패 확인 → 구현 → `npm test && npm run build`, Playwright(가짜 API)
- [ ] **Step 5: 실제 스택 리허설 1회** — 워커 포트에서 처음부터 끝까지 진행하고 스크린샷을 남긴다. 운영 8000을 쓴다.
- [ ] **Step 6:** PR(파일 5개 안팎)

### Task 4: 실험 결과 계산

**Files:** Create `ai-core/eval/experiment_report.py`. Test `ai-core/tests/test_eval_experiment.py`.

**Interfaces:**
- `grading-sheet --export <json> --out ai-core/outputs/experiment/grading-<grader>.csv`
  - 사후 서술 답만 모은다. 순서를 섞고 참가자, 조건, 개념 순서를 가린다(눈가림).
  - 열 구성은 Task 4(MVP 3) 채점 시트와 같다.
- `report --export <json> --materials backend/src/main/resources/experiment/materials.json --grades <csv...> --out ai-core/outputs/experiment/report.json`
  - 학생별 표: 개념, 조건, 사전 정답 수, 사후 정답 수, 변화, 서술 점수(충족 비율), O2, O3(초)
  - 조건별 요약: 차이(system − chat)의 중앙값과 범위, 학생 수
  - "사전 점수 높음" 학생은 따로 표시한다.
  - 기존 `metrics.py`를 쓴다.
- [ ] 가짜 기록 3명으로 테스트 → 구현 → AI core 전체 → PR

### Task 5: 동의서·안내문·설문·진행 절차서 (문서)

**Files:** Create `docs/experiment/consent.md`, `docs/experiment/protocol.md`.
- 동의서: 목적(학습 도구 두 가지 비교), 소요 시간, 수집 항목(코드, 학년, 수강 여부, 답, 시간 기록), 보관과 삭제(보고서 작성 뒤 원문 삭제), 언제든 중단 가능. 가설은 쓰지 않는다.
- 설문 5문항: 각 도구의 도움 정도(1~5), 더 쓰고 싶은 도구, 막힌 지점, 자유 의견.
- 진행자 절차서
  - 시작 전 점검: 서버 상태, 8000 대량 측정 중지 확인
  - 진행 순서, 진행자가 말할 문장(가설 언급 금지), 문제 발생 시 대처(새로고침, 재개)
  - 끝난 뒤 내보내기와 채점 시트 전달
- 코디네이터가 초안을 쓰고 사용자가 확인한다. 문서 PR이다.

## 실행 배치 (Orca)

| 워커 | Task | 비고 |
| --- | --- | --- |
| 콘텐츠 | 1 | 지금 하던 Mohler 루브릭·Task 4(MVP 3)·KMMLU 분류 다음. 실험 일정상 3주차 안에 데이터 PR |
| Backend(새로 투입) | 2 | |
| Frontend | 3 | Task 2의 API 형식이 정해지면 가짜 API로 먼저 시작 |
| AI 엔지니어 | 4 | 품질 하한·Mohler 다음 |
| 코디네이터 | 5, 리허설 일정 | 사용자 리허설 1회 → 5주차 실험 |

- 순서: Task 2와 Task 3은 3주차, Task 1은 3주차 말 데이터 PR, Task 4·5는 4주차다. 4주차 말에 사용자가 리허설을 하고, 5주차에 실험을 진행한다.
