# MVP 2: 자료구조 콘텐츠 + 단계별 애니메이션 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> 이 저장소에서는 Orca 코디네이터가 Task를 워커에게 나눠 맡긴다(설계 7절). 워커는 `orca-superpowers` 워커 편을 따른다.

**Goal:** 자료구조 36개 개념 전체에서 막힘 흐름이 끝까지 동작하고, 보충(S4)과 채팅에서 코드가 계산한 단계별 애니메이션을 보여 준다.

**Architecture:**
- AI core에 애니메이션 모듈을 새로 둔다.
  - 모델은 장면 종류와 작은 입력값(연산 목록)만 고른다.
  - 코드가 그 연산을 실제로 실행해 프레임(상자·화살표·한 줄 설명)을 만든다.
- 결과는 기존 도식과 같은 `artifact` 이벤트로 흘러간다.
  - Spring 채팅 저장은 artifact 이벤트를 그대로 보관하므로 바꾸지 않는다.
  - 막힘 세션은 `view.animation`에 담긴다.
- 프런트엔드는 프레임을 SVG로 그린다.
  - 상자 ID가 같으면 CSS transition으로 위치를 옮겨 움직임을 보여 준다.
  - 새 라이브러리는 쓰지 않는다.
- 문항은 5개 개념씩 데이터 PR 5개로 나눠 올린다. 단답 정답은 PR마다 붙는 테스트가 코드로 다시 계산해 대조한다.

