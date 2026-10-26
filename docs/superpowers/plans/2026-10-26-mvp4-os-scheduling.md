# MVP 4: 운영체제 CPU 스케줄링 단원 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> 이 저장소에서는 Orca 코디네이터가 Task를 워커에게 나눠 맡긴다. 워커는 `orca-superpowers` 워커 편을 따른다.

**Goal:** 운영체제 CPU 스케줄링 단원을 자료구조와 같은 연결 지도에 붙인다. 이 단원에서 막힌 학생을 자료구조 선수 개념(큐·우선순위 큐)까지 진단해 보충한 뒤 원래 개념으로 돌려보낸다. 이로써 이 도구가 다른 과목에도 적용됨을 보인다.

**Architecture:**
- 과목마다 `ai-core/content/<과목>/concept-map.json`과 `items-*.jsonl`을 둔다.
  - 로더가 모든 과목 지도를 하나의 지도로 합친다. 과목 사이 선수 관계(예: `round-robin` → `queue`)를 허용한다.
  - 순환·고립·간접 관계 검증은 합친 지도 전체에 적용한다.
- 각 개념에는 `subject`(과목 ID)를 붙인다. `/concept-map` 응답에 과목 목록을 함께 준다. 화면은 과목 이름을 영역 표시와 로드맵 구분에 쓴다.
- 운영체제 애니메이션은 간트 차트(시간축 위 프로세스 배치)다. 기존 애니메이션 모델(Box·Frame, 12×8 칸)을 그대로 쓴다.
  - 상태는 코드가 결정적으로 계산한다. 모델은 알고리즘과 프로세스 입력만 고른다.
  - 프런트 재생기는 바꾸지 않는다.
- 막힘 엔진(`stuck.py`)은 바꾸지 않는다. 합친 지도를 받으면 과목 경계를 넘어 그대로 내려간다.

**Tech Stack:** Python(pydantic, unittest), React 19 + TypeScript, Spring은 프록시만(변경 없음 예상). 새 라이브러리 없음.

**Spec:** `docs/superpowers/specs/2026-10-26-completion-roadmap-design.md`
- 2-1: 과목마다 애니메이션 종류가 다르다. 운영체제는 시간축 위 프로세스 배치다.
- 2-3: 하나의 연결 지도
- 3-3 MVP 4 항목 3과 검증: "운영체제 단원에서 막힘 흐름이 자료구조 선수 개념(큐·우선순위 큐)까지 내려가 보충한 뒤 복귀한다."
- 3-6: 운영체제 개념 5~6개, 약 30문항

## Global Constraints

- PR 하나는 하나의 목적이고, 변경 파일 4~5개 이내를 목표로 한다. main에 직접 커밋하지 않는다. `Co-Authored-By`를 넣지 않는다.
- 데이터 PR(개념 지도·문항)은 2026-10-26 사용자 위임에 따라 검수자가 객관적으로 검토한 뒤 병합한다.
  - 검토 항목: 정답 독립 계산, 루브릭 정확성, CS-300 중복 없음, 형식.
- 애니메이션 상태를 모델이 지어내지 않는다. 장면 입력 검증에 실패하면 장면을 보여 주지 않는다.
- 기존 자료구조 지도 36개 개념과 문항 180개의 ID·내용은 바꾸지 않는다. 기존 테스트는 모두 통과해야 한다.
- 브라우저 학습 상태 저장 키(`cs-dsa-roadmap-v1`)에 저장된 기존 기록이 사라지지 않게 한다.
- 화면 작업은 `design-taste-frontend` 스킬을 따른다.

## Review Focus

1. 과목 사이 선수 관계 검증
   - 운영체제 개념이 자료구조 개념을 가리킬 때 간접 관계 검사가 합친 지도 기준으로 동작해야 한다.
   - 예: `sjf` → `priority-queue`를 직접 두면서 다른 경로로도 닿으면 거부한다.
2. 간트 차트 동점 처리
   - 도착 시각이 같거나 남은 시간이 같을 때의 순서 규칙이 문서·코드·테스트에서 같아야 한다. 규칙은 "도착 시각 → 프로세스 이름 순"이다.
   - RR에서 새로 도착한 프로세스와 시간 할당량이 끝난 프로세스가 같은 시각에 큐에 들어갈 때는 새 도착이 먼저다(교과서 관례). 이 규칙을 테스트로 고정한다.
