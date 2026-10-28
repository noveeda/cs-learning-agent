# 하네스·RAG 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> 이 저장소에서는 Orca 코디네이터가 Task를 워커에게 나눠 맡긴다. 워커는 `orca-superpowers` 워커 편을 따른다.

**Goal:** 공개 교재 근거 검색과 문장별 근거 검사 하네스를 설정 스위치 뒤에 만들고, gemma 4 작은 모델들과 26B를 같은 하네스로 측정한다.

**Architecture:**
- ai-core에 `rag/` 패키지를 새로 둔다. 하는 일은 교재 수집, 절 분해, 색인, 검색이다.
  - 교재 원문, 색인 DB, 그림 파일은 Git 밖 `ai-core/data/rag/`에 둔다.
  - 저장소에는 수집·색인 코드와 절 대응표(`ai-core/content/rag/section-map.json`)만 둔다.
- 하네스는 다섯 단계로 나눈다: 범위 결정 → 검색 → 근거 생성 → 문장 판정 → 수정.
  - 순수 로직(검증, 조립, 흐름)은 `serving/harness.py`에 둔다.
  - 모델 호출은 `serving/harness_llm.py`의 `HarnessGateway(StuckModelGateway)`가 맡는다. 기존 `_call` 재시도·잠금·스키마 방식을 그대로 쓰고, 역할은 `stuck_llm.py`의 `ROLE_SETTINGS`·`PROMPTS`에 추가한다.
- 채팅 출력은 기존 NDJSON 이벤트 계약을 유지한다(`text_start`·`text_delta`·`answer`·`artifact`·`done`).
  - 검증된 문장마다 `text_delta`를 보낸다. `answer`는 각주(`[^1]`)와 출처가 붙은 마크다운이다.
  - 그래서 Spring 저장·복원과 기존 마크다운 렌더러가 그대로 동작한다. 새 이벤트는 진행 상태 `status` 하나뿐이다.
- 막힘 흐름은 `Gateway` 계약(`explain`은 문자열 반환)을 유지한다. 하네스판 게이트웨이가 근거 각주가 붙은 마크다운을 돌려준다. 상태 모델, Spring, 프런트는 바꾸지 않는다.
- 스위치 `CS_HARNESS=1`(ai-core 환경 변수)이 이 계획의 모든 동작 변경을 켠다. 기본은 꺼짐이며, 꺼지면 지금과 같다.

**Tech Stack:**
- Python 표준 라이브러리: sqlite3, zipfile, html.parser, re, array
- 기존 openai SDK: 임베딩·생성 모두 OpenAI 호환 주소로 호출
- 외부 도구(저장소 밖): llama.cpp `llama-server`(CUDA 빌드)로 임베딩과 작은 gemma 모델을 서빙. Ghostscript `gs`(PDF 텍스트·그림 변환)는 서버에 이미 있음
- 새 Python 패키지 없음

**Spec:** `docs/superpowers/specs/2026-10-28-harness-rag-design.md` (2026-10-28 승인)

## Global Constraints

- PR 하나는 하나의 목적이고 변경 파일 4~5개 이내를 목표로 한다.
  - main 직접 커밋, force push, rebase는 하지 않는다.
  - `Co-Authored-By`를 넣지 않는다.
  - 병합은 `gh pr merge --merge`로 한다.
- 모든 동작 변경은 `CS_HARNESS=1`일 때만 켜진다. 꺼진 상태의 기존 테스트는 전부 그대로 통과해야 한다(실험 보호, spec 9절).
- 지도 밖 질문(`none`)의 차단 문구는 정확히 `이 도구는 자료구조·운영체제·데이터베이스·네트워크·컴퓨터구조의 61개 개념만 다룹니다. 로드맵에서 개념을 골라 학습해 주세요.`다.
- 근거 없음 문구: `교재에서 이 질문의 근거를 찾지 못했습니다.`
- 생성 실패 문구: `설명을 만들지 못했습니다. 아래 교재 원문을 확인해 주세요.`
- "확인 필요" 표시: 문장 끝에 ` ⚠️ **확인 필요**`
- 한 답의 문장은 최대 8개이고, 문장 하나는 200자 이하다.
- 검색은 상위 3개 문단이고, 그림은 최대 1개다. 재시도는 형식 오류 1회, 수정 1회다. 틀린 문장이 절반을 넘으면 원문으로 대체한다.
- 한자 검사 범위: `[㐀-䶿一-鿿豈-﫿]`
- 판정 근거(`evidence`)는 공백을 정규화한 뒤 문단의 부분 문자열이어야 한다. 서술 채점의 근거는 공백과 문장부호를 지운 뒤 학생 답의 부분 문자열이어야 한다.
- 교재 원문, 그림, 색인, 측정 결과, 하네스 로그는 Git 밖에 둔다.
  - 교재 원문·그림·색인: `ai-core/data/rag/`
  - 측정 결과·하네스 로그: `ai-core/outputs/`
- CS-300과 모델 선택 평가 세트(`codex/` 아래 기존 세트)는 읽지도 쓰지도 않는다.
- 측정 세트 생성 모델은 gemma-4-26B-A4B다. gpt-6-astra 등 외부 API로는 측정 데이터를 만들지 않는다.
- 차단된 사이트(Watt `opentextbc.ca`의 403)는 우회하지 않는다. 사람이 브라우저로 받은 파일만 쓴다.
- 주석은 AGENTS.md 7절(한국어 개조식, Doxygen 형식)을 따른다.
- 포트는 20000 이상을 쓴다. 측정용 llama-server는 20300번대, 임베딩 서버는 20310이다.