**Tech Stack:** FastAPI·Pydantic v2(AI core), LangGraph 막힘 엔진(기존), Spring Boot(변경 없음 확인만), React 19 + TypeScript + SVG, node:test, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-26-completion-roadmap-design.md` (3-3절 MVP 2, 2-1절 애니메이션, 2-4절 한국어 답)

## Global Constraints

- 브랜치는 `<유형>/<작업명>`이다. main에서 직접 커밋하지 않는다. 커밋·PR 제목은 `<type>(<scope>): <한국어 설명>` 형식이고, `Co-Authored-By`나 생성 도구 문구를 붙이지 않는다.
- PR 하나는 목적 하나다. 변경 파일은 4~5개 이내를 목표로 한다. 템플릿은 `.github/pr-templates.md`를 따른다.
- 주석은 AGENTS.md 7절을 따른다(한국어 개조식, 파일 상단 역할, 제한값의 의미).
- 데이터 PR(문항 은행·루브릭)은 사용자가 직접 검수하고 승인해야 병합한다. 코드·문서 PR은 CI 통과와 검수자 리뷰 뒤 병합한다(CLAUDE.md 병합 위임).
- 새 라이브러리는 추가하지 않는다. 필요하면 코디네이터에게 묻는다.
- CS-300 문항·정답은 Git에 넣지 않는다. 학생 답 원문, 측정 결과 원본은 Git 밖(`ai-core/outputs/`, git-ignored)에 둔다.
- 워커 시험 서버 포트는 20000 이상을 쓴다. AI core 21000~21019, Frontend 23000~23019, 검수자 24000~24019.
- 운영 중인 8000(vLLM), 8001, 8080, 5173은 건드리지 않는다. 실모델 측정 스크립트(Task 10)만 8000에 요청을 하나씩 보낸다.
- 애니메이션의 상태는 모델이 쓰지 않는다. 모델 출력은 장면 종류와 연산 입력뿐이다(설계 2-1).
- 검증 명령
  - AI core: `cd ai-core && .venv/bin/python -m unittest discover -s tests`
  - 프런트엔드: `cd frontend && npm test && npm run build`
  - 백엔드: `cd backend && ./gradlew test bootJar`
  - 새 worktree에는 `.venv`가 없다. 메인 체크아웃의 `/home/infoserver/cs-learning-agent/ai-core/.venv/bin/python`을 읽기 전용으로 쓴다.

## Review Focus

1. **모델이 실행할 수 없는 연산을 고른 경우:** 빈 스택 pop, 없는 키 삭제, 범위 밖 인덱스 같은 경우다.
   - 채팅에서는 도구 오류로 돌려보내 모델이 고쳐 다시 부를 수 있어야 한다.
   - 막힘 보충에서는 애니메이션 없이 텍스트만 보여 줘야 한다. 503으로 흐름을 멈추면 안 된다. (Task 1·4 테스트)
2. **애니메이션 필드가 없던 시절에 저장된 막힘 세션:** 새로고침으로 복구하면 `view.animation`이 없다. 화면은 그대로 텍스트만 보여 줘야 한다. (Task 5 테스트)
3. **채팅에서 애니메이션 artifact를 다시 선택한 경우:** `active_artifact`로 돌아와도 에이전트가 `diagram` 키를 찾다 죽으면 안 된다. (Task 3 테스트)
4. **원소 8개 배열이나 노드 9개 BST처럼 큰 장면:** 휴대폰 폭(375px)에서 가로 스크롤 없이 축소되어 보여야 한다. (Task 5 테스트)
5. **붙여넣기(E2)로 특정된 개념:** 지원 목록과 실제 엔진의 판단이 같아야 한다. 화면은 지원한다고 보여 주는데 엔진은 문항이 없어 보충을 건너뛰는 일이 없어야 한다. (Task 6 테스트)

## 파일 구조

| 파일 | 책임 | Task |
| --- | --- | --- |
| `ai-core/serving/animation/model.py` (신규) | 프레임(Box·Arrow·Frame·Animation)과 장면 요청(종류별 입력) 스키마, 제한값 | 1 |
| `ai-core/serving/animation/linear.py` (신규) | 배열·연결 리스트·스택·큐 시뮬레이터 | 1 |
| `ai-core/serving/animation/hierarchical.py` (신규) | 해시 테이블·BST·힙 시뮬레이터 | 2 |
| `ai-core/serving/animation/__init__.py` (신규) | `simulate()`, `create_animation()`, 도구 정의, 개념별 허용 장면 목록 | 1·2·4 |
| `ai-core/serving/agent.py`, `ai-core/serving/context.py` | 채팅 도구 목록에 `show_animation` 추가 | 3 |
| `ai-core/serving/stuck.py`, `ai-core/serving/stuck_llm.py` | S4에서 장면 선택 LLM 호출과 `view.animation` | 4 |
| `ai-core/serving/api.py` | `/concept-map` 응답에 `stuck_supported` 추가 | 6 |
| `frontend/src/animation.ts`, `frontend/src/AnimationPlayer.tsx`, `frontend/src/animation.css` (신규) | 프레임 타입, 화면 맞춤 계산, 재생기 | 5 |
| `frontend/src/StuckPanel.tsx`, `frontend/src/ChatSession.tsx` | 재생기 연결 | 5·7 |
| `frontend/src/stuck.ts`, `frontend/src/Roadmap.tsx`(또는 지원 목록을 쓰는 곳) | 하드코딩 지원 목록 제거 | 6 |
| `ai-core/content/dsa/items-<묶음>.jsonl` (신규 5개) | 25개 개념 문항 | 8 |
| `ai-core/tests/test_item_answers_<묶음>.py` (신규 5개) | 단답 정답 독립 계산 대조 | 8 |
| `ai-core/eval/cs300_overlap.py` (신규) | 문항 은행과 로컬 CS-300의 유사 문항 검사(로컬 전용) | 8 |
| `ai-core/tests/test_item_coverage.py` (신규) | 36개 개념 모두 문항 구성이 갖춰졌는지 | 9 |
| `ai-core/eval/answer_sheet.py`, `ai-core/eval/animation_choice.py` (신규) | 한국어 답 수집 시트 내보내기, 장면 선택 성공률 측정 | 10 |

---

### Task 1: 애니메이션 프레임 모델과 선형 구조 시뮬레이터

**Files:**
- Create: `ai-core/serving/animation/__init__.py`, `ai-core/serving/animation/model.py`, `ai-core/serving/animation/linear.py`
- Test: `ai-core/tests/test_animation_linear.py`

**Interfaces:**
- Produces:
  - `Box(id: str, label: str ≤16, x: int 0..11, y: int 0..7, shape: Literal['cell','node','slot','label'], tone: Literal['normal','active','new','removed']='normal')`
  - `Arrow(source: str, target: str, label: str | None ≤8)`
  - `Frame(boxes: list[Box] ≤40, arrows: list[Arrow] ≤40, caption: str 1..80)`
  - `Animation(kind: str, title: str 1..60, frames: list[Frame] 1..MAX_FRAMES)`, `MAX_FRAMES = 16`
  - 장면 요청: `kind`로 구분하는 discriminated union `AnimationRequest`
    - `ArrayRequest(kind='array', values: list[int] 1..8, ops: list[ArrayOp] ≤6)`. `ArrayOp`는 `{op:'access'|'delete', index}` 또는 `{op:'insert', index, value}`.
    - `LinkedListRequest(kind='linked_list', values: list[int] 0..6, ops ≤6)`. 연산은 `insert_head value`, `insert_after target value`, `delete value`.
    - `StackRequest(kind='stack', ops ≤10)`. 연산은 `push value`, `pop`.
    - `QueueRequest(kind='queue', capacity: int 2..8, circular: bool, ops ≤10)`. 연산은 `enqueue value`, `dequeue`.
    - 값은 모두 정수 -99..999다.
  - `simulate(request: AnimationRequest) -> Animation`. 실행할 수 없는 연산이면 한국어 `ValueError`를 낸다.
  - `create_animation(arguments: str) -> dict`
    - 성공: `{'ok': True, 'artifact_id': <uuid hex>, 'kind': 'animation', 'animation': Animation dump, 'request': request dump}`
    - 검증 실패: `ValueError`
- 프레임 규칙
  - 첫 프레임은 초기 상태다. 연산 하나마다 프레임을 하나 추가한다(`len(frames) == len(ops) + 1`).
  - 같은 원소는 프레임이 바뀌어도 같은 `Box.id`를 유지한다. 프런트엔드가 이 ID로 이동을 그린다.
  - caption은 코드 템플릿으로 쓴다. 예: `"push 5: 5를 top 위에 올립니다"`, `"dequeue: front의 3을 꺼냅니다"`.
  - 제한값은 교육적 분량 제한이다. 한 화면에서 따라갈 수 있는 원소·단계 수를 뜻하며, 주석에 그 의미를 적는다.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_animation_linear.py`)
  - `test_stack_frames_match_list_reference`: ops `push 1, push 2, pop, push 3`. 프레임 수 5. 마지막 프레임의 cell 라벨(아래→위)이 `['1','3']`이다. pop 프레임에서 꺼낸 상자 tone이 `removed`다.
  - `test_stack_pop_empty_rejected`: `pop` 하나만 있으면 `simulate`가 `ValueError`를 내고 메시지에 `"비어"`가 들어 있다.
  - `test_array_insert_shifts_and_keeps_ids`: values `[4,7,9]`, ops `insert 1 5`. 마지막 라벨이 `['4','5','7','9']`다. 원래 7·9의 Box.id가 첫 프레임과 같다.
  - `test_array_index_out_of_range_rejected`: `delete 3`(길이 3)이면 `ValueError`.
  - `test_linked_list_delete_rewires_arrow`: values `[1,2,3]`, `delete 2`. 마지막 프레임에 `1→3` 화살표가 있고 `2`로 가는 화살표가 없다.
  - `test_circular_queue_wraps`: capacity 3, circular, ops `enqueue 1,2,3, dequeue, enqueue 4`. 마지막 프레임에서 값 4가 slot 0에 있다. front·rear 라벨 상자가 각각 slot 1·0을 가리킨다.
  - `test_queue_full_rejected`: capacity 2, enqueue 3번이면 `ValueError`.
  - `test_frame_limit`: ops 개수가 상한을 넘는 요청은 Pydantic 검증 단계에서 거부된다.
  - `test_create_animation_returns_artifact`: JSON 문자열 입력으로 `ok`, `kind=='animation'`, uuid 32자리 hex를 확인한다.