3. CPU 유휴 시간: 도착 전 빈 구간이 있는 입력에서 간트 칸이 "-"(유휴)로 그려지고 대기 시간 계산이 맞아야 한다.
4. 기존 저장 기록 호환: 과목 필드가 없던 브라우저 저장 기록을 읽어도 화면이 깨지지 않아야 한다.
5. 막힘 흐름의 과목 경계: 운영체제 개념에서 진단이 자료구조 개념 문항을 내고, 보충 후 원래 운영체제 개념으로 돌아와야 한다(E2E).

---

### Task 1: 여러 과목 지도·문항 로더 (AI core, 코드 PR)

**Files:**
- Modify: `ai-core/serving/concept_map.py`
- Modify: `ai-core/serving/item_bank.py`
- Modify: `ai-core/serving/api.py`(`/concept-map` 응답)
- Test: `ai-core/tests/test_concept_map.py`(기존 파일에 추가) 또는 새 `ai-core/tests/test_subjects.py`

**Interfaces:**
- Produces:
  - `CONTENT_DIR = Path(...)/'content'`
  - `SUBJECT_ORDER = ('dsa', 'os')`: 과목 폴더 이름. 존재하는 폴더만 읽는다.
  - `Concept.subject: str | None = None`: 지도 파일에는 쓰지 않고, 로더가 채운다. 파일에 적혀 있으면 거부한다.
  - `ConceptMap.subjects: list[Subject] = []`, `Subject(id: str, title: str)`
  - `load_concept_map(path: Path | None = None) -> ConceptMap`
    - `path`가 None이면 `CONTENT_DIR/<과목>/concept-map.json`을 모두 읽어 합친다.
    - 합친 지도의 값: `id='cs'`, `title='CS 전공'`, `version`은 각 과목 version의 합, `status`는 모두 reviewed일 때만 reviewed.
    - 경로를 주면 기존처럼 지도 하나만 읽는다(테스트 호환).
  - `load_item_bank(directory: Path | None = None, concept_map=None)`
    - None이면 모든 과목 폴더의 `items-*.jsonl`을 읽는다.
  - `/concept-map` 응답: 기존 필드 + `subjects: [{id, title}]` + 개념별 `subject`
- Consumes: 없음

- [ ] Step 1: 실패하는 테스트를 쓴다.
  - 임시 폴더에 dsa(2개념), os(1개념, dsa 개념을 선수로 가짐) 지도를 만든다. 합친 지도에서 os 개념의 `subject=='os'`이고 선수 ID가 dsa 개념을 가리키는지 확인한다.
  - 다른 과목을 거쳐 생긴 간접 관계를 거부하는지 확인한다(Review Focus 1).
  - 파일에 `subject`가 적혀 있으면 거부하는지 확인한다.
  - 과목 사이 개념 ID가 중복되면 거부하는지 확인한다.
- [ ] Step 2: `cd ai-core && .venv/bin/python -m unittest discover -s tests`로 실행한다. 새 테스트가 실패해야 한다.
- [ ] Step 3: 구현한다. 지도 하나를 검증하는 규칙(고립 개념 금지 등)은 합친 지도에 적용한다. 과목 지도 하나만으로는 고립처럼 보여도, 합친 뒤 이어지면 허용한다.
- [ ] Step 4: 전체 AI core 테스트를 통과시킨다. 실제 저장소 지도(dsa만)로 `load_concept_map()`이 개념 36개, `subject='dsa'`를 돌려주는지 확인한다.
- [ ] Step 5: 커밋한다. `feat(ai-core): 여러 과목 개념 지도·문항을 하나의 연결 지도로 읽기`

### Task 2: 간트 차트 애니메이션 (AI core, 코드 PR)

**Files:**
- Create: `ai-core/serving/animation/scheduling.py`
- Modify: `ai-core/serving/animation/model.py`(요청 모델 `CpuScheduleRequest` 추가)
- Modify: `ai-core/serving/animation/__init__.py`(SIMULATORS, SCENE_FORMATS, CONCEPT_SCENES, CONCEPT_SCENE_LIMITS)
- Test: `ai-core/tests/test_animation_scheduling.py`

