# MVP 3: 평가 파이프라인 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> 이 저장소에서는 Orca 코디네이터가 Task를 워커에게 나눠 맡긴다(설계 7절). 워커는 `orca-superpowers` 워커 편을 따른다.

**Goal:** 디펜스 근거가 될 수치를 만드는 도구와 첫 결과를 준비한다. 측정 대상은 네 가지다.
- 진단 정확도(M1)
- 최소 보충(M2)
- 서술 채점 신뢰도(M3 개발용 측정과 한국어 채점 준비)
- 품질 하한

그래프 탐색·정렬·트리 순회 애니메이션도 이 단계에서 마무리한다.

**Architecture:**
- 측정 도구는 모두 `ai-core/eval/` 아래 스크립트로 둔다. 실행 중인 서비스 코드를 바꾸지 않는다.
- 외부 데이터와 학생 답 원문, 측정 결과는 Git 밖(`ai-core/data/raw/`, `ai-core/data/processed/`, `ai-core/outputs/`, 모두 git-ignored)에 둔다.
- 시뮬레이션 학습자는 사람과 같은 공개 API(Spring `/api/stuck/sessions`)로 막힘 흐름을 끝까지 진행한다. 결과는 화면에 보이는 view만으로 계산한다.
  - 시험 스택은 워커 포트에 따로 띄운다(AI core → 운영 8000 vLLM).
  - 서술 답을 쓰는 학습자 모델은 서빙과 다른 계열로, GPU 1번의 별도 포트에서 돌린다.
- 채점 일치도 계산은 하나의 모듈로 묶는다. Mohler 개발 검증과 한국어 사람 라벨 분석이 같은 코드로 같은 지표를 낸다.

**Tech Stack:** Python 3(표준 라이브러리 + 기존 의존성), 기존 `StuckModelGateway`, vLLM(GPU 1번 학습자 모델), Spring 공개 API, React(애니메이션 장면 렌더러는 그대로).

**Spec:** `docs/superpowers/specs/2026-10-26-completion-roadmap-design.md` (3-3절 MVP 3, 2-4절 평가), `docs/evaluation-datasets.md`, `docs/project-direction.md` Level 2(M1~M3 정의와 목표 수치)

## Global Constraints

- MVP 2 계획(`2026-10-26-mvp2-content-animation.md`)의 Global Constraints를 그대로 따른다. 브랜치·커밋·PR 형식, 파일 수, 주석, 데이터 PR 승인, 포트, 운영 서비스 금지가 포함된다.
- **목표 수치**(project-direction Level 2): 진단 recall(M1) ≥ 0.8, 과잉 보충률(M2) ≤ 20%, 판정 일치도(M3) kappa ≥ 0.6, 거짓 통과율(M3) ≤ 10%. 미달이어도 수치를 그대로 보고한다.
- **포트**
  - 시뮬레이션 시험 스택: AI core 21100, Spring 21101.
  - 학습자 모델 vLLM(GPU 1번): 20900.
  - 운영 8000은 요청을 하나씩 보낸다. 대량 실행(품질 하한 3회 반복 등)은 시작·끝 시각을 PR 본문에 적는다.
- **GPU 1번:** 학습자 모델 하나만 띄운다. 끝나면 내린다. GPU 0번(운영)은 건드리지 않는다.
- **외부 데이터**
  - 원본은 `ai-core/data/raw/<데이터셋>/`에만 둔다. 저장소에는 내려받기 스크립트와 고른 문항 ID 목록만 둔다.
  - KMMLU(CC-BY-ND)는 번역·수정본을 저장소에 두지 않는다.
- **CS-300:** Git에 넣지 않는다. CS-300 관련 작업은 `codex/model-evaluation/`(Git 밖)에서만 한다.
- **학생 답 원문, 채점 시트, 사람 라벨:** `ai-core/outputs/` 아래에만 둔다. 이름 대신 작성자 코드(학생 A, B…)와 개발자 표시만 쓴다.
- **평가 설계 데이터:** 사람이 내용을 보증해야 하는 데이터(고정 프로필, 문항 ID 목록 중 사람이 고른 것)는 데이터 PR로 사용자 승인을 받는다. 코드가 결정적으로 생성하는 것은 코드 PR이다.

## Review Focus