- [ ] **Step 2: 실패 확인**
  - Run: `cd ai-core && .venv/bin/python -m unittest tests.test_animation_linear -v`
  - Expected: `ModuleNotFoundError: serving.animation`
- [ ] **Step 3: 구현**
  - `model.py`: 위 스키마. `extra='forbid'`를 쓴다.
  - `linear.py`: 종류별 `simulate_<kind>(req) -> Animation`. 실제 파이썬 리스트·deque로 상태를 바꾸면서 매 연산 뒤 레이아웃을 계산한다.
  - `__init__.py`: `simulate`, `create_animation`을 둔다.
- [ ] **Step 4: 통과 확인**
  - Run: 위 명령 + 전체 AI core 테스트
  - Expected: 모두 PASS
- [ ] **Step 5: 커밋**
  - `feat(ai-core): 애니메이션 프레임 모델과 선형 구조 시뮬레이터 추가`
  - PR: `feature/animation-engine`

### Task 2: 해시 테이블·BST·힙 시뮬레이터

**Files:**
- Create: `ai-core/serving/animation/hierarchical.py`
- Modify: `ai-core/serving/animation/model.py`(요청 3종 추가), `ai-core/serving/animation/__init__.py`(분기 추가)
- Test: `ai-core/tests/test_animation_hierarchical.py`

**Interfaces:**
- Consumes: Task 1의 `Box/Arrow/Frame/Animation`, `simulate`
- Produces:
  - `HashTableRequest(kind='hash_table', buckets: int 3..11, ops ≤8)`
    - 연산은 `insert key`, `search key`.
    - 해시 함수는 `h(k) = k mod buckets`, 충돌은 체이닝이다.
    - caption에 `"h(17) = 17 mod 5 = 2"`처럼 계산식을 넣는다.
  - `BSTRequest(kind='bst', ops ≤10)`
    - 연산은 `insert value`, `delete value`.
    - 삭제는 자식 2개일 때 중위 후속자로 대체한다.
    - 중복 insert는 `ValueError`다. 노드는 최대 9개다.
  - `HeapRequest(kind='heap', order: Literal['min','max'], ops ≤10)`
    - 연산은 `insert value`, `extract`.
    - 빈 힙에서 extract하면 `ValueError`다.
    - sift-up과 sift-down의 교환 한 번마다 프레임을 하나씩 만든다. 이 경우만 `len(frames) > len(ops)+1`을 허용하되 `MAX_FRAMES` 안이어야 한다.
    - 트리 모양과 배열 모양을 함께 그린다.