## Review Focus

1. **검증 안 된 문장이 표시 없이 나가는 경로.** 판정 예외, 수정 실패, 시간 초과 중 어느 경로로도 `verified`가 아닌 문장이 ⚠️ 없이 `text_delta`나 `answer`에 들어가면 안 된다. 담당: Task 6 테스트 `test_no_unverified_sentence_leaks`
2. **스위치 꺼짐 = 기존 동작.** `CS_HARNESS` 미설정 시 `/chat`과 `/stuck/step`이 기존 에이전트·게이트웨이를 쓰는지 확인한다. 담당: Task 7 `test_chat_uses_agent_when_switch_off`, Task 9 `test_stuck_uses_model_gateway_when_switch_off`
3. **후속 질문의 개념 이어받기.** "더 쉽게"처럼 개념어가 없는 질문이 직전 개념의 문단을 쓰는지 확인한다. 직전 턴에 근거 기록이 없으면(스위치를 켜기 전 대화) 일반 범위 결정으로 가야 한다. 담당: Task 7 `test_follow_up_reuses_previous_grounding`, `test_follow_up_without_grounding_falls_back`
4. **근거 구절 대조의 거짓 실패.** 줄바꿈과 연속 공백이 다른 같은 구절은 통과해야 하고, 한 글자라도 다른 구절은 실패해야 한다. 담당: Task 5 `test_evidence_whitespace_normalized`, `test_evidence_altered_word_fails`
5. **빈 색인·개념에 문단 없음.** 빈칸 개념(조인 알고리즘 등)이나 색인 파일이 없을 때 예외 대신 근거 없음 문구가 나와야 한다. 담당: Task 3 `test_search_unknown_concept_returns_empty`, Task 6 `test_no_passage_returns_no_source_text`

---

### Task 1: 교재 수집과 절 분해

**Files:**
- Create: `ai-core/rag/__init__.py` (빈 파일 + 패키지 설명 주석)
- Create: `ai-core/rag/sources.py`
- Create: `ai-core/rag/parse.py`
- Create: `ai-core/rag/fetch.py`
- Create: `ai-core/tests/test_rag_parse.py`
- Modify: `.gitignore` (`/ai-core/data/rag/` 추가)

**Interfaces:**
- Produces:
  - `rag.sources.Book` (frozen dataclass): `id: str, title: str, authors: str, license: str, license_url: str, home: str, kind: Literal['latex','rst','pdf','epub','html','wikibooks'], fetch: str | None, figures: Literal['all','none','allowlist'], figure_allow: tuple[str, ...] = ()`
    - `figures`는 그림 라이선스 확인 결과다(spec 6절). `hailperin`은 `all`(Seagate 사진 파일 제외), `peterson-davie`는 출판사 ISBN이 붙은 파일을 뺀 `allowlist`, `openstax-cs`·`wikibooks-mpd`는 `none`으로 시작한다. 확인되지 않은 그림은 색인하지 않는다.
    - `fetch`가 None이면 사람이 직접 내려받는 책이다.
  - `rag.sources.BOOKS: dict[str, Book]`
    - 키: `ods`, `erickson`, `peterson-davie`, `hailperin`, `openstax-cs`, `watt-db`, `wikibooks-mpd`
    - 값은 `~/cs-handoff/research/rag-open-textbooks-2026-10-28.md`의 라이선스·출처 문구를 따른다.
    - `watt-db`와 `openstax-cs`는 `fetch=None`이다.
  - `rag.parse.FigureRef` (frozen dataclass): `file: str, caption: str, label: str | None`
  - `rag.parse.Section` (frozen dataclass): `book: str, section_id: str, title: str, text: str, figures: tuple[FigureRef, ...]`
    - `section_id`는 책 안에서 안정적인 경로다. 예: `scheduling.tex#3.3`, `internetworking/routing.rst#3.4.2`
  - `rag.parse.parse_book(book: Book, raw_dir: Path) -> list[Section]`
  - `rag.fetch.main(argv: list[str] | None = None) -> int`
    - 실행: `python -m rag.fetch [--book ID]`
    - 원본을 `ai-core/data/rag/raw/<book>/`에 받는다. `fetch=None`인 책은 놓을 위치를 안내만 한다.
    - 그림은 `ai-core/data/rag/figures/<book>/<file>.png`로 변환한다. PDF 그림은 `gs -sDEVICE=png16m -r200`, PNG는 복사한다.
    - 변환할 수 없는 형식(ODS의 Ipe 원본 등)은 건너뛰고 건수를 보고한다.
    - 결과를 `ai-core/data/rag/sections.jsonl`에 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성** `tests/test_rag_parse.py`
  - `test_latex_sections_split_on_section_commands`: `\section{A}`·`\subsection{B}` 문자열 → 절 2개, 제목 `A`·`B`, 본문에 LaTeX 명령 이름(`\section`)이 남지 않음
  - `test_latex_figure_caption_extracted`: `\includegraphics{hail_f0303}` + `\caption{Threads change states…}` → `FigureRef(file='hail_f0303', caption='Threads change states…')`
  - `test_rst_sections_use_underline_headings`: `Title\n=====` / `Sub\n---` → 절 2개
  - `test_rst_figure_directive`: `.. figure:: figures/f03-01.png` + 캡션 줄 → `FigureRef`
  - `test_epub_sections_from_xhtml_headings`: `zipfile`로 만든 최소 EPUB(`h1`·`h2`·`img alt`) → 절과 그림
  - `test_missing_manual_book_returns_empty`: 원본 폴더가 없는 `fetch=None` 책 → `[]`
  - `test_figures_filtered_by_book_policy`: `figures='none'` 책의 `FigureRef`는 결과에서 빠지고, `allowlist` 책은 목록에 있는 파일만 남음