1. **시뮬레이션 학습자의 지식 상태가 엔진 가정과 어긋나는 경우.** 엔진은 "선수 개념을 알면 그 아래도 안다"고 보고 가지치기한다. 프로필이 이 가정을 어기면(위는 알고 아래는 모름) M1이 엔진 탓이 아닌 이유로 낮게 나온다. 기본 프로필은 가정을 지키게 만들고, 어기는 프로필은 따로 묶어 보고한다. (Task 2 테스트)
2. **정답 상한 효과.** 진단 문항은 최대 5개다. 빈틈이 5개를 넘는 프로필에서는 recall 상한이 1보다 낮다. 상한을 함께 계산해 보고해야 엔진 실패와 구분된다. (Task 2 테스트)
3. **Mohler 점수를 통과·미통과로 바꾸는 기준을 결과를 보고 고르는 경우.** 기준값은 개발 분할(문항 절반)에서 정하고, 보고 분할에서 한 번만 잰다. (Task 3 테스트)
4. **채점자 1명일 때와 2명일 때의 계산 경로.** 채점자 1명이면 사람 대 사람 kappa 대신 채점자 내 일치도를 내야 한다. 빈 값이나 한쪽만 채점한 답이 계산을 깨면 안 된다. (Task 4 테스트)
5. **품질 하한 응답 파싱 실패.** 모델이 보기 기호 대신 문장을 내면 오답으로 셀지, 파싱 실패로 따로 셀지 정해야 한다. 파싱 실패율은 따로 보고한다. (Task 5 테스트)

## 파일 구조

| 파일 | 책임 | Task |
| --- | --- | --- |
| `ai-core/serving/animation/graph.py`, `sorting.py` (신규), `model.py`, `__init__.py` | 그래프 탐색(BFS·DFS)·정렬(삽입·병합·퀵)·트리 순회 장면과 개념별 장면 목록 | 1 |
| `ai-core/eval/metrics.py` (신규) | kappa, Wilson 구간, 가중 kappa, recall·precision 등 공통 지표 | 2·3·4·5 |
| `ai-core/eval/sim_learner/` (신규: `profiles.py`, `learner.py`, `client.py`, `run.py`) | 지식 상태 프로필 생성, 공개 API 진행, 답 생성, M1·M2 계산 | 2 |
| `ai-core/eval/mohler.py` (신규) | Mohler 내려받기·분할·루브릭 채점 실행·지표 | 3 |
| `ai-core/eval/grading_sheet.py`, `ai-core/eval/ai_grade.py`, `ai-core/eval/agreement.py` (신규) | 한국어 답 채점 시트 생성, AI 채점, 사람·AI 일치도 | 4 |
| `ai-core/eval/quality_floor.py`, `ai-core/eval/quality_floor_ids/` (신규) | KMMLU·CS-Bench 선별·실행·보기만 조건 | 5 |
| `ai-core/tests/test_eval_*.py` (신규) | 위 도구 테스트(가짜 데이터·가짜 클라이언트) | 각 Task |
| `docs/evaluation-results.md` (신규) | 측정 결과 요약. 수치와 한계만 넣고 원본 데이터는 넣지 않음 | 7 |

---

### Task 1: 그래프 탐색·정렬·트리 순회 애니메이션

**Files:** Create `ai-core/serving/animation/graph.py`, `ai-core/serving/animation/sorting.py`. Modify `model.py`, `__init__.py`(CONCEPT_SCENES, SCENE_FORMATS, 도구 스키마). Test `ai-core/tests/test_animation_graph_sort.py`.