- [ ] **Step 1: 실패하는 테스트**
  - `test_hash_chaining_matches_dict_reference`: buckets 5, insert `12,7,17,3`. 마지막 프레임의 버킷별 체인이 `{2:[12,7,17], 3:[3]}`과 같다. caption 하나에 `"17 mod 5 = 2"`가 있다.
  - `test_bst_inorder_sorted`: insert `8,3,10,1,6`, delete `3`. 마지막 프레임 노드 라벨의 중위 순회가 `[1,6,8,10]`이다. `8`의 왼쪽 자식이 `6`이다(후속자 대체).
  - `test_bst_duplicate_rejected`
  - `test_min_heap_matches_heapq`: insert `5,3,8,1`, extract. 마지막 배열이 `heapq`로 같은 연산을 한 결과와 같다.
  - `test_heap_swap_frames_within_limit`: 상한을 넘는 연산열은 `ValueError`(분량 초과)다.
  - `test_extract_empty_rejected`
- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m unittest tests.test_animation_hierarchical -v` → FAIL(ImportError 또는 kind 검증 실패)
- [ ] **Step 3: 구현** — 시뮬레이터 3종과 레이아웃
  - BST·힙 트리는 깊이를 y로, 중위 순서를 x로 둔다.
  - 힙 배열은 y=트리 깊이+2 줄에 cell로 둔다.
- [ ] **Step 4: 통과 확인** — 위 명령 + 전체 AI core 테스트 → PASS
- [ ] **Step 5: 커밋** — `feat(ai-core): 해시 테이블·BST·힙 애니메이션 시뮬레이터 추가`(같은 브랜치에 이어서 커밋하거나 별도 PR `feature/animation-hierarchical`)

### Task 3: 채팅 도구 `show_animation`

**Files:**
- Modify: `ai-core/serving/animation/__init__.py`(도구 정의 `ANIMATION_TOOL`), `ai-core/serving/agent.py`, `ai-core/serving/context.py`
- Test: `ai-core/tests/test_agent.py`(사례 추가)

**Interfaces:**
- Consumes: `create_animation`, `serving.tools.diagram.inline_schema`
- Produces:
  - `ANIMATION_TOOL = {'type':'function','function':{'name':'show_animation', ...,'parameters': inline_schema(AnimationRequestModel.model_json_schema())}}`
  - `serving.agent.TOOLS = [TOOL, ANIMATION_TOOL]`. `context.token_counter`도 같은 `TOOLS`를 쓴다.
- 동작
  - 도구 성공 시 기존과 같은 `{'type':'artifact', **result}` 이벤트를 낸다. result에 `kind:'animation'`이 들어 있다.
  - 활성 artifact가 애니메이션이면 질문에 `{'artifact_id', 'animation_request'}`를 붙인다. 도식이면 지금처럼 `diagram`을 붙인다.
  - SYSTEM 프롬프트에 한 줄을 더한다: "자료구조 연산 과정을 단계별로 보여 달라고 하면 show_animation을 사용하세요."

- [ ] **Step 1: 실패하는 테스트**(기존 fake client 패턴을 따른다)
  - `test_show_animation_tool_emits_artifact`: 가짜 모델이 `show_animation`을 stack 요청으로 호출한다. 이벤트에 `type=='artifact'`, `kind=='animation'`, frames 개수가 있다.
  - `test_show_animation_invalid_returns_tool_error`: pop만 있는 요청이면 `tool_error` 이벤트가 나고, 다음 턴에 모델이 받은 tool 메시지에 `"ok": false`가 있다.
  - `test_active_animation_artifact_does_not_require_diagram`(Review Focus 3): `active_artifact={'artifact_id':..., 'kind':'animation', 'request':{...}}`로 `run_agent`를 호출해도 예외가 나지 않고, 질문에 `animation_request`가 들어간다.
  - `test_token_counter_includes_both_tools`
- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m unittest tests.test_agent -v` → 새 사례 FAIL
- [ ] **Step 3: 구현** — 도구 이름으로 분기한다. `Unknown tool` 처리와 호출 제한은 그대로 둔다.
- [ ] **Step 4: 통과 확인** — 전체 AI core 테스트 PASS
- [ ] **Step 5: 커밋** — `feat(ai-core): 채팅에 단계별 애니메이션 도구 추가`, PR `feature/chat-animation-tool`

### Task 4: 막힘 보충(S4)에서 장면 선택

**Files:**
- Modify: `ai-core/serving/animation/__init__.py`(`CONCEPT_SCENES`), `ai-core/serving/stuck.py`, `ai-core/serving/stuck_llm.py`
- Test: `ai-core/tests/test_stuck_flow.py`, `ai-core/tests/test_stuck_llm.py`(사례 추가)