- [ ] **Step 2:** `cd ai-core && .venv/bin/python -m unittest tests.test_rag_parse -v` → 모듈이 없어 FAIL
- [ ] **Step 3: `rag/sources.py`, `rag/parse.py`, `rag/fetch.py` 구현**
  - 형식별 파서는 표준 라이브러리만 쓴다.
  - PDF(erickson)는 `gs -sDEVICE=txtwrite`로 텍스트를 뽑은 뒤 장·절 제목 정규식으로 자른다.
  - GitHub 원본은 `gh api -H "Accept: application/vnd.github.raw"`나 raw URL로 받는다. 30초 시간 제한 실패 시 1회 재시도한다.
- [ ] **Step 4:** 같은 테스트 → PASS. 이어서 `cd ai-core && .venv/bin/python -m unittest discover -s tests` → 전체 PASS
- [ ] **Step 5: 실제 수집 확인.** `python -m rag.fetch`
  - 확인: `sections.jsonl` 줄 수를 책별로 보고하고, 책마다 절 3개 이상
  - 사람 작업: Watt EPUB과 OpenStax PDF가 없으면 안내만 출력하고, 사용자에게 내려받기를 요청하는 메모를 PR에 남긴다.
- [ ] **Step 6: 커밋** `feat(rag): 공개 교재 수집과 절 분해 추가`

### Task 2: 절 대응표 (데이터 PR, 사용자 검수)

**Files:**
- Create: `ai-core/content/rag/section-map.json`
- Create: `ai-core/rag/section_map.py`
- Create: `ai-core/tests/test_rag_section_map.py`

**Interfaces:**
- Consumes: `rag.sources.BOOKS`, `sections.jsonl`(Task 1), `serving.concept_map.load_concept_map()`
- Produces:
  - 대응표 JSON 형식: `{"version": 1, "entries": [{"book": str, "section_id": str, "concepts": [str, ...]}], "gaps": [{"concept": str, "reason": str}]}`
  - `rag.section_map.load_section_map(path: Path | None = None) -> SectionMap`
    - `SectionMap.concepts_for(book, section_id) -> tuple[str, ...]`
    - `SectionMap.sections_for(concept) -> tuple[tuple[str, str], ...]`
  - 검수 시트 CLI: `python -m rag.section_map --review OUT.md`
    - 개념별로 대응된 절 제목과 본문 첫 200자를 표로 쓴다. OUT은 Git 밖(`~/cs-handoff/`)에 둔다.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_concepts_exist_in_concept_map`: 모든 `concepts`가 합친 지도의 ID
  - `test_books_known`: 모든 `book`이 `BOOKS` 키
  - `test_every_concept_covered_or_gap`: 61개념 각각 `sections_for`가 1개 이상이거나 `gaps`에 있음
  - `test_no_duplicate_entries`: `(book, section_id)` 중복 없음
  - `test_gaps_include_known_blanks`: `gaps`에 `join-algorithms`가 있음. 2의 보수 개념 ID는 지도에서 확인해 넣는다.
- [ ] **Step 2:** `python -m unittest tests.test_rag_section_map -v` → FAIL
- [ ] **Step 3: 로더 구현, 대응표 초안 작성**
  - 초안은 `sections.jsonl`의 절 제목과 첫 문단을 읽고 개념 설명(`description`·`objectives`)과 맞춰 작성한다.
  - 절이 개념의 정의나 핵심 원리를 직접 다룰 때만 대응시킨다. 지나가는 언급은 넣지 않는다.
- [ ] **Step 4:** 테스트 PASS. 전체 AI core 테스트 PASS
- [ ] **Step 5: 검수 시트 생성.** `python -m rag.section_map --review ~/cs-handoff/section-map-review.md`
- [ ] **Step 6: 커밋, PR.** `add(rag): 교재 절과 개념 대응표 초안 추가`
  - **병합 조건: 사용자가 검수 시트를 보고 승인.** 2026-10-26 데이터 PR 위임의 예외다(spec 6절 "사용자가 직접 검수").

### Task 3: 문단 색인과 검색

**Files:**
- Create: `ai-core/rag/embed.py`
- Create: `ai-core/rag/index.py`
- Create: `ai-core/rag/search.py`
- Create: `ai-core/tests/test_rag_index.py`

**Interfaces:**
- Consumes: `Section`(Task 1), `SectionMap`(Task 2)
- Produces:
  - `rag.embed.Embedder(base_url: str, model: str, query_prefix: str = '', passage_prefix: str = '')`
    - `.embed_queries(texts: list[str]) -> list[list[float]]`
    - `.embed_passages(texts: list[str]) -> list[list[float]]`
    - 결과는 L2 정규화한다. 호출은 OpenAI `embeddings.create`다.
    - 환경 변수: `RAG_EMBED_URL`(기본 `http://127.0.0.1:20310/v1`), `RAG_EMBED_MODEL`, `RAG_EMBED_QUERY_PREFIX`, `RAG_EMBED_PASSAGE_PREFIX`
  - `rag.index.Chunk` (frozen dataclass): `id: str, book: str, section_id: str, title: str, text: str`
  - `rag.index.chunk_section(section: Section, max_chars: int = 1200) -> list[Chunk]`
    - 문단 경계에서 자르고 `id`는 `f"{book}:{section_id}:{n}"`이다.
  - `rag.index.build_index(sections, section_map, embedder, db_path: Path) -> dict[str, int]`
    - 대응표에 있는 절만 색인한다. 반환값은 `{'chunks': n, 'figures': m}`이다.
    - SQLite 테이블
      - `chunks(id TEXT PK, book, section_id, title, text, vector BLOB)`
      - `chunk_concepts(chunk_id, concept)`
      - `figures(id TEXT PK, book, section_id, caption, path, vector BLOB)`
      - `figure_concepts(figure_id, concept)`
    - 벡터는 `array('f')` 바이트로 저장한다.
  - CLI: `python -m rag.index` → `ai-core/data/rag/index.sqlite`
  - `rag.search.Passage` (frozen dataclass): `id, book, section_id, title, text, score: float`
  - `rag.search.Figure` (frozen dataclass): `id, book, section_id, caption, path: Path, score: float`
  - `rag.search.Retriever(db_path: Path, embedder)`
    - `.passages(concepts: list[str], query: str, k: int = 3, exclude: set[str] = frozenset()) -> list[Passage]`
    - `.figure(concepts: list[str], query: str) -> Figure | None`
    - 개념 꼬리표로 먼저 거른 뒤 코사인 유사도 순으로 고른다. `RAG_MIN_SCORE`(기본 0.0, Task 4에서 정함) 미만은 버린다. 색인 파일이 없으면 `[]`와 None을 돌려준다.