**Interfaces:**
- Consumes: MVP 2의 `Box/Arrow/Frame/Animation`, `simulate`, `CONCEPT_SCENES`, 장면 선택 json_schema 생성 함수.
- Produces
  - `GraphTraversalRequest(kind='graph_traversal', nodes: list[str] 2..8, edges: list[[str,str]] ≤12, directed: bool, start: str, order: Literal['bfs','dfs'])`
    - 이웃은 이름 오름차순으로 방문한다. 프레임마다 방문 표시(tone `active`/`normal`)와 큐 또는 스택 상태를 보여 준다.
  - `SortRequest(kind='sort', algorithm: Literal['insertion','merge','quick'], values: list[int] 2..8)`
    - 퀵 정렬은 Lomuto 분할, 피벗은 마지막 원소다. 교환·병합 단계마다 프레임을 만들고 `MAX_FRAMES` 안이어야 한다.
  - `TreeTraversalRequest(kind='tree_traversal', values: list[int] 1..9, order: Literal['preorder','inorder','postorder'])`
    - values를 차례로 BST에 넣은 트리를 순회한다.
  - `CONCEPT_SCENES`에 추가: bfs·graph → `graph_traversal`(bfs), dfs → `graph_traversal`(dfs), sorting-basics → `sort`(insertion), merge-sort → `sort`(merge), quick-sort → `sort`(quick), tree-traversal → `tree_traversal`.
  - 개념별로 허용 algorithm/order를 좁힌다. 예: merge-sort는 merge만.
- [ ] **Step 1: 실패하는 테스트**
  - `test_bfs_order_matches_reference`: 무방향 A-B, A-C, B-D, C-D, start A. 방문 순서 `A,B,C,D`는 deque 참조 구현과 같다.
  - `test_dfs_order_matches_reference`: 같은 그래프에서 `A,B,D,C`(재귀 참조 구현과 같음).
  - `test_sort_final_sorted_and_steps`: 세 알고리즘 모두 마지막 프레임이 정렬 결과와 같다. 퀵 정렬 첫 분할 뒤 피벗 위치는 Lomuto 참조와 같다.
  - `test_tree_traversal_orders`: values `[8,3,10,1,6]`일 때 inorder `1,3,6,8,10`, preorder `8,3,1,6,10`, postorder `1,6,3,10,8`.
  - `test_unknown_start_rejected`, `test_frame_limit`
  - `test_concept_scenes_restrict_algorithm`: merge-sort의 장면 선택 스키마가 algorithm `merge`만 허용한다.
- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m unittest tests.test_animation_graph_sort -v`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — AI core 전체 테스트
- [ ] **Step 5: 실측** — `ai-core/eval/animation_choice.py`로 새 개념 6개를 3회씩 잰다. 성공률, 표본 관련성, 보충 글과 장면의 예시 일치를 PR 본문에 적는다.
- [ ] **Step 6: 커밋·PR** — `feat(ai-core): 그래프 탐색·정렬·트리 순회 애니메이션 추가`. 프런트엔드는 범용 렌더러라 바뀌지 않아야 한다. 375px 확인은 기존 Playwright 사례에 새 장면 하나를 추가해서 한다(Frontend 파일 1개).

### Task 2: 시뮬레이션 학습자와 M1·M2

**Files:** Create `ai-core/eval/metrics.py`, `ai-core/eval/sim_learner/{__init__,profiles,learner,client,run}.py`. Test `ai-core/tests/test_eval_metrics.py`, `ai-core/tests/test_sim_learner.py`.

**Interfaces:**
- `metrics.py`
  - `cohen_kappa(a: list[bool], b: list[bool]) -> float`
  - `wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]`
  - `quadratic_weighted_kappa(a: list[int], b: list[int], labels: list[int]) -> float`
  - `recall_precision(predicted: set, truth: set) -> tuple[float, float]`
- `profiles.py`
  - `Profile(id: str, target: str, known: frozenset[str], slip: float, guess: float, consistent: bool)`
  - `generate_profiles(concept_map, targets: list[str], per_target: int, seed: int) -> list[Profile]`
    - 기본 프로필은 known이 선수 관계에 대해 닫혀 있다(consistent=True). X를 알면 X의 선수 개념도 모두 안다.
    - targets는 지도에서 깊이 3 이상인 개념 10개를 고른다. 고르는 규칙은 결정적이고, 코드 주석에 근거를 적는다.
    - 대상 하나당 빈틈 조합을 3~5개 만든다(빈틈 0개 조합 1개 포함).
    - 별도 묶음으로 consistent=False 프로필을 대상당 1개 만든다.
  - `true_gaps(profile, concept_map, max_depth=3) -> set[str]`: 대상 개념과 깊이 3 안의 선수 개념 중 모르는 것.
  - `recall_ceiling(profile, concept_map) -> float`: 진단 문항 5개 상한 때문에 엔진이 이론상 찾을 수 있는 최대 recall.
- `learner.py`
  - 자동 채점 문항: 아는 개념이면 문항 정답 키로 답한다. 확률 slip으로 일부러 틀린다.
    - 학습자는 문항 은행을 읽을 수 있는 시험 장치로 본다. 사람 학습자를 흉내 내는 것이 아니라 "지식 상태가 정해진 응답자"다.
  - 모르는 개념이면 선택형은 확률 guess로 맞히고, 아니면 틀린 보기를 고른다. 단답은 틀린 값을 낸다.
  - 서술 문항: 학습자 모델(GPU 1번, 서빙과 다른 계열)에 "알면 정확하게, 모르면 불완전하게" 지시해 답을 받는다.
  - 보충을 받은 개념은 "알게 됨"으로 바꾼다. 확인을 통과해야 다음으로 가는 흐름을 그대로 겪게 하기 위해서다.
  - 학습자 모델 선택(Qwen3.5-9B AWQ와 Qwen3.6-27B AWQ 중)은 빌더 몫이다. 고른 근거(속도·지시 따르기)를 PR 본문에 적는다.
- `client.py`: Spring 공개 API(`POST /api/stuck/sessions`, `POST /{id}/step`) 호출. 409·503은 재시도 규칙(최대 3회, 2초 간격)을 둔다.
- `run.py --profiles <n> --base-url http://127.0.0.1:21101 --learner-url http://127.0.0.1:20900/v1 --out ai-core/outputs/eval/sim/<날짜>.jsonl`
  - 프로필마다 로드맵 진입으로 세션을 끝까지 진행한다.
  - 기록: 진단 결과 view의 `gaps`(시스템 빈틈), 보충 view의 개념 목록, 단계 수, 걸린 시간.
  - `summarize(results) -> dict`
    - M1: recall·precision 평균과 분포, 상한 대비 비율
    - M2: 과잉 보충률 = 보충한 개념 중 프로필이 이미 아는 개념의 비율
    - consistent=False 묶음은 따로 낸다.