**Interfaces:**
- Produces:
  - 요청: `CpuScheduleRequest(kind='cpu_schedule', algorithm: Literal['fcfs','sjf','rr','priority'], processes: list[Proc] (1~5개), quantum: int | None (rr일 때만 1~4))`
  - `Proc(name: 'P1'~'P5', arrival: 0~9, burst: 1~6, priority: 1~5 | None)`
    - priority는 algorithm이 priority일 때만 필수이고, 숫자가 작을수록 우선이다.
    - 모든 burst의 합과 유휴 시간을 더한 총 길이가 12 이하여야 한다(칸 폭). 넘으면 검증 오류를 낸다.
  - `simulate_cpu_schedule(request) -> list[Frame]`
    - 비선점 sjf·priority와 rr만 다룬다. 선점 SRTF는 범위 밖이다.
    - 화면 배치:
      - y=0: 시간 눈금(label)
      - y=1: 간트 칸(cell, 프로세스 이름 또는 "-")
      - y=3: "준비 큐" label, y=4: 준비 큐 slot(큐 순서 또는 우선순위 순서)
      - y=6: 프로세스별 대기 시간 label
    - 매 시간 단위 또는 사건마다 frame을 하나 만든다. 마지막 frame 설명에 평균 대기 시간을 넣는다.
    - frame이 MAX_FRAMES=16을 넘지 않게 사건 단위로 묶는다.
  - CONCEPT_SCENES:
    - `fcfs`, `sjf`, `round-robin`, `priority-scheduling`, `scheduling-metrics` → `('cpu_schedule',)`
    - LIMITS: 개념에 맞는 algorithm만 허용한다. 예: `round-robin` → rr, `scheduling-metrics` → fcfs·sjf.
- Consumes: Task 3의 개념 ID(이름만, 순서와 무관)

- [ ] Step 1: 실패하는 테스트를 쓴다(교과서 예제, 기대값은 손으로 계산해 테스트에 적음).
  - FCFS: P1(0,5) P2(1,3) P3(2,1) → 간트 `P1×5 P2×3 P3×1`, 대기 0,4,6, 평균 3.33
  - SJF 비선점: 같은 입력 → `P1×5 P3×1 P2×3`, 대기 0,5,3, 평균 2.67
  - RR q=2: P1(0,5) P2(0,3) P3(0,1) → `P1 P1 P2 P2 P3 P1 P1 P2 P1`, 대기 P1=4, P2=5, P3=4
  - Priority: P1(0,3,p2) P2(0,2,p1) → `P2×2 P1×3`
  - 유휴 구간: P1(2,2) → 칸 0·1이 "-"
  - 동점: 도착 시각이 같으면 이름 순으로 처리한다. RR에서 새 도착과 할당량이 끝난 프로세스가 같은 시각이면 새 도착이 먼저 큐에 들어간다.
  - 검증 오류: 총 길이 13, rr인데 quantum 없음, priority인데 priority 없음
- [ ] Step 2: 실패를 확인한다.
- [ ] Step 3: 구현한다. 기존 `linear.py`·`sorting.py`의 frame 작성 방식을 따른다. 설명문은 한국어이고 80자 이내다.
- [ ] Step 4: 전체 AI core 테스트를 통과시킨다. `create_animation`으로 JSON을 넣어 Animation이 만들어지는지 확인한다.
- [ ] Step 5: 커밋한다. `feat(ai-core): CPU 스케줄링 간트 차트 애니메이션 추가`

### Task 3: 운영체제 개념 지도·문항 (데이터 PR, Task 1 이후 병합)

**Files:**
- Create: `ai-core/content/os/concept-map.json`
- Create: `ai-core/content/os/items-scheduling.jsonl`

**Interfaces:**
- Produces: 개념 6개(ID 고정)

| ID | 제목 | 직접 선수 개념 |
| --- | --- | --- |
| `process-state` | 프로세스와 상태 | `queue`(준비 큐가 큐임) |
| `scheduling-metrics` | 스케줄링 지표(대기·반환·응답 시간) | `process-state` |
| `fcfs` | FCFS 스케줄링 | `scheduling-metrics` |
| `round-robin` | 라운드 로빈 | `fcfs` |
| `priority-scheduling` | 우선순위 스케줄링 | `fcfs`, `priority-queue` |
| `sjf` | SJF 스케줄링 | `priority-scheduling` |

  - 간접 관계 금지 규칙을 지킨다. 예: `round-robin` → `queue`는 `fcfs` → … → `process-state` → `queue`로 이미 이어지므로 직접 두지 않는다.
  - 개념마다 학습 목표 1~3개를 둔다.
  - 문항은 개념마다 진단 2(d), 확인 2(c), 서술 1(e) + 루브릭 2~4개로, 모두 30문항이다.
  - 계산 문항(대기 시간 등)의 정답은 Task 2 시뮬레이터 규칙(동점 처리 포함)과 일치해야 한다.