- [ ] **Step 1: 실패하는 테스트 작성** (가짜 임베더: 단어 주머니 기반 결정적 벡터)
  - `test_chunk_section_respects_max_chars_and_paragraphs`
  - `test_build_index_only_mapped_sections`: 대응표에 없는 절은 색인에 없음
  - `test_search_filters_by_concept`: 다른 개념 꼬리표의 더 비슷한 문단이 있어도 고르지 않음
  - `test_search_orders_by_similarity_and_k`
  - `test_search_min_score_drops_all`: 기준 이상인 문단이 없으면 `[]`
  - `test_search_unknown_concept_returns_empty`, `test_missing_db_returns_empty`
  - `test_exclude_skips_used_passages`: 3차 보충에서 쓸 `exclude`
- [ ] **Step 2:** FAIL 확인
- [ ] **Step 3:** 구현
- [ ] **Step 4:** PASS. 전체 AI core 테스트 PASS
- [ ] **Step 5:** 커밋 `feat(rag): 개념 꼬리표 기반 문단·그림 색인과 검색 추가`

### Task 4: 검색 측정 세트와 임베딩 모델 선택

**Files:**
- Create: `ai-core/eval/harness_sets.py`
- Create: `ai-core/eval/harness_retrieval.py`
- Create: `ai-core/tests/test_eval_harness_sets.py`
- Create: `docs/harness-measurement.md` (llama.cpp 빌드·서버 실행 명령, 이 Task 결과 기록)

**Interfaces:**
- Consumes: `Retriever`, `Embedder`, `Chunk`(Task 3), `StuckModelGateway._call`(26B 생성)
- Produces:
  - `eval.harness_sets.retrieval_set(chunks: list[Chunk], concepts_of: Callable[[str], tuple[str, ...]], write_question: Callable[[Chunk], str], per_concept: int = 3, seed: int = 0) -> list[dict]`
    - 행 형식: `{id, concept, question, gold_chunk}`
    - 정답(`gold_chunk`)은 질문을 만든 문단이다.
  - `eval.harness_sets.sample_sheet(rows, n: int, out: Path)`: 사용자 표본 확인용 마크다운
  - `eval.harness_retrieval.recall_at_k(rows, retriever, k=3) -> float`, CLI는 `python -m eval.harness_retrieval --set PATH`