- [ ] **Step 1: 실패하는 테스트**
  - 지표: `cohen_kappa` 완전 일치 1.0, 우연 수준 0 근처. `wilson_interval(1,50)`의 상한이 약 0.105.
  - 프로필
    - `test_profiles_are_prerequisite_closed`: consistent 프로필의 known은 선수 관계에 대해 닫혀 있다.
    - `test_true_gaps_respects_depth`
    - `test_recall_ceiling_when_gaps_exceed_items`(Review Focus 1·2)
  - 학습자: slip=0, guess=0이면 아는 개념 문항은 모두 정답, 모르는 개념은 모두 오답이다.
  - 실행: 가짜 API로 진단→보충→확인→복귀를 끝내고, `summarize`가 M1·M2를 계산한다.
- [ ] **Step 2~4:** 실패 확인 → 구현 → AI core 전체 테스트
- [ ] **Step 5: 실제 실행**
  - 워커 포트에 시험 스택(AI core 21100 → 8000, Spring 21101, 저장소는 임시 경로)과 학습자 모델(GPU 1번, 20900)을 띄운다.
  - 기본 프로필 약 50개와 consistent=False 10개를 돌린다.
  - 결과 요약(M1·M2, 상한, 실패 사례 분류)을 PR 본문에 적는다. 끝나면 모든 프로세스를 내린다.
- [ ] **Step 6: 커밋·PR** — 도구와 테스트는 코드 PR이다. 고정 프로필 파일은 만들지 않는다(생성 규칙과 seed만 코드에 둔다).

### Task 3: Mohler 영어 원문 개발 검증

**Files:** Create `ai-core/eval/mohler.py`. Test `ai-core/tests/test_eval_mohler.py`.

**Interfaces:**
- `download(out_dir=ai-core/data/raw/mohler)`: Hugging Face 미러 `Viswa45/ASAG`의 `raw/open_ended`를 내려받는다. 열은 id, question, instructor_answer, student_answer, score_grader_1, score_grader_2, score_avg다. 라이선스 표기(CC-BY-4.0, 미러 기준)와 원 논문 인용을 README 주석에 적는다.
- `split_questions(rows, seed) -> (dev_ids, report_ids)`: 문항(question) 단위로 절반씩 나눈다. 같은 문항의 답이 양쪽에 섞이지 않는다.
- 루브릭
  - 콘텐츠 워커가 문항 80개의 instructor_answer에서 핵심 요소 2~4개를 영어로 뽑아 `ai-core/data/processed/mohler/rubrics.jsonl`(Git 밖)에 둔다.
  - 형식은 `{question_id, rubric: [..]}`다.
  - 사용자가 확인할 표본 20개를 `ai-core/outputs/eval/mohler/rubric-sample.md`로 만든다(설계 3-6: 사용자는 표본 20개만).