- Consumes: Task 1 로더

- [ ] Step 1: 지도와 문항을 쓴다. 출처는 직접 작성한다. CS-300·KMMLU 원문은 쓰지 않는다.
- [ ] Step 2: 실제 로더로 검증한다. `load_concept_map()` 개념 42개, `load_item_bank()` 문항 210개, 테스트 전체 통과.
- [ ] Step 3: 검수자가 계산 문항의 정답을 독립적으로 다시 계산하고, CS-300과 겹치지 않는지 확인한다(`ai-core/eval/cs300_overlap.py`).
- [ ] Step 4: 커밋한다. `add(ai-core): 운영체제 CPU 스케줄링 개념 지도와 문항 추가`

### Task 4: 과목 표시 (Frontend, 코드 PR, Task 1 이후)

**Files:**
- Modify: `frontend/src/roadmap.ts`(Concept에 `subject`, 과목 목록. `domain`은 과목 이름)
- Modify: `frontend/src/Roadmap.tsx`(과목 구분 표시: 범례 또는 개념 카드 표식, 과목 필터)
- Modify: 관련 테스트 파일

**Interfaces:**
- Consumes: Task 1 `/concept-map` 응답의 `subjects`, 개념별 `subject`
- Produces:
  - 로드맵에서 운영체제 개념이 자료구조 개념 아래 단계에 보이고, 과목이 시각적으로 구분된다.
  - 학습 화면 영역 표시가 개념의 과목 이름이 된다.

- [ ] Step 1: 실패하는 테스트를 쓴다.
  - `subject`가 없는 응답(구버전)도 dsa로 처리한다.
  - 과목 필터가 개념을 거르면서도 선수 관계가 끊긴 표시가 남지 않는다.
  - 기존 저장 키 기록을 그대로 읽는다.
- [ ] Step 2: `design-taste-frontend` 스킬로 과목 표식 디자인을 정하고 구현한다.
- [ ] Step 3: `cd frontend && npm test && npm run build`를 통과시킨다. 실제 화면(5173이 아닌 시험 포트 20000 이상)에서 확인한다.
- [ ] Step 4: 커밋한다. `feat(frontend): 개념 지도에 과목 구분 표시`

### Task 5: 과목 경계를 넘는 막힘 흐름 검증과 측정 (통합)

**Files:**
- Modify: `ai-core/eval/sim_learner/run.py`(과목 선택 인자 `--subject`, 기본은 전체)
- Modify: `ai-core/eval/sim_learner/run.py` `_session_metrics`(놓친 빈틈 분류에 "찍기로 가지치기" 추가, 2026-10-26 분석 근거)
- Test: 해당 테스트
- 문서: `README.md` 또는 현재 기능 문서의 과목 목록 갱신

- [ ] Step 1: 시뮬레이션 측정 분류 보완은 TDD로 한다. `not_probed_budget`과 `not_probed_guessed`를 분리한다.
- [ ] Step 2: 운영 반영 후 E2E를 확인한다(Review Focus 5).
  - `sjf`에서 [막혔어요]를 누르고 `priority-queue`를 모르는 학생으로 답한다.
  - 진단 → 결과(`priority-queue` 빈틈) → 보충(힙 애니메이션) → 확인 → 복귀(`sjf`) 순서로 진행되는지 본다.
- [ ] Step 3: 시뮬레이션 학습자를 운영체제 개념에 돌려 M1 recall·precision을 따로 보고한다(GPU1, 포트 20900).
- [ ] Step 4: 결과를 HANDOFF.md와 발표 자료 수치에 반영한다.

## 순서와 병렬

- Task 1과 Task 2는 병렬로 진행한다(서로 다른 파일).
- Task 3은 Task 1 브랜치 위에서 작성·검증하고, Task 1 병합 뒤에 병합한다.
- Task 4는 Task 1 병합 뒤에 시작한다.
- Task 5는 1~4 병합과 운영 반영 뒤에 진행한다.
- 운영 반영 순서: AI core 재시작(8001) → 5173 vite 메인 체크아웃 pull. Spring은 변경이 없으면 재시작하지 않는다.