- 세트 파일은 `ai-core/outputs/harness-sets/retrieval.jsonl`(Git 밖)에 둔다.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_retrieval_set_gold_is_source_chunk`
  - `test_retrieval_set_per_concept_and_seeded`
  - `test_recall_at_k_counts_gold_in_top_k`
- [ ] **Step 2:** FAIL 확인 → **Step 3:** 구현 → **Step 4:** PASS. 전체 AI core 테스트 PASS
- [ ] **Step 5: 외부 도구 준비** (저장소 밖, `docs/harness-measurement.md`에 명령 기록)
  - `~/llama.cpp`를 `cmake -DGGML_CUDA=ON`으로 빌드한다.
  - 임베딩 후보 2개를 GGUF로 내려받는다: `bge-m3`, `multilingual-e5-base`
  - CPU로 `llama-server --embeddings --port 20310 -ngl 0`을 실행한다.
- [ ] **Step 6: 측정**
  - 26B(8000번)로 검색 세트를 만든다.
  - 후보별로 색인을 다시 만들어 상위 3개 회수율을 잰다.
  - 회수율이 높은 모델을 고르고, 차이가 2%p 이내면 작은 모델을 고른다.
  - `RAG_MIN_SCORE`는 정답 문단 점수 분포의 하위 5% 값으로 정한다.
  - 결과와 판단을 `docs/harness-measurement.md`에 적는다.
- [ ] **Step 7: 사용자 표본 시트.** `~/cs-handoff/retrieval-sample.md`, 개념당 1개
- [ ] **Step 8:** 커밋 `feat(eval): 검색 측정 세트와 임베딩 모델 선택 결과 추가`

### Task 5: 하네스 검증 규칙과 모델 역할

**Files:**
- Create: `ai-core/serving/harness.py` (이 Task에서는 검증 함수와 자료형만)
- Create: `ai-core/serving/harness_llm.py`
- Modify: `ai-core/serving/stuck_llm.py` (`ROLE_SETTINGS`·`PROMPTS`에 역할 4개 추가)
- Create: `ai-core/tests/test_harness_rules.py`

**Interfaces:**
- Produces (`serving/harness.py`):
  - `Sentence` (frozen dataclass): `text: str, sources: tuple[str, ...], status: Literal['verified','needs_check']`
    - `sources`의 요소는 근거 키다. 문단은 `'p1'~'p3'`, 애니메이션 단계는 `'a1'~'aN'`이다.
  - `validate_sentences(raw: dict, source_keys: set[str]) -> list[tuple[str, tuple[str, ...]]]`
    - 문장 1~8개, 각 200자 이하, `sources`가 비어 있지 않고 모두 `source_keys` 안에 있어야 한다. 한자가 섞이면 안 된다. 어기면 `ValueError`다.
  - `evidence_in(evidence: str, text: str) -> bool`: 공백 정규화 후 부분 문자열이면 참. 빈 문자열은 거짓이다.
  - `grading_evidence_in(evidence: str, answer: str) -> bool`: 공백과 문장부호(`string.punctuation`과 `·…“”‘’「」『』`)를 지운 뒤 부분 문자열이면 참. 빈 문자열은 거짓이다.
  - `has_han(text: str) -> bool`
- Produces (`serving/harness_llm.py`): `HarnessGateway(StuckModelGateway)`
  - `.scope(question: str, previous: list[str], concept_map) -> tuple[list[str], bool]`: (개념 1~2개 또는 `[]`=none, follow_up). 스키마의 enum은 61개 ID와 `none`이다.
  - `.ground(question: str, sources: dict[str, str], concept_titles: list[str], prior: list[str]) -> list[tuple[str, tuple[str, ...]]]`: 응답을 `validate_sentences`로 검증한다. 실패하면 `_call`이 재시도한다(`attempts=2`).
  - `.judge(sentence: str, source_text: str) -> tuple[Literal['supported','unsupported','partial'], str]`
  - `.repair(sentence: str, source_text: str, source_key: str) -> tuple[str, tuple[str, ...]]`
- 역할 설정: `scope`·`judge`는 temperature 0, `ground`·`repair`는 temperature 0.3, `max_tokens`는 각각 128·256·1024·256, `timeout` 30초, `attempts` 2

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_validate_rejects_missing_or_unknown_source`
  - `test_validate_rejects_han_characters`: `"利用了"` 포함 → ValueError
  - `test_validate_rejects_over_8_sentences_and_over_200_chars`
  - `test_evidence_whitespace_normalized`: 줄바꿈·연속 공백 차이 → 참
  - `test_evidence_altered_word_fails`: 한 단어 바뀜 → 거짓
  - `test_grading_evidence_ignores_spaces_and_punctuation`: `"스택은 LIFO다."` vs 답 `"스택은LIFO 다"` → 참
  - `test_grading_evidence_empty_is_false`
  - `test_scope_schema_enum_matches_concept_map`: 가짜 클라이언트로 요청의 `response_format` 확인
  - `test_ground_retries_on_invalid_then_raises`: 가짜 클라이언트가 두 번 다 잘못된 출력 → `LLMUnavailable`
- [ ] **Step 2:** FAIL 확인 → **Step 3:** 구현(프롬프트는 spec 5-1·5-3·5-4·5-5의 입력·출력 그대로) → **Step 4:** PASS. 전체 AI core 테스트 PASS
- [ ] **Step 5:** 커밋 `feat(ai): 하네스 검증 규칙과 범위·생성·판정·수정 역할 추가`

### Task 6: 하네스 흐름과 기록

**Files:**
- Modify: `ai-core/serving/harness.py` (흐름 추가)
- Create: `ai-core/serving/harness_log.py`
- Create: `ai-core/tests/test_harness_flow.py`

**Interfaces:**
- Consumes: Task 3 `Retriever`·`Passage`·`Figure`, Task 5 전부
- Produces:
  - `Grounding` (frozen dataclass): `concepts: tuple[str, ...], passage_ids: tuple[str, ...], verified: tuple[str, ...]`
    - 다음 턴의 후속 질문이 이어받는 기록이다. `artifact` 이벤트 `{'kind': 'grounding', ...}`로 Spring에 저장된다.
  - `answer(question: str, previous: Grounding | None, gw: HarnessGateway, retriever: Retriever, concept_map, log: HarnessLog) -> Iterator[dict]`
    - 이벤트 순서: `{'type':'status','stage':'scope'}` → (차단이면 `text_start`·`text_delta`(차단 문구)·`answer`·`done`) → `status: search` → `status: write` → 문장마다 `status: check`
    - 그 뒤 검증·수정이 끝난 문장마다 `text_delta`(문장 + 각주 `[^p1]`, 확인 필요면 ⚠️), 그림이 있으면 `artifact`(Task 7 형식), 마지막에 `artifact`(grounding), `answer`, `done`
  - `render_answer(sentences: list[Sentence], passages: dict[str, Passage], books) -> str`
    - 마크다운 문장 + 각주 정의(`[^p1]: > 원문…` + 교재명·저자·절·라이선스·"발췌") + `**출처**` 목록을 만든다.
  - `fallback_answer(passages, books) -> str`: 생성 실패 문구 + 원문 인용 + 출처
  - `HarnessLog(dir: Path)`, `.write(record: dict)`: `ai-core/outputs/harness-logs/YYYY-MM-DD.jsonl`에 추가한다. 기록 항목은 spec 5-7을 따른다.