- `grade(rows, rubrics, gateway) -> list[{id, met: list[bool]}]`
  - 운영 채점 경로 `StuckModelGateway.grade_explanation`을 그대로 쓴다. 문항과 루브릭은 영어 그대로 넘긴다.
  - 요청은 하나씩 보내고, 중간 결과를 이어서 저장한다(중단 후 재개).
- `evaluate(rows, grades, dev_ids, report_ids) -> dict`
  - 사람 통과 기준: score_avg ≥ 4.0을 주 기준으로 한다. 3.5와 4.5도 함께 보고한다 **(Claude 판단, 결과 보기 전에 고정)**.
  - AI 통과 기준: 충족 비율 임계값을 dev 분할에서 kappa가 가장 큰 값으로 고른다. 후보는 1.0, 0.75, 0.67, 0.5다. report 분할에서는 그 값 하나로만 잰다.
  - 보고 지표
    - report 분할의 kappa
    - 거짓 통과율(사람 미통과 중 AI 통과)과 Wilson 95% 구간
    - 충족 비율과 score_avg의 Spearman
    - 사람 채점자 둘 사이의 kappa(같은 통과 기준). AI-사람 일치도의 현실적 상한이다.
  - 점수가 정답 쪽으로 치우쳐(평균 4.17) 생기는 kappa 역설에 대비해 일치율, 민감도, 특이도도 함께 낸다.
- [ ] **Step 1: 실패하는 테스트**
  - `test_split_by_question_no_leak`
  - `test_threshold_chosen_on_dev_only`(Review Focus 3): report 분할 데이터를 바꿔도 고른 임계값이 같다.
  - `test_false_pass_rate_and_interval`
  - `test_resume_skips_graded_ids`
- [ ] **Step 2~4:** 실패 확인 → 구현 → AI core 전체 테스트
- [ ] **Step 5: 실제 실행**
  - 루브릭 초안이 준비되고 사용자가 표본 20개를 확인한 뒤 2,273개를 채점한다(8000, 순차, 약 40분 예상).
  - 결과 요약을 PR 본문과 Task 7 결과 문서에 쓴다.
- [ ] **Step 6: 커밋·PR** — 도구와 테스트만 커밋한다. 데이터·루브릭·결과는 Git 밖에 둔다.

### Task 4: 한국어 답 채점 시트·AI 채점·일치도

**Files:** Create `ai-core/eval/grading_sheet.py`, `ai-core/eval/ai_grade.py`, `ai-core/eval/agreement.py`. Test `ai-core/tests/test_eval_grading.py`.

**Interfaces:**
- 입력: MVP 2 답 시트 CSV들(`answer_id, item_id, concept, question, phase, author, answer`). author는 `dev`(개발자) 또는 `S1`~`S5` 같은 학생 코드다.
- `grading_sheet.py --answers <csv...> --grader <G1|G2> --out ai-core/outputs/grading/<grader>.csv`
  - 빈 답은 뺀다.
  - 순서는 채점자마다 다른 seed로 섞는다.
  - 열은 `answer_id, question, answer, rubric_1..4, met_1..4(빈칸), overall_pass(빈칸), note`다.
  - 작성자, phase, AI 판정은 넣지 않는다(눈가림).
  - 채점 기준표(루브릭 요소별 "충족" 예시를 적는 칸)를 별도 시트로 함께 만든다.
  - 연습용 답 5개는 개발자가 쓴 답에서 고르고, `practice=1`로 표시해 결과에서 뺀다.
- `ai_grade.py --answers <csv...> --out ai-core/outputs/grading/ai.csv`
  - 운영 채점 경로로 각 답을 채점한다(8000, 순차).
  - met와 overall은 계획 Task 4(MVP 2)의 판정 규칙을 따른다. 서술 판정은 루브릭이 모두 충족이어야 통과다.