**Interfaces:**
- Consumes: `simulate`, `AnimationRequest`
- Produces:
  - `CONCEPT_SCENES: dict[str, tuple[str, ...]]`(개념 ID → 허용 장면 종류)
    - array·dynamic-array·memory-address → `array`
    - reference·linked-list → `linked_list`
    - stack → `stack`
    - queue → `queue`
    - hash-function·hash-table → `hash_table`
    - binary-tree·bst·tree-traversal → `bst`
    - heap·priority-queue·heap-sort → `heap`
    - 목록에 없는 개념은 애니메이션을 붙이지 않는다.
  - `Gateway.choose_animation(concept: Concept, entry: Entry, kinds: tuple[str, ...]) -> AnimationRequest | None`
    - 새 역할 `'animate'`의 설정: temperature 0, 비추론, json_schema 제한 출력. 스키마는 `kinds`에 든 요청 타입만 허용한다.
    - 입력은 개념 설명·학습 목표·막힌 맥락이다. 문항·정답은 넘기지 않는다.
    - 알맞은 장면이 없으면 `null`을 고를 수 있다.
  - `_remediate` 동작
    - 개념이 `CONCEPT_SCENES`에 있으면 `explain` 뒤에 `choose_animation`을 부르고 `simulate`한다.
    - 성공하면 `s.view['animation'] = {'kind':..., 'title':..., 'frames': [...]}`로 둔다.
    - 실패하면 `animation`을 두지 않고 `_log(..., 'animation_failed', reason=...)`만 남긴다. 실패 종류는 LLM 오류, 검증 실패, 시뮬레이션 `ValueError`다.
    - 이 실패로는 503을 내지 않는다(Review Focus 1). `explain` 자체의 실패는 기존대로 503이다.
  - 로그 `remediated`에 `animation_kind`(없으면 None)를 남긴다. Task 10 측정에 쓴다.

- [ ] **Step 1: 실패하는 테스트**
  - `test_flow`: FakeGateway에 `choose_animation`을 추가한다.
    - `test_remediation_view_has_animation_for_stack`: stack 개념 막힘에서 보충 화면의 `view['animation']['frames']`가 있다.
    - `test_animation_failure_falls_back_to_text`: FakeGateway가 실행 불가 요청(빈 스택 pop)을 돌려준다. 보충 view에 `text`는 있고 `animation`은 없다. 로그에 `animation_failed`가 있다. 예외가 나지 않는다.
    - `test_concept_without_scene_skips_choose`: logarithm은 `choose_animation`을 부르지 않는다(호출 횟수 0).
  - `test_stuck_llm`
    - `test_choose_animation_schema_limits_kinds`: 가짜 클라이언트로 보낸 `response_format` 스키마가 `kinds`에 든 종류만 허용한다.
    - `test_choose_animation_null`: 모델이 null을 고르면 `None`을 돌려준다.
- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m unittest tests.test_stuck_flow tests.test_stuck_llm -v` → FAIL
- [ ] **Step 3: 구현** — `UnavailableGateway`의 `_fail` 목록에 `choose_animation`을 추가하고, 프롬프트(`PROMPTS['animate']`)를 추가한다.
- [ ] **Step 4: 통과 확인** — 전체 AI core 테스트 PASS
- [ ] **Step 5: 커밋** — `feat(ai-core): 막힘 보충에 개념별 단계 애니메이션 추가`, PR `feature/stuck-animation`
  - PR 본문에 적을 것: 실행 중인 서버 동작이 바뀐다. 보충 단계마다 LLM 호출이 1회 늘어난다. 병합은 검수자가, 운영 재시작은 코디네이터가 MVP 2 끝에 한다.

### Task 5: 프런트엔드 애니메이션 재생기와 막힘 패널 연결

**Files:**
- Create: `frontend/src/animation.ts`, `frontend/src/AnimationPlayer.tsx`, `frontend/src/animation.css`
- Modify: `frontend/src/StuckPanel.tsx`
- Test: `frontend/tests/animation.test.mjs`

**Interfaces:**
- Consumes: AI core 프레임 JSON(Task 1의 `Animation` dump). 막힘 view의 선택 필드 `animation?`
- Produces:
  - `animation.ts`
    - `type AnimationData = { kind: string; title: string; frames: Frame[] }`
    - `fitViewBox(frames: Frame[]): { width: number; height: number }`: 모든 프레임에서 가장 큰 x·y로 고정 크기를 계산한다. 프레임마다 화면이 출렁이지 않게 하기 위해서다.
    - `clampStep(step: number, total: number): number`
  - `AnimationPlayer({ animation }: { animation: AnimationData })`
    - 이전·다음 버튼, `n / 전체` 표시, caption을 둔다.
    - 키보드 ←·→로 넘긴다.
    - SVG `viewBox`와 `width:100%`로 화면 폭에 맞춘다.
    - 상자는 `key=box.id`로 그리고 `transform`에 CSS transition 300ms를 준다. `prefers-reduced-motion`이면 transition을 끈다.
  - StuckPanel `remediation` 화면: `view.animation`이 있으면 설명 아래에 `AnimationPlayer`를 넣는다.

- [ ] **Step 1: 실패하는 테스트**(`tests/animation.test.mjs`, 기존 node:test 방식)
  - `fitViewBox`가 프레임별 최대 좌표의 합집합 크기를 돌려준다.
  - `clampStep(-1,5)===0`, `clampStep(9,5)===4`.
  - `animation` 필드가 없는 막힘 view 타입 값도 그대로 렌더 함수에 넘길 수 있다(Review Focus 2). 렌더 분기 함수 `remediationParts(view)`를 순수 함수로 빼서 `{text, animation: null}`을 확인한다.
- [ ] **Step 2: 실패 확인** — `cd frontend && npm test` → FAIL
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `npm test && npm run build` PASS
- [ ] **Step 5: 브라우저 확인**(Review Focus 4)
  - `frontend/tests/browser/stuck.spec.ts`에 사례를 추가한다. 가짜 API 응답에 원소 8개 배열 애니메이션을 넣고 375px 폭에서 `document.documentElement.scrollWidth <= 375`인지 본다.
  - 다음 버튼을 누르면 caption이 바뀌는지 본다.
  - Run: `npm run test:ui -- stuck.spec.ts` → PASS
- [ ] **Step 6: 커밋** — `feat(frontend): 단계별 애니메이션 재생기와 막힘 보충 연결`, PR `feature/animation-player`

### Task 6: 막힘 지원 개념 목록을 서버에서 받기

**Files:**
- Modify: `ai-core/serving/api.py`, `frontend/src/stuck.ts`, `frontend/src/StuckPanel.tsx`(지원 여부를 받는 곳), `frontend/src/roadmap.ts`(타입)
- Test: `ai-core/tests/test_api.py`, `frontend/tests/stuck.test.mjs`

**Interfaces:**
- Produces:
  - `/concept-map` 응답에 `stuck_supported: list[str]`을 추가한다. 기준은 확인(check) 문항과 서술(explanation) 문항이 모두 있는 개념이며, 엔진이 이해 확인을 할 수 있는 개념이다.
  - Spring `ConceptMapController`는 JSON을 그대로 전달하므로 변경하지 않는다. 병합 전에 8080 시험 실행으로 필드가 전달되는지 확인한다.
  - 프런트엔드는 하드코딩한 `supportedConcepts`를 지우고, 개념 지도 응답의 `stuck_supported`로 판단한다.
  - `isStuckSupported(map, conceptId): boolean`

- [ ] **Step 1: 실패하는 테스트**
  - `test_concept_map_lists_stuck_supported`: 현재 문항 은행 기준으로 11개 개념과 일치한다. 문항이 없는 `recursion`은 포함되지 않는다.
  - `test_stuck_supported_matches_engine`(Review Focus 5): 목록의 모든 개념에 `ctx.items_for(c,'check')`와 `ctx.items_for(c,'explanation')`이 비어 있지 않다.
  - 프런트엔드: `isStuckSupported`가 응답 목록을 따른다.
- [ ] **Step 2: 실패 확인** — AI core 테스트·`npm test` FAIL
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — AI core 전체, `npm test && npm run build` PASS
- [ ] **Step 5: 커밋** — `feat: 막힘 지원 개념 목록을 문항 은행에서 계산`, PR `feature/stuck-supported-from-bank`
  - Task 8 데이터 PR이 병합되면 화면이 자동으로 따라오게 하는 선행 작업이므로 **1주차 안에 병합**한다.

### Task 7: 채팅 화면에서 애니메이션 artifact 표시

**Files:**
- Modify: `frontend/src/ChatSession.tsx`, `frontend/src/api.ts`(Artifact 타입에 `kind?`·`animation?`)
- Test: `frontend/tests/api.test.mjs`

**Interfaces:**
- Consumes: Task 3의 artifact 이벤트, Task 5의 `AnimationPlayer`
- Produces: artifact의 `kind === 'animation'`이면 `DiagramPanel` 대신 `AnimationPlayer`를 보여 준다. 대화 복구 시 저장된 artifact로도 같은 화면이 나온다.

- [ ] **Step 1: 실패하는 테스트** — 스트림 파서가 `kind:'animation'` artifact를 보존하고, SVG 경로가 없어도 오류로 처리하지 않는다.
- [ ] **Step 2: 실패 확인** — `npm test` FAIL
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `npm test && npm run build` PASS
- [ ] **Step 5: 커밋** — `feat(frontend): 채팅에서 애니메이션 결과 표시`, PR `feature/chat-animation-view`

### Task 8: 자료구조 나머지 25개 개념 문항 (데이터 PR 5개)

**Files (묶음마다):**
- Create: `ai-core/content/dsa/items-<묶음>.jsonl`, `ai-core/tests/test_item_answers_<묶음>.py`
- 첫 묶음에만 Create: `ai-core/eval/cs300_overlap.py`

**묶음과 순서**(선수 개념이 앞 묶음에 오도록 했다)

| 묶음 | 파일 | 개념 |
| --- | --- | --- |
| 1 | `items-recursion-hash.jsonl` | recursion, recurrence-relation, binary-search, hash-function, hash-table |
| 2 | `items-tree.jsonl` | tree, binary-tree, tree-traversal, bst, balanced-bst |
| 3 | `items-heap-sort.jsonl` | heap, priority-queue, sorting-basics, merge-sort, quick-sort |
| 4 | `items-graph.jsonl` | heap-sort, graph, graph-representation, bfs, dfs |
| 5 | `items-graph-algo.jsonl` | topological-sort, shortest-path, greedy, mst, dynamic-programming |

**Interfaces:**
- Consumes: `serving.item_bank.Item` 스키마(기존). 개념 지도의 학습 목표 순번.
- Produces
  - 개념마다 diagnosis 2, check 2, explanation 1(루브릭 2~4개)을 둔다. 기존 선형 11개와 같은 구성이다.
  - ID 규칙은 `<개념>-d1`, `-d2`, `-c1`, `-c2`, `-e1`이다.
  - check 문항은 diagnosis와 다른 상황을 쓴다.
  - 단답(`short_exact`) 문항마다 `test_item_answers_<묶음>.py`에 정답을 계산하는 함수를 둔다.
    - 예: 이진 탐색 비교 순서, 해시 위치, 순회 결과, 정렬 중간 상태.
    - 문항의 `answer`와 같은지 단언한다. 정답을 상수로 다시 적는 것은 계산으로 인정하지 않는다.
  - `cs300_overlap.py --cs300 <로컬 경로> --items <디렉터리>`
    - 정규화한 질문 문장의 `difflib.SequenceMatcher` 비율이 0.8 이상인 쌍을 출력한다.
    - CS-300 경로는 인자로만 받고 저장소에 기록하지 않는다. CI에서 돌리지 않는다.

- [ ] **Step 1: 묶음 정답 테스트 작성** — 묶음의 단답 문항 ID별 계산 함수와 단언을 쓴다(먼저 jsonl 없이 실행하면 FAIL).
- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m unittest tests.test_item_answers_<묶음> -v` → FAIL(문항 없음)
- [ ] **Step 3: 문항 작성** — 개념 지도의 description·objectives에 맞춰 쓴다. 근거(rationale)는 400자 이내다.
- [ ] **Step 4: 검증**
  - 전체 AI core 테스트를 돌린다. `load_item_bank` 검증과 정답 테스트가 PASS여야 한다.
  - `cs300_overlap.py`를 돌려 유사 문항 0건(있으면 고쳐 다시)을 확인한다. 결과를 PR 본문에 적는다.