- 규칙(spec 5장)
  - 판정이 `supported`이면서 `evidence_in`이 참일 때만 `verified`다. 그 밖은 수정 1회 후 재판정하고, 그래도 아니면 `needs_check`다.
  - 판정·수정 호출이 `LLMUnavailable`이면 그 문장은 `needs_check`다.
  - `needs_check`가 절반을 넘으면 문장 대신 `fallback_answer`를 쓴다(이미 보낸 `text_delta`가 있으면 `text_start`로 초기화한 뒤).
  - 문장 순서를 지킨다. 앞 문장이 끝나야 다음 문장의 `text_delta`를 보낸다.

- [ ] **Step 1: 실패하는 테스트 작성** (가짜 게이트웨이·검색기)
  - `test_blocked_question_returns_fixed_text`
  - `test_no_passage_returns_no_source_text`
  - `test_all_verified_emits_sentences_in_order_with_footnotes`
  - `test_wrong_sentence_repaired_then_verified`
  - `test_unrepairable_sentence_marked_needs_check`
  - `test_judge_evidence_not_in_passage_counts_as_wrong`
  - `test_judge_failure_marks_needs_check`
  - `test_majority_wrong_replaced_by_fallback`
  - `test_no_unverified_sentence_leaks`: 가능한 모든 판정 조합(supported/unsupported/partial × evidence 유무 × 예외)에서 ⚠️ 없는 문장은 모두 verified
  - `test_grounding_artifact_emitted_last_before_answer`
  - `test_log_record_has_stage_timings`
- [ ] **Step 2:** FAIL → **Step 3:** 구현 → **Step 4:** PASS. 전체 AI core 테스트 PASS
- [ ] **Step 5:** 커밋 `feat(ai): 근거 검사 하네스 흐름과 단계별 기록 추가`

### Task 7: 채팅 연결과 교재 그림

**Files:**
- Modify: `ai-core/serving/api.py`
- Create: `ai-core/serving/harness_chat.py` (스위치·이력에서 Grounding 복원·애니메이션·그림 artifact 조립)
- Create: `ai-core/tests/test_harness_chat.py`

**Interfaces:**
- Consumes: Task 6 `answer`, `Grounding`; 기존 `create_animation`, `CONCEPT_SCENES`, `StuckModelGateway.choose_animation`
- Produces:
  - `harness_enabled() -> bool`: `os.getenv('CS_HARNESS') == '1'`
  - `previous_grounding(history: list[dict]) -> Grounding | None`: 마지막 assistant 턴의 `kind == 'grounding'` artifact를 찾는다.
  - `chat_events(question, history, gw, retriever, concept_map, log) -> Iterator[dict]`
    - Task 6 `answer`를 감싼다.
    - 고른 개념에 애니메이션 장면이 있고 질문이 과정·추적을 묻거나(`scope`가 낸 개념에 `CONCEPT_SCENES` 항목이 있을 때) 그림 검색 결과가 없으면 `choose_animation`으로 애니메이션 artifact를 하나 보낸다.
    - 아니면 교재 그림 artifact를 보낸다: `{'type':'artifact','kind':'figure','artifact_id': hex32, 'figure': {'title', 'caption', 'source'}}`
    - 그림 파일은 `OUTPUT_DIR/{artifact_id}.png`로 복사한다.
  - `/chat`: `harness_enabled()`이면 `chat_events`를 쓰고, 아니면 기존 `run_agent`를 쓴다.
  - `/artifacts/{id}`: `{id}.png`가 있으면 `image/png`, 아니면 기존 SVG 처리
  - `history_messages`와 `artifact_data`는 `grounding`·`figure` 종류를 모델 입력에서 뺀다(스위치가 꺼진 에이전트로 넘어가도 깨지지 않게).