- `agreement.py --sheets G1.csv [G2.csv] --ai ai.csv --answers <csv...> --out ai-core/outputs/grading/report.json`
  - 채점자 2명이면 사람 간 kappa(전체 판정, 요소별)를 낸다. 판정이 갈린 답 목록을 내고, 합의 라벨 파일(`final.csv`, 갈린 답만 채우는 칸)을 받아 최종 라벨을 만든다.
  - 채점자 1명이면 2주 뒤 재채점 시트(같은 답 50개, 다른 seed)를 만들고 채점자 내 kappa를 낸다.
  - AI 대 최종 라벨: kappa, 거짓 통과율과 Wilson 95% 구간을 낸다. 학생 답만, 개발자 답만, 전체를 따로 계산한다(설계 2-4).
  - 미통과 답이 100개 미만이면 보고서 맨 위에 "거짓 통과율 상한 기준 미달 가능"을 표시한다.
- [ ] **Step 1: 실패하는 테스트**
  - `test_sheet_hides_author_phase_ai`
  - `test_two_graders_kappa_and_disagreements`
  - `test_single_grader_uses_regrade_path`(Review Focus 4)
  - `test_partial_sheet_does_not_crash`: 한쪽만 채점한 답은 사람 간 계산에서 빼고 개수를 보고한다.
  - `test_split_by_author_group`
- [ ] **Step 2~4:** 실패 확인 → 구현 → AI core 전체 테스트
- [ ] **Step 5: 사용 안내**
  - 사용자와 석사 과정생이 쓸 짧은 안내를 PR 본문에 넣는다: 시트 받는 곳, 채점 방법, 연습 5개, 돌려줄 파일.
  - 실제 채점은 답이 모인 뒤 사람이 한다.
- [ ] **Step 6: 커밋·PR**

### Task 5: 품질 하한 시험 (KMMLU·CS-Bench 선별, CS-300 추적)

**Files:** Create `ai-core/eval/quality_floor.py`, `ai-core/eval/quality_floor_ids/cs_bench.json`, `ai-core/eval/quality_floor_ids/kmmlu.json`. Test `ai-core/tests/test_eval_quality_floor.py`.

**Interfaces:**
- 내려받기
  - CS-Bench `CS-Bench/English/test.jsonl`(열: ID, Domain, SubDomain, Format, Question, A~D, Answer)
  - KMMLU `Computer-Science` test(1,000문항, 열: question, A~D, answer 1~4, Human Accuracy)
  - 둘 다 `ai-core/data/raw/` 아래에 둔다.
- 선별
  - CS-Bench: Domain이 `Data Structure and Algorithm` 또는 `Operating System`이고, Format이 `Multiple-choice` 또는 `Assertion`인 문항이다(코드 규칙). 영어 test 기준 860문항이다. 고른 ID 목록은 코드가 만들고 커밋한다.
  - KMMLU: 문항마다 자료구조, 운영체제, 기타로 나눈다. 분류는 콘텐츠 워커(Claude)가 읽고 정한다. 결과 ID 목록(`kmmlu.json`, 분류 근거 한 줄씩)은 **데이터 PR**이며, 사용자가 표본 20개를 확인한다.
- `quality_floor.py run --set cs_bench|kmmlu --runs 3 [--options-only] --out ai-core/outputs/eval/quality-floor/<날짜>-<set>.jsonl`
  - 운영 서빙 모델·설정(추론 끔, 온도 0)으로 json_schema `{answer: "A"|"B"|"C"|"D"}`(Assertion은 `"True"|"False"`)를 요청한다.
  - `--options-only`는 질문을 지우고 보기만 준다(Balepur 2024 방식).
  - 파싱 실패는 오답과 따로 센다(Review Focus 5).
- `summarize`: 세트·영역·형식별 정답률을 3회 평균±범위로 낸다. 보기만 조건의 정답률도 낸다. KMMLU는 문항별 수험생 정답률 평균과 나란히 둔다.
- CS-300 추적 60(Git 밖 작업, AI 엔지니어 또는 AI core 워커)
  - 풀이를 다시 적기만 한 29개를 `codex/model-evaluation/cs-300-v1/` 안에서 실행 코드로 바꿔 정답을 재검증한다.
  - 서빙 모델로 추적 60문항을 3회 푼다.
  - 결과를 `codex/model-evaluation/cs300-reliability.md`에 덧붙인다.