- [ ] **Step 5: 커밋·PR**
  - 커밋: `add(ai-core): <묶음> 개념의 진단·확인 문항 추가`, PR `feature/items-<묶음>`
  - 검수자는 형식·정답 계산·중복을 먼저 본다.
  - **사용자 승인 전에는 병합하지 않는다.**
  - PR 본문에 개념별 문항 표(질문 요약·정답·루브릭)를 넣어 사용자가 내용만 읽으면 되게 한다.
- 일정: 1주차에 묶음 1·2, 2주차 초에 3·4·5를 올린다(설계 3-4: 검수가 몰리지 않게).

### Task 9: 36개 개념 문항 구성 검사

**Files:**
- Create: `ai-core/tests/test_item_coverage.py`

**Interfaces:**
- Consumes: Task 8 다섯 묶음 병합 완료
- Produces
  - 개념 지도의 모든 개념이 diagnosis ≥ 2, check ≥ 2, explanation ≥ 1인지 검사한다. 이후 문항이 빠지면 CI가 막는다.
  - 모든 개념에서 막힘 흐름이 끝까지 가는지 검사한다(설계 MVP 2 완료 기준 "36개 개념 E2E"의 자동 부분).
    - `test_stuck_flow`의 FakeGateway로 각 개념을 로드맵 진입으로 시작한다.
    - 진단 문항은 모두 정답으로, 확인·서술도 통과로 답한다.
    - `stage == 'complete'`에 도달해야 한다.
    - 보충 화면에 `note`(확인 문항 없음)가 한 번도 나오지 않아야 한다.