- 결정: `create_diagram`은 스위치가 켜지면 쓰이지 않는다(하네스 경로에 도구 없음). 코드 삭제는 spec 9절대로 나중에 한다.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_chat_uses_agent_when_switch_off`
  - `test_chat_uses_harness_when_switch_on`
  - `test_follow_up_reuses_previous_grounding`
  - `test_follow_up_without_grounding_falls_back`
  - `test_figure_artifact_png_served`
  - `test_history_skips_grounding_and_figure_artifacts`
  - `test_animation_preferred_for_trace_concepts`
- [ ] **Step 2:** FAIL → **Step 3:** 구현 → **Step 4:** PASS. 전체 AI core 테스트 PASS
- [ ] **Step 5:** 커밋 `feat(ai): 스위치로 채팅을 근거 하네스와 교재 그림에 연결`

### Task 8: Spring 그림 형식 중계와 화면 표시

**Files:**
- Modify: `backend/src/main/java/com/csagent/backend/AgentController.java` (`/artifacts/{id}` 응답 Content-Type을 core가 준 값 중 `image/png`·`image/svg+xml`만 그대로 전달, 그 외는 404)
- Modify: `backend/src/test/java/...` 기존 AgentController 테스트 파일 (PNG 중계 테스트 추가)
- Modify: `frontend/src/ChatSession.tsx` (`status` 이벤트를 진행 문구로 표시: scope "질문 범위 확인 중", search "근거 찾는 중", write "설명 쓰는 중", check "확인 중")
- Modify: `frontend/src/DiagramPanel.tsx` (`kind === 'figure'`면 캡션·출처를 보이고 저장 단추를 PNG로)
- Modify: `frontend/src/api.ts` (artifact 타입에 `figure` 종류 추가, `grounding` 종류는 화면 artifact로 쓰지 않음)

**Interfaces:**
- Consumes: Task 7 이벤트·artifact 형식
- 디자인 작업은 `design-taste-frontend` 스킬을 따른다. 각주와 ⚠️ 문장 스타일은 기존 `markdown.tsx`(remark-gfm 각주) 위에 CSS만 더한다.

- [ ] **Step 1: 실패하는 테스트 작성**
  - 백엔드: `artifactPassesPngContentType`, `artifactRejectsOtherContentTypes`
  - 프런트(`tests/api.test.mjs` 또는 새 `tests/harness.test.mjs`): `status` 이벤트 → 진행 문구, `grounding` artifact 무시
  - 프런트 브라우저(`tests/browser/learning.spec.ts`): 가짜 스트림으로 각주가 렌더되고 `⚠️ 확인 필요`가 보이며, 그림 패널에 캡션과 출처가 보이는지 확인. Playwright는 `flock /tmp/cs-playwright-5197.lock`로 실행한다.
- [ ] **Step 2:** FAIL → **Step 3:** 구현 → **Step 4:** `cd backend && ./gradlew test bootJar`, `cd frontend && npm test && npm run build`, 브라우저 테스트 PASS
- [ ] **Step 5:** 커밋 `feat: 교재 그림 중계와 근거 답변 진행 표시 추가`

### Task 9: 막힘 보충·서술 채점·복귀 요약 하네스판

**Files:**
- Create: `ai-core/serving/harness_stuck.py`
- Modify: `ai-core/serving/api.py` (`/stuck/step`이 스위치에 따라 게이트웨이 선택)
- Create: `ai-core/tests/test_harness_stuck.py`

**Interfaces:**
- Consumes: Task 3 `Retriever`, Task 5·6 검증·판정·렌더링, 기존 `Gateway` 계약
- Produces: `HarnessStuckGateway(HarnessGateway)`
  - `.explain(concept, attempt, entry, scene=None) -> str`
    - 1차: 개념 문단 상위 3개
    - 2차: `scene`이 있으면 `scene['steps']`를 근거 `a1..aN`으로 쓰고(문단 대신), 없으면 1차에서 쓰지 않은 상위 3개 문단
    - 3차: 앞 시도에서 쓰지 않은 상위 3개 문단
    - 쓴 문단은 `entry`와 개념 ID로 키를 만든 프로세스 내 표에 기억한다. 서버가 재시작되면 잊어도 된다. 그 경우 1차와 같은 문단을 써도 오류는 아니다.
    - 결과는 Task 6 `render_answer` 마크다운이다. 근거가 없으면 근거 없음 문구, 생성이 실패하면 `fallback_answer`를 돌려준다.
  - `.grade_explanation(item, answer) -> list[bool]`: 기존 판정 후 `met`이 참이어도 `grading_evidence_in(evidence, answer)`가 거짓이면 거짓으로 바꾼다.
  - `.summarize(state, concept_map) -> str`: 모델을 부르지 않고 코드가 문장을 조립한다.
    - 형식: `"{막힌 개념}을(를) 이해하려고 {보충 개념들}을(를) 보충했어요."` + 통과 개념 `"…은(는) 이해 확인을 통과했어요."` + 미통과 개념 `"…은(는) 아직 이해 확인을 통과하지 못했어요. 다시 살펴보세요."`
    - 조사는 받침 여부로 고른다.
- `/stuck/step`: `harness_enabled()`이면 `HarnessStuckGateway`, 아니면 `StuckModelGateway`

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_stuck_uses_model_gateway_when_switch_off`
  - `test_explain_first_attempt_cites_passages`
  - `test_explain_second_attempt_uses_scene_steps_as_sources`
  - `test_explain_third_attempt_uses_unused_passages`
  - `test_explain_no_passage_returns_no_source_text`
  - `test_grade_evidence_not_in_answer_flips_to_false`
  - `test_summarize_is_template_with_particles`: `"큐를"`, `"힙을"`, `"스택을"`의 조사 처리
  - 기존 `tests/test_stuck_flow.py`를 `HarnessStuckGateway`(가짜 클라이언트)로 한 번 더 돌리는 매개변수 테스트: 상태 전이가 같음
- [ ] **Step 2:** FAIL → **Step 3:** 구현 → **Step 4:** PASS. 전체 AI core 테스트 PASS
- [ ] **Step 5:** 커밋 `feat(ai): 막힘 보충·채점·복귀 요약에 근거 하네스 적용`

### Task 10: 범위·판정·채점 측정 세트

**Files:**
- Modify: `ai-core/eval/harness_sets.py`
- Modify: `ai-core/tests/test_eval_harness_sets.py`
- Modify: `docs/harness-measurement.md`