- [ ] **Step 1: 실패하는 테스트**
  - `test_cs_bench_selection_rule`: 가짜 행으로 영역·형식 필터를 확인한다.
  - `test_options_only_removes_question`
  - `test_parse_failure_counted_separately`
  - `test_summary_mean_and_range`
- [ ] **Step 2~4:** 실패 확인 → 구현 → AI core 전체 테스트
- [ ] **Step 5: 실제 실행**
  - CS-Bench 860문항 × 3회 + 보기만 1회, KMMLU 선별분 × 3회 + 보기만 1회를 운영 8000에 순차로 보낸다.
  - 사용이 적은 시간대에 돌린다. 시작·끝 시각을 기록한다.
- [ ] **Step 6: 커밋·PR** — 도구와 CS-Bench ID 목록은 코드 PR이다. KMMLU 분류 목록은 별도 데이터 PR이다.
- [ ] **(선택) CS-Bench 원문·번역 쌍 비교**(설계 2-4)
  - 자료구조 객관식 100문항을 서빙 모델이 아닌 모델로 번역해 같은 방식으로 3회 풀고, 원문과 McNemar로 비교한다. 답이 갈린 문항은 번역 오류인지 사람이 분류한다.
  - 일정이 밀리면 가장 먼저 뺀다(설계 3-5).

### Task 6: 비교 실험 모드 (조건부)

- 학생 4~5명이 비교 실험(1인 1시간, 대면 1회)에 참여한다고 사용자가 확인하면 진행한다. 늦어도 2주차 말까지 확인한다.
- 확인되면 이 Task만 따로 상세 계획을 쓴다(Backend·Frontend 포함). 범위는 설계 3-3 MVP 3의 항목 4와 같다.
  - 참가자 ID
  - 개념별 조건 배정(제안 시스템 / 같은 모델 일반 채팅)과 학생마다 반대 배정
  - 사전·사후 A·B형 문항(데이터 PR)
  - 시간 기록, 결과 내보내기
- 확인되지 않으면 만들지 않는다. O1~O3는 "측정하지 못함"으로 보고한다.

### Task 7: MVP 3 결과 정리 (코디네이터)

- [ ] `docs/evaluation-results.md`를 만든다.
  - M1·M2(시뮬레이션), Mohler 개발 지표, 품질 하한(세트별), 장면 선택 성공률, 측정 조건과 한계를 넣는다.
  - 원본 데이터는 넣지 않고 Git 밖 경로만 적는다.
- [ ] 한국어 답 수집 현황(답 수, 미통과 추정)과 채점 일정(사용자·석사 과정생)을 정리한다.
- [ ] 설계 3-3 MVP 3 완료 기준과 대조한다. 미달 항목은 원인과 다음 조치를 적는다.
- [ ] 주별 발표 자료
  - 3주차: 시뮬레이션 첫 수치, 새 애니메이션
  - 4주차: Mohler 결과, 품질 하한, 채점 진행

## 실행 배치 (Orca)

| 워커 | Task | 비고 |
| --- | --- | --- |
| AI core | 1 → 2 | 2의 학습자 모델 서빙(GPU 1번) 포함 |
| AI 엔지니어(새로 투입) | 3의 도구·실행, 5의 도구·실행, CS-300 추적 | 지표 해석은 `metrics.py`(Task 2) 병합 뒤 |
| 콘텐츠 | 3의 Mohler 루브릭 초안, 5의 KMMLU 분류(데이터 PR), 4 | 4는 답 수집 일정에 맞춰 3주차 안 |
| Frontend | 1의 Playwright 사례 1개. 6이 확정되면 6 | 확정 전에는 대기 |
| 검수자 2명 | 코드 PR(검수자 1), 데이터 PR 사전 검토(검수자 2) | |
| 코디네이터 | 7, 시험 스택·GPU 사용 조율 | |

- 순서
  - Task 2의 `metrics.py`를 먼저 작은 PR로 병합한다. 3·4·5가 같은 지표를 쓰기 때문이다.
  - 8000 사용이 겹치지 않게 대량 실행(Task 3·5의 실제 실행, Task 2의 실제 실행)은 코디네이터가 순서를 정한다.