- [ ] **Step 1: 테스트 작성** — `test_every_concept_has_items`, `test_flow_completes_for_every_concept`
- [ ] **Step 2: 실행** — Task 8 병합 전에는 FAIL(누락 개념 목록 출력), 병합 후 PASS
- [ ] **Step 3: 커밋** — `add(ai-core): 모든 개념의 문항 구성과 흐름 완주 검사 추가`, PR `develop/item-coverage-test`

### Task 10: 측정 도구(한국어 답 시트, 장면 선택 성공률)

**Files:**
- Create: `ai-core/eval/answer_sheet.py`, `ai-core/eval/animation_choice.py`
- Test: `ai-core/tests/test_eval_tools.py`

**Interfaces:**
- `answer_sheet.py --out ai-core/outputs/answers/<날짜>.csv [--concepts ...]`
  - 열: `answer_id`(무작위 8자리), `item_id`, `concept`, `question`, `phase`(before/after), `author`(빈칸), `answer`(빈칸).
  - 루브릭과 모범 근거는 넣지 않는다. 답 쓰는 사람이 채점 기준을 보지 않게 하기 위해서다.
  - `--out`이 `ai-core/outputs/` 밖이면 거부한다. 원문이 Git에 들어가지 않게 하기 위해서다.
  - **1주차 첫날에 먼저 만든다.** 사용자의 답 수집을 바로 시작할 수 있게 하기 위해서다(설계 3-4).
- `animation_choice.py --base-url http://127.0.0.1:8000/v1 --runs 3 --out ai-core/outputs/eval/animation-choice-<날짜>.json`
  - `CONCEPT_SCENES`의 개념마다 로드맵 진입 1개와 붙여넣기 진입 1개를 만들어 `StuckLLMGateway.choose_animation` → `simulate`를 실행한다.
  - 결과 분류: 성공, null 선택, 스키마 실패, 시뮬레이션 실패.
  - 요청을 하나씩 보낸다(`--max-num-seqs 1` 운영 서버와 공유).
- 사용자 직접 측정 시점: Task 4 병합 뒤 1회 실행해 성공률을 2주차 발표에 쓴다.

- [ ] **Step 1: 실패하는 테스트**
  - `answer_sheet`가 rubric·rationale을 출력하지 않는다.
  - outputs 밖 경로를 거부한다.
  - `animation_choice` 집계 함수 `summarize(results) -> dict`가 네 분류의 개수와 성공률을 낸다.
- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m unittest tests.test_eval_tools -v` FAIL
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — 전체 AI core 테스트 PASS
- [ ] **Step 5: 커밋** — `add(ai-core): 한국어 답 시트와 장면 선택 측정 도구 추가`, PR `feature/eval-tools-mvp2`

### Task 11: MVP 2 마무리 검증과 운영 반영 (코디네이터)

- [ ] **Step 1: 전체 검증** — `/verify-all`(AI core, 백엔드, 프런트엔드, 공백)
- [ ] **Step 2: 실제 스택 E2E** — 워커 포트에서 실제 vLLM(8000)으로 아래를 끝까지 진행한다.
  - 새 개념 3개(hash-table, bst, dfs)의 [막혔어요] 흐름
  - 애니메이션이 붙는 개념(stack, heap) 보충 화면
  - Playwright 스크린샷을 PR이나 보고에 첨부한다.
- [ ] **Step 3: 측정** — `animation_choice.py` 3회 실행, 성공률 기록
- [ ] **Step 4: 운영 재시작** — 8001·8080·5173 재시작(코디네이터). README "구현 상태" 갱신은 문서 PR로 한다.
- [ ] **Step 5: 완료 기준 대조**(설계 3-3 MVP 2)
  - 36개 개념 E2E
  - 애니메이션 상태가 참조 구현과 일치(Task 1·2 테스트)
  - 장면 선택 성공률
  - 한국어 답 수집 시작 여부(사용자 확인)

## 실행 배치 (Orca)

| 워커 | Task | 비고 |
| --- | --- | --- |
| AI core | 1 → 2 → 3, 4 → 10의 측정 부분 | 3과 4는 같은 파일(`animation/__init__.py`)을 건드리므로 순서대로 |
| Frontend | 6(프런트 부분) → 5 → 7 | 5는 Task 1의 프레임 JSON 예시만 있으면 시작 가능 |
| 콘텐츠 | 10의 `answer_sheet`(첫날) → 8 묶음 1~5 → 9 | 문항 작성은 Claude가 한다(서빙 모델 아님) |
| 검수자 | 모든 PR 리뷰. 코드·문서 PR 병합, 데이터 PR은 리뷰만 | 데이터 PR 병합은 사용자 승인 뒤 |
| 코디네이터 | 6의 AI core 부분 병합 조율, 11 | |

- 주별 발표
  - 1주차: Task 1·2 결과를 테스트 장면으로 시연한다(재생기 Task 5 포함).
  - 2주차: 36개 개념 흐름과 애니메이션이 붙은 보충을 보여 준다.