**Interfaces:**
- Produces
  - `scope_set(concept_map, write_questions, per_concept=5, outside=60, follow_ups=60, seed=0) -> list[dict]`
    - 행 형식: `{id, question, previous: [ids], expected: [ids] | [], kind: 'direct'|'compare'|'outside'|'follow_up'}`
    - 비교 질문은 선수 관계가 있는 개념 쌍에서 만든다.
    - 지도 밖 질문에는 코드 오류 찾기 요청 20개를 포함한다.
  - `perturbation_set(verified_rows, perturb, seed=0) -> list[dict]`
    - 행 형식: `{id, sentence, passage, label: 'supported'|'unsupported', perturbation: None|'number'|'negation'|'addition'|'swap_concept'}`
    - 맞는 문장과 틀린 문장을 반반으로 둔다.
    - 맞는 문장은 26B 하네스에서 verified가 된 문장 중 사용자가 표본 확인한 것의 출처 세트에서 뽑는다.
    - `perturb`의 숫자 바꾸기·부정은 코드 규칙으로 하고, 덧붙이기·개념 바꾸기는 26B가 한다. 결과가 원문과 같으면 버린다.
  - `grading_set(mohler_rows) -> list[dict]`: 기존 `eval/mohler.py`의 사람 채점 번역본을 하네스 채점 입력 형식으로 바꾼다.
- 세트는 Git 밖 `ai-core/outputs/harness-sets/`에 둔다. 사용자 표본 시트는 `~/cs-handoff/`에 둔다.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_scope_set_kinds_and_counts`
  - `test_scope_compare_pairs_are_prerequisites`
  - `test_perturbation_balanced_and_changed`: 바꾼 문장 ≠ 원문
  - `test_number_perturbation_changes_a_number`
  - `test_grading_set_keeps_human_labels`
- [ ] **Step 2:** FAIL → **Step 3:** 구현 → **Step 4:** PASS. 전체 AI core 테스트 PASS
- [ ] **Step 5:** 26B로 세트를 생성한다. 사용자 표본 시트(범위 개념당 1개, 판정 50개)를 만들고 **사용자 확인을 요청**한다.
- [ ] **Step 6:** 커밋 `feat(eval): 하네스 범위·판정·채점 측정 세트 생성기 추가`

### Task 11: 모델별 측정과 보고

**Files:**
- Create: `ai-core/eval/harness_measure.py`
- Create: `ai-core/tests/test_eval_harness_measure.py`
- Modify: `docs/harness-measurement.md`

**Interfaces:**
- Consumes: Task 4·10 세트, Task 6 `answer`, Task 7 `chat_events`
- Produces: `python -m eval.harness_measure --base-url URL --model NAME [--judge-url URL --judge-model NAME] --out DIR`
  - 지표(spec 8-2)
    - 범위 정확도(종류별)
    - 판정 검출률·오경보율(교란 세트)
    - 채점 거짓 통과·거짓 불통과(채점 세트)
    - 노출 지표: 표시 없이 나간 문장 중 26B 판정기와 사용자 표본이 근거 불일치로 본 비율
    - 확인 필요 비율, 근거 없음 비율, 한자 건수
    - 첫 문장까지 시간과 전체 시간의 중앙값·90% 값
  - GPU 메모리 최대값을 측정 중 1초 간격 `nvidia-smi --query-gpu=memory.used`로 기록한다.
  - 결과: `DIR/report.md`와 `DIR/rows.jsonl`
- `metrics(rows) -> dict`는 순수 함수다(테스트 대상).

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_metrics_percentiles`
  - `test_exposure_counts_only_unmarked_sentences`
  - `test_false_pass_and_false_fail`
- [ ] **Step 2:** FAIL → **Step 3:** 구현 → **Step 4:** PASS. 전체 AI core 테스트 PASS
- [ ] **Step 5: GPU 확보 확인 (사람 작업 관문).** GPU 1번(다른 사용자 데모 점유) 조율 결과나, GPU 0번 운영 중단 허락을 사용자에게 받는다. 허락 없이는 운영 모델을 멈추지 않는다.
- [ ] **Step 6: 측정 실행** (명령은 `docs/harness-measurement.md`)
  - 26B(vLLM)
  - E4B Q4·Q8, 12B Q4, E2B Q4(llama.cpp, `-ngl`을 GPU 메모리 8GB 이내로 맞춰 실행)
  - 26B 판정기 교차 측정(E4B Q8)
- [ ] **Step 7: 보고서 작성.** `docs/harness-measurement.md`에 표와 spec 8-4 기준 판정을 적는다.
  - 3090 시간은 RTX 4060급으로 보정한 추정치와 함께 보고한다. 보정 계수는 llama.cpp 공개 벤치마크의 두 GPU 생성 속도 비율이고, 출처를 적는다.
- [ ] **Step 8:** 커밋 `feat(eval): 하네스 모델별 측정과 보고서 추가`. 결과를 사용자에게 보고하고 전환·파인튜닝 결정을 요청한다.

---

## 순서와 의존

- 1 → 2(사용자 검수) → 3 → 4 → 5 → 6 → 7 → 8, 9 → 10 → 11
- 5는 3과 병렬로 할 수 있다(검색과 독립).
- 8과 9는 7 뒤에 병렬로 할 수 있다.
- **사람 작업 관문**
  - Task 1: Watt EPUB·OpenStax PDF 내려받기
  - Task 2: 대응표 검수
  - Task 4·10: 표본 확인
  - Task 11: GPU 확보
