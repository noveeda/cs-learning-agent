# 하네스 측정 기록

하네스·RAG 계획(`docs/superpowers/plans/2026-10-28-harness-rag.md`)의 측정 명령과 결과를 모은다.
측정 세트·색인·결과 원본은 모두 Git 밖(`ai-core/outputs/`, `RAG_DATA_DIR`)에 있고, 여기에는 명령과 요약만 적는다.

## 1. 외부 도구 준비 (Task 4 Step 5)

### 1-1. llama.cpp 빌드

- 위치: `~/llama.cpp` (저장소 밖). 2026-10-28 `master` 커밋 `cf24317`
- 서버의 `/usr/local/cuda-12.8`은 nvcc만 있고 cuBLAS가 없다. 그래서 툴킷 전체가 있는 13.0으로 빌드한다.
  - nvcc 12.8에 `/usr/local/cuda`(13.0)의 cuBLAS 헤더가 섞이면 `cudaEmulationSpecialValuesSupport is undefined`로 실패한다.
  - 드라이버는 CUDA 13.2까지 지원하므로 13.0 빌드가 돈다.
- GPU는 RTX 3090(sm_86)이라 아키텍처를 86으로 고정해 빌드 시간을 줄인다.

```sh
git clone --depth 1 https://github.com/ggml-org/llama.cpp.git ~/llama.cpp
cd ~/llama.cpp
cmake -B build -DGGML_CUDA=ON \
  -DCUDAToolkit_ROOT=/usr/local/cuda-13.0 -DCMAKE_CUDA_COMPILER=/usr/local/cuda-13.0/bin/nvcc \
  -DCMAKE_CUDA_ARCHITECTURES=86 -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j 32 --target llama-server llama-cli
```

### 1-2. 임베딩 모델 내려받기

- 두 후보 모두 같은 양자화(Q8_0)로 받아 양자화 차이가 비교에 섞이지 않게 한다. 위치는 `~/.cache/cs-embed/`(저장소 밖)다.

| 후보 | Hugging Face 저장소(리비전) | 파일 | 크기 | 차원 | 최대 입력 |
|---|---|---|---|---|---|
| bge-m3 (MIT) | `ggml-org/bge-m3-Q8_0-GGUF` (`9eba04c`) | `bge-m3-q8_0.gguf` | 635MB | 1024 | 8192 토큰 |
| multilingual-e5-base (MIT) | `dinab/multilingual-e5-base-Q8_0-GGUF` (`b964777`) | `multilingual-e5-base-q8_0.gguf` | 303MB | 768 | 512 토큰 |

```sh
mkdir -p ~/.cache/cs-embed && cd ~/.cache/cs-embed
curl -L -o bge-m3-q8_0.gguf \
  https://huggingface.co/ggml-org/bge-m3-Q8_0-GGUF/resolve/9eba04c5d75ba5a1595e45de734d36bef4e5cb98/bge-m3-q8_0.gguf
curl -L -o multilingual-e5-base-q8_0.gguf \
  https://huggingface.co/dinab/multilingual-e5-base-Q8_0-GGUF/resolve/b96477725c04afcdc7c8bba980c18e682a80f462/multilingual-e5-base-q8_0.gguf
```

### 1-3. 임베딩 서버 실행 (CPU)

- 임베딩은 CPU로만 돌린다(`-ngl 0`, `CUDA_VISIBLE_DEVICES=` 빈 값). GPU 두 장은 26B 운영 모델과 다른 사용자 데모가 쓰고 있다.
- 풀링은 각 모델의 학습 방식대로 지정한다. bge-m3는 `cls`, e5는 `mean`이다.
- e5는 질문과 문단 앞에 `query: `, `passage: ` 접두어를 붙여야 한다. bge-m3는 접두어가 없다.
- `-ub`(한 번에 처리하는 토큰 수)는 입력 하나 전체가 들어가도록 문맥 길이와 같게 둔다. 임베딩은 입력을 나눠 처리할 수 없다.
  - 색인 문단 묶음(1200자 이하)은 가장 긴 것이 약 700토큰이다. 그래서 bge-m3는 2048로 둔다.
  - 8192로 두면 상주 메모리가 1048MB이고, 2048이면 995MB다. 벡터는 같다(코사인 0.9997 이상).
  - e5는 모델 자체 한도가 512토큰이라 더 늘릴 수 없다.

```sh
# bge-m3 → 20310
CUDA_VISIBLE_DEVICES= nohup ~/llama.cpp/build/bin/llama-server -m ~/.cache/cs-embed/bge-m3-q8_0.gguf \
  --embeddings --pooling cls -ngl 0 -t 16 -c 2048 -b 2048 -ub 2048 \
  --host 127.0.0.1 --port 20310 --alias bge-m3 > ~/cs-embed-logs/bge-m3.log 2>&1 &
# multilingual-e5-base → 20311
CUDA_VISIBLE_DEVICES= nohup ~/llama.cpp/build/bin/llama-server -m ~/.cache/cs-embed/multilingual-e5-base-q8_0.gguf \
  --embeddings --pooling mean -ngl 0 -t 16 -c 512 -b 512 -ub 512 \
  --host 127.0.0.1 --port 20311 --alias multilingual-e5-base > ~/cs-embed-logs/e5.log 2>&1 &
```

- 각 후보의 임베더 환경 변수는 아래와 같다.

| 후보 | `RAG_EMBED_URL` | `RAG_EMBED_MODEL` | `RAG_EMBED_QUERY_PREFIX` | `RAG_EMBED_PASSAGE_PREFIX` |
|---|---|---|---|---|
| bge-m3 | `http://127.0.0.1:20310/v1` | `bge-m3` | (없음) | (없음) |
| multilingual-e5-base | `http://127.0.0.1:20311/v1` | `multilingual-e5-base` | `query: ` | `passage: ` |

## 2. 검색 측정 세트와 임베딩 모델 선택 (Task 4 Step 6)

### 2-1. 명령

```sh
cd ai-core
export RAG_DATA_DIR=/home/infoserver/cs-learning-agent/ai-core/data/rag
# (1) 검색 세트: 개념당 문단 3개, 26B(8000번)가 문단마다 한국어 학생 질문을 씀
.venv/bin/python -m eval.harness_sets retrieval
#     → ai-core/outputs/harness-sets/retrieval.jsonl, 표본 시트 ~/cs-handoff/retrieval-sample.md(개념당 1개)
# (2) 후보마다 전용 색인을 새로 만들고 회수율을 잼(위 표의 환경 변수를 후보별로 바꿔 반복)
#     --db로 후보별 파일을 지정해 운영 색인(RAG_DATA_DIR/index.sqlite)을 덮지 않음
RAG_EMBED_URL=http://127.0.0.1:20310/v1 RAG_EMBED_MODEL=bge-m3 \
  .venv/bin/python -m eval.harness_retrieval --set outputs/harness-sets/retrieval.jsonl \
  --build --db outputs/harness-sets/index-bge-m3.sqlite
```

- `--section-map PATH`로 저장소 밖 대응표를 줄 수 있다. 대응표가 main에 병합되기 전에는 이 인자로 초안을 썼다.
- 대응표가 바뀐 뒤에는 세트를 처음부터 다시 만들지 않고 `--refresh 기존세트`로 고친다.
  - 정답 문단이 아직 그 개념에 대응되는 행은 질문까지 그대로 둔다.
  - 대응이 끊긴 행만 빼고, 같은 개념의 안 쓴 문단으로 개념당 3개를 채운다.
- 운영 색인은 `python -m rag.index`로 만든다(`RAG_DATA_DIR/index.sqlite`, 2-3절).

- 세트 규칙
  - 문단 묶음은 색인과 같은 `rag.index.chunk_section`으로 자른다. 그래서 정답 id가 색인의 문단 id와 그대로 맞는다.
  - 300자 미만 묶음(장 머리말·한 줄 설명)은 질문 재료에서 뺀다.
  - 질문 생성은 temperature 0, 추론 끔이다. 잘린 출력, 200자를 넘는 출력, 한자가 섞인 출력은 그 문단을 세트에서 뺀다.
- 회수율은 운영 검색기(`Retriever.passages`)를 그대로 거쳐 잰다. 개념 꼬리표 거르기와 라이선스 계열 규칙이 들어간 값이다. 기준 점수는 끈다(`min_score=-1`).
- 선택 규칙(계획): 상위 3개 회수율이 높은 모델을 고른다. 차이가 2%p 이내면 작은 모델을 고른다.
  `RAG_MIN_SCORE`는 고른 모델의 정답 점수 분포 하위 5% 값이다.

### 2-2. 결과 (2026-10-28, 초안 대응표 기준)

- 이 절은 초안 기준 기록으로 남긴다. 운영 값은 2-3절(확정 대응표)이 대체한다.

- **기준 데이터**
  - 절 대응표는 PR #113 초안 커밋 `69edcaf`다. 절 269개, 개념 59개, 빈칸 2개다.
    - 사용자 검수 중이라 main에 병합되기 전이다. 대응표가 바뀌면 다시 잰다.
  - 교재 원문은 `sections.jsonl`의 절 2,140개다. 색인 문단 묶음은 987개다.
  - 검색 세트는 175행이다(개념 59개 × 최대 3개, 300자 이상 묶음이 모자란 개념은 적음).
- **회수율 결과**

| 후보 | 상위 3개 회수율 | 상위 1개 회수율 | 후보 4개 이상 행(171행)의 상위 3개 | 정답 점수 하위 5% | 정답 점수 중앙값 | 색인 시간(CPU 16스레드) | 상주 메모리 |
|---|---|---|---|---|---|---|---|
| **bge-m3** | **91.4%** (160/175) | 66.3% | 91.2% | 0.510 | 0.607 | 약 5분 50초 | 995MB |
| multilingual-e5-base | 75.4% (132/175) | 56.0% | 74.9% | 0.774 | 0.805 | 약 1분 55초 | 632MB |

- **세트와 측정 조건**
  - 개념당 후보 문단 수의 중앙값은 16개다. 무작위로 3개를 고르면 약 19%이므로, 회수율은 개념 거르기만으로 얻는 값이 아니다.
  - e5는 512토큰을 넘는 문단 7개(987개 중)를 e5 표준 사용법대로 512토큰에서 잘라 임베딩했다.
    - 자르기는 측정용 일회성 스크립트에서만 했다. 저장소 임베더에는 자르기가 없다.
- **선택: bge-m3**
  - 상위 3개 회수율이 e5보다 16.0%p 높다. 계획의 "2%p 이내면 작은 모델" 규칙에 해당하지 않는다.
  - 상주 메모리 1GB 이하, CPU 실행, MIT 라이선스라는 spec 조건을 만족한다.
  - 입력 한도가 8192토큰이라 문단을 자르지 않아도 된다.
- **`RAG_MIN_SCORE` = 0.51**
  - bge-m3 정답 점수 분포(175개)의 하위 5% 값 0.5103을 소수 둘째 자리로 내렸다.
  - 이 기준이면 정답 문단의 약 95%가 기준을 넘는다. 가장 낮은 정답 점수는 0.442다.
  - 점수 척도가 모델마다 다르므로(e5는 정답 중앙값 0.80), 임베딩 모델을 바꾸면 이 값도 다시 정한다.
- **운영 설정 (Task 6·7이 쓸 값)**
  - `RAG_EMBED_URL=http://127.0.0.1:20310/v1`
  - `RAG_EMBED_MODEL=bge-m3`
  - 접두어 없음
  - `RAG_MIN_SCORE=0.51`
- **사용자 표본 확인:** `~/cs-handoff/retrieval-sample.md`(개념당 1개, 59개)
  - 질문이 정답 문단만으로 답할 수 있는지, 개념에 맞는 학생 질문인지 확인한다.
  - 부적절한 행이 많으면 질문 생성 지시를 고쳐 세트를 다시 만든다.

### 2-3. 결과 (2026-10-29, 확정 대응표 `bbbce57` 기준)

- **기준 데이터**
  - 절 대응표는 PR #113 병합 커밋 `bbbce57`이다.
    - 사용자 검수로 초안에서 56줄을 빼고 41줄을 더했다.
    - 절 250개, 절-개념 쌍 285개, 개념 59개다. 빈칸은 `sorting-basics`, `join-algorithms` 2개다.
  - 교재 원문은 2-2절과 같은 `sections.jsonl`(절 2,140개)이다.
- **공용 색인:** `RAG_DATA_DIR/index.sqlite`
  - 명령: `RAG_EMBED_URL=http://127.0.0.1:20310/v1 RAG_EMBED_MODEL=bge-m3 .venv/bin/python -m rag.index`
  - 문단 묶음 956개, 그림 23개, 문단-개념 꼬리표 1,135쌍이다. CPU에서 약 5분 걸렸다.
  - 하네스 런타임(`CS_HARNESS=1`)과 Task 10·11 측정이 이 파일을 쓴다.
- **검색 세트:** `ai-core/outputs/harness-sets/retrieval.jsonl`, 176행
  - 초안 세트(175행)는 `retrieval-draft-69edcaf.jsonl`로 남겼다.
  - 정답 문단의 절이 확정 대응표에서 그 개념에 더 이상 대응되지 않는 30행을 뺐다(20개 개념).
  - 같은 생성기(26B, `--refresh`)로 31행을 새로 만들었다.
    - `logarithm`은 초안에서 2행이었는데, 확정 대응표에서 문단이 늘어 3행이 됐다.
  - 개념 58개가 3행이고, `tree-traversal`만 2행이다. 이 개념은 대응 문단이 2개뿐이다.
- **bge-m3 회수율** (e5는 다시 재지 않았다)

| 기준 | 상위 3개 회수율 | 상위 1개 회수율 | 후보 4개 이상 행의 상위 3개 | 정답 점수 하위 5% | 정답 점수 중앙값 | 최저 정답 점수 |
|---|---|---|---|---|---|---|
| 초안 `69edcaf` (2-2) | 91.4% (160/175) | 66.3% | 91.2% (171행) | 0.510 | 0.607 | 0.442 |
| **확정 `bbbce57`** | **88.1% (155/176)** | 65.9% | 87.9% (174행) | **0.501** | 0.609 | 0.442 |

- **회수율이 내려간 이유**
  - 그대로 둔 145행의 회수율은 89.0%, 새로 만든 31행은 83.9%다.
  - 대응표가 바뀌어 같은 개념 안의 경쟁 문단이 달라진 영향이 섞여 있다.
  - 개념당 후보 문단 수의 중앙값은 15개다.
- **`RAG_MIN_SCORE` = 0.50**
  - 확정 세트 정답 점수(176개)의 하위 5% 값 0.5014를 소수 둘째 자리로 내렸다.
  - 운영 기본값으로 코드에 넣었다: `serving/harness_chat.py`의 `DEFAULT_MIN_SCORE`. 환경 변수 `RAG_MIN_SCORE`가 있으면 그 값을 쓴다.
  - `rag/search.py`의 기본값(0.0)은 그대로 둔다. 점수 척도가 임베딩 모델마다 달라서, 운영 검색기를 만드는 곳에만 둔다.
  - 스위치가 꺼져 있으면 검색기를 만들지 않으므로 기존 동작에는 영향이 없다.
- **이 기준이 다른 경로에 주는 영향** (min_score를 끄고 잰 값)
  - [막혔어요] 보충은 "개념 제목 + 설명"으로 검색한다.
    - 개념 59개 모두 1위 문단 점수가 0.50 이상이다(최저 `normalization` 0.524, 중앙값 0.634). 이 기준 때문에 근거 없음이 되는 개념은 없다.
  - 교재 그림도 같은 기준으로 거른다.
    - 검색 세트 질문 176개 중 그림 후보가 있는 것은 24개다. 그중 13개만 0.50 이상이다(그림 점수 중앙값 0.508).
    - 그림 캡션은 짧아 점수가 낮게 나오므로, 그림이 덜 붙는 쪽으로 동작한다.
- **운영 설정 (확정)**
  - `RAG_EMBED_URL=http://127.0.0.1:20310/v1`
  - `RAG_EMBED_MODEL=bge-m3`
  - 접두어 없음
  - `RAG_MIN_SCORE`: 기본 0.50
- **사용자 표본 확인:** `~/cs-handoff/retrieval-sample.md`
  - 확정 세트로 다시 만들었다. 개념당 1개, 59개이고, 정답 문단 원문을 함께 실었다.
  - 초안 시트는 `retrieval-sample-draft-69edcaf.md`로 남겼다.

## 3. 범위·판정·채점 측정 세트 (Task 10)

생성기: `ai-core/eval/harness_sets_judge.py`.
세트는 Git 밖 `HARNESS_SETS_DIR`(기본 `ai-core/outputs/harness-sets/`)에 있다. 세트마다 같은 이름의 `.meta.json`에 만든 조건을 남긴다.

### 3-1. 명령

```bash
cd ai-core
export HARNESS_SETS_DIR=/home/infoserver/cs-learning-agent/ai-core/outputs/harness-sets
# (1) 범위 세트: 26B(8000번)가 종류별 학생 질문을 씀 → scope.jsonl, 표본 시트 ~/cs-handoff/scope-sample.md
.venv/bin/python -m eval.harness_sets_judge scope
# (2) 맞는 문장 모으기: 26B 하네스로 범위 세트의 직접 질문 120개를 처리 → harvest-logs/*.jsonl
#     색인은 확정 색인 RAG_DATA_DIR/index.sqlite(--index를 빼면 이것을 씀), 임베딩·기준은 2-3에서 정한 값(bge-m3, RAG_MIN_SCORE=0.50)
RAG_EMBED_MODEL=bge-m3 RAG_MIN_SCORE=0.50 .venv/bin/python -m eval.harness_sets_judge harvest --limit 120
# (3) 판정 세트: verified 문장 절반은 그대로, 절반은 망가뜨림 → judge.jsonl, 표본 시트 ~/cs-handoff/judge-sample.md
.venv/bin/python -m eval.harness_sets_judge perturb --map-commit bbbce57
# (4) 채점 세트: Mohler 사람 채점 원본(영어)과 검수한 루브릭 → grading.jsonl
.venv/bin/python -m eval.harness_sets_judge grading \
  --data /home/infoserver/cs-eval-data/raw/mohler/open_ended.jsonl \
  --rubrics /home/infoserver/cs-eval-data/processed/mohler/rubrics.jsonl
```

- 절 대응표가 검수로 바뀌면 색인을 다시 만든 뒤 (2)·(3)을 다시 실행한다. 이전 `harvest-logs/`는 지우고 실행한다.
- 처음 세트는 초안 색인(`index-bge-m3.sqlite`, 대응표 `69edcaf`)으로 만들었고, 확정 색인으로 다시 만들면서 초안 결과는 이름에 `-draft-69edcaf`를 붙여 남겼다.
  - `judge-draft-69edcaf.jsonl`, `judge-draft-69edcaf.meta.json`, `harvest-logs-draft-69edcaf/`, `~/cs-handoff/judge-sample-draft-69edcaf.md`
- (1)·(4)는 대응표와 무관하다.

### 3-2. 결과 (2026-10-29, 확정 색인: 대응표 `bbbce57`, 문단 956·그림 23, `RAG_MIN_SCORE=0.50`)

| 세트 | 행 | 구성 | 정답의 근거 |
|---|---:|---|---|
| `scope.jsonl` | 485 | 직접 305(개념당 5), 비교 60, 지도 밖 60(그중 코드 요청 20), 후속 60 | 질문을 만든 규칙(개념·선수 관계 쌍·지도 밖·직전 개념) |
| `judge.jsonl` | 616 | 맞음 308, 틀림 308(덧붙이기 123, 개념 바꾸기 89, 반대로 말하기 77, 숫자 바꾸기 19) | 망가뜨린 방식 |
| `grading.jsonl` | 2,273 | 루브릭이 있는 81문항 전체, 사람 통과(평균 4.0 이상) 1,639 | 사람 채점 점수 |

- 범위 세트
  - 비교 질문은 선수 관계(개념 → 선수 개념) 82쌍 중 60쌍에서 만들었다.
  - 문항 은행과 같은 문장, 한자가 섞인 질문은 버리고 다시 만들었다.
- 판정 세트
  - 맞는 문장은 26B 하네스가 verified로 낸 문장(근거 문단 1개) 617개 중 616개다(반반을 맞추려고 1개 뺌).
  - harvest는 초안 때와 같은 120질문(seed 0)이다. 초안 색인 결과와 비교하면 아래와 같다.

    | | 초안 색인 `69edcaf` | 확정 색인 `bbbce57` |
    |---|---:|---:|
    | 답변 / 근거 없음 / 원문 대체 | 110 / 7 / 3 | 110 / 7 / 3 |
    | 문장 verified / 확인 필요 | 635 / 71 | 647 / 61 |
    | 수정 시도 / 수정 실패(`:` 응답) | 86 / 9 | 80 / 0 |
    | 수정 후 verified | 15 | 19 |
    | 그림이 붙은 답 | 0 | 8 |

  - 확정 색인 수확은 `:` 수정(PR #122) 뒤라 수정 역할이 실패 없이 동작했다. 모든 역할에서 형식 오류 재시도 경고가 0건이었다.
  - 숫자 바꾸기·반대로 말하기는 코드 규칙으로, 덧붙이기·개념 바꾸기는 26B로 했다.
  - 숫자가 있는 문장은 숫자 바꾸기를 먼저 시도한다. 영문자에 붙은 숫자(`L1`, `IPv4`)는 이름이라 바꾸지 않는다.
- 채점 세트: `split`(dev·report)은 `eval/mohler.py`의 문항 묶음 분할과 같다.
- **사용자 표본 확인 요청**
  - `~/cs-handoff/scope-sample.md`: 개념당 직접 질문 1개 + 비교·지도 밖·후속 각 5개
  - `~/cs-handoff/judge-sample.md`: 맞음 25 + 틀림 25. 요약 표 아래에 행마다 문장과 근거 문단 원문 전체를 둠

### 3-3. 한계와 발견

- **채점 측정은 영어 답 기준이다.** Mohler는 번역하지 않았다. 번역도 모델 생성이라 검수 대상이 늘어나기 때문이다(2026-10-29 코디네이터 결정). 한국어 답에서의 거짓 불통과는 석사생 채점 세트가 생기면 다시 잰다.
- **판정 세트는 확정 대응표(`bbbce57`) 기준이다.** 대응표가 다시 바뀌면 이전 `harvest-logs/`를 옮기거나 지운 뒤 3-1의 (2)·(3)으로 다시 만든다.
- **26B와 strict JSON schema의 `:` 출력**
  - 문자열 하나(`{"sentence": ...}`)를 schema로 묶으면 26B가 `{"sentence":": "}`를 내는 경우가 많았다. 같은 요청을 schema 없이 보내면 정상 문장이 나왔다.
  - 그래서 교란 요청은 schema 없이 받고 결과를 코드로 검사한다.
  - 하네스 생성·판정·수정 역할의 같은 현상은 원인을 찾아 PR #122에서 고쳤다(지시에 공백 없는 JSON 예시). 확정 색인 수확에서 수정 실패 0건으로 확인했다.

## 4. 모델별 측정 (Task 11)

측정 도구: `ai-core/eval/harness_measure.py`. 결과 원본(`rows.jsonl`, `metrics.json`, `report.md`, `meta.json`, `gpu.csv`)은 Git 밖 `ai-core/outputs/harness-measure/<모델>/`에 있다.

### 4-1. 모델 파일

- 위치는 `~/.cache/cs-llm/`(저장소 밖)이다. 내려받은 뒤 Hugging Face의 LFS sha256과 대조했다.
- 저장소는 ggml-org 공식을 먼저 찾고, 그 양자화가 없으면 unsloth, 그다음 bartowski 순으로 골랐다.
  - ggml-org에는 Q4_0·Q8_0만 있고 Q4_K_M이 없다. 그래서 Q4_K_M은 unsloth에서 받았다.
  - unsloth에는 12B가 없어 12B Q4_K_M은 bartowski에서 받았다.

| 모델 | Hugging Face 저장소(리비전) | 파일 | 크기 |
|---|---|---|---:|
| E4B Q4_K_M | `unsloth/gemma-4-E4B-it-GGUF` (`bfc15c3`) | `gemma-4-E4B-it-Q4_K_M.gguf` | 4.98GB |
| E4B Q8_0 | `ggml-org/gemma-4-E4B-it-GGUF` (`a69f018`) | `gemma-4-E4B-it-Q8_0.gguf` | 8.03GB |
| 12B Q4_K_M | `bartowski/gemma-4-12B-it-GGUF` (`2ae7d41`) | `gemma-4-12B-it-Q4_K_M.gguf` | 7.66GB |
| E2B Q4_K_M | `unsloth/gemma-4-E2B-it-GGUF` (`0314792`) | `gemma-4-E2B-it-Q4_K_M.gguf` | 3.11GB |
| 12B QAT Q4_0 (Google 공식) | `google/gemma-4-12B-it-qat-q4_0-gguf` (`29d0977`) | `gemma-4-12b-it-qat-q4_0.gguf` | 6.98GB |
| 12B MTP 드래프트 헤드 | `unsloth/gemma-4-12B-it-qat-GGUF` (`980b060`) | `mtp-gemma-4-12B-it.gguf`(Q4_0) | 0.25GB |

```sh
mkdir -p ~/.cache/cs-llm && cd ~/.cache/cs-llm
# 저장소 리비전 고정(전체 해시)
curl -L -o gemma-4-E4B-it-Q4_K_M.unsloth.gguf \
  https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/bfc15c382204943c3a8fff0c750b94ae2364d7a3/gemma-4-E4B-it-Q4_K_M.gguf
curl -L -o gemma-4-E4B-it-Q8_0.ggml-org.gguf \
  https://huggingface.co/ggml-org/gemma-4-E4B-it-GGUF/resolve/a69f018d7678a2b9f5e705f8903d6fb29b9b25dc/gemma-4-E4B-it-Q8_0.gguf
curl -L -o gemma-4-12B-it-Q4_K_M.bartowski.gguf \
  https://huggingface.co/bartowski/gemma-4-12B-it-GGUF/resolve/2ae7d41be21ca62de00a2d320ee9cec50daa3aa6/gemma-4-12B-it-Q4_K_M.gguf
curl -L -o gemma-4-E2B-it-Q4_K_M.unsloth.gguf \
  https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/0314792d7f1f7e229411f620751375812bb9faf2/gemma-4-E2B-it-Q4_K_M.gguf
curl -L -o gemma-4-12b-it-qat-q4_0.google.gguf \
  https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf/resolve/29d097773436b69ff9feafd636ab4cf873786537/gemma-4-12b-it-qat-q4_0.gguf
curl -L -o mtp-gemma-4-12B-it.unsloth.gguf \
  https://huggingface.co/unsloth/gemma-4-12B-it-qat-GGUF/resolve/980b060c40a8539ac159e0501a3e0f66a6365af3/mtp-gemma-4-12B-it.gguf
```

- sha256(LFS)
  - E4B Q4_K_M `85a896a0…1fab87`, E4B Q8_0 `34be82b1…8ce80a`, 12B Q4_K_M `3962624d…671509`, E2B Q4_K_M `740185b2…ec34b8`
  - 12B QAT Q4_0 `93567e57…8a538b`, MTP 헤드 `fcb35dea…c6f4f6`

**다른 12B급 거르기 후보(사용자 승인, 2026-10-29)**

- 회사 공식 GGUF를 먼저 찾았다. 없으면 ggml-org → unsloth → bartowski 순으로 찾았다.
  - Ministral은 mistralai 공식 GGUF가 있어 그것을 받았다.
  - Midm·Kanana는 네 곳 모두에 Q4_K_M GGUF가 없었다. 그래서 공식 safetensors를 받아 직접 변환했다(코디네이터 결정 A안).
- 변환은 미세조정 뒤 GGUF 변환에도 같은 경로를 쓴다. 학습 환경 `~/.venvs/cs-train`(torch 2.13 cu130, transformers 5.21)에 모자란 것만 더했다.
  - 원본 safetensors는 `~/.cache/cs-llm/src/`, 변환본은 `~/.cache/cs-llm/`에 둔다(저장소 밖). 중간 BF16 파일은 지웠다.

| 모델 | 원본(리비전) | 파일 | 크기 | sha256 |
|---|---|---|---:|---|
| Ministral 3 14B Q4_K_M | `mistralai/Ministral-3-14B-Instruct-2512-GGUF` (`74fac47`) | `Ministral-3-14B-Instruct-2512-Q4_K_M.gguf` | 8.24GB | `824e0f33…8cc613` |
| Midm 2.0 Base Q4_K_M | `K-intelligence/Midm-2.0-Base-Instruct` (`35479c5`) safetensors → 변환 | `Midm-2.0-Base-Instruct-Q4_K_M.converted.gguf` | 7.03GB | `5e2759fa…6fc683` |
| Kanana 1.5 8B Q4_K_M | `kakaocorp/kanana-1.5-8b-instruct-2505` (`c963a5f`) safetensors → 변환 | `kanana-1.5-8b-instruct-2505-Q4_K_M.converted.gguf` | 4.92GB | `c0713691…71c069` |

```sh
cd ~/.cache/cs-llm
curl -L -o Ministral-3-14B-Instruct-2512-Q4_K_M.mistralai.gguf \
  https://huggingface.co/mistralai/Ministral-3-14B-Instruct-2512-GGUF/resolve/74fac473c43357d7fb2671713608183cc72496d0/Ministral-3-14B-Instruct-2512-Q4_K_M.gguf

# 변환 도구 준비(한 번): 변환 스크립트의 의존성과 양자화 도구
uv pip install --python ~/.venvs/cs-train/bin/python sentencepiece protobuf   # cs-train에는 pip이 없어 uv로 설치
cmake --build ~/llama.cpp/build --config Release -j 16 --target llama-quantize   # 1-1과 같은 빌드 디렉터리

# 공식 safetensors 받기(리비전 고정)
~/.venvs/cs-train/bin/python -c "from huggingface_hub import snapshot_download as d
d('K-intelligence/Midm-2.0-Base-Instruct', revision='35479c5fc9a18a5db7cc6dbadcf1db68db7beab0', local_dir='src/Midm-2.0-Base-Instruct')
d('kakaocorp/kanana-1.5-8b-instruct-2505', revision='c963a5f4f6496c749f94064a20b33028b0db9f19', local_dir='src/kanana-1.5-8b-instruct-2505')"

# 변환(CPU만): safetensors → BF16 GGUF → Q4_K_M
for name in Midm-2.0-Base-Instruct kanana-1.5-8b-instruct-2505; do
  CUDA_VISIBLE_DEVICES= PYTHONPATH=~/llama.cpp/gguf-py ~/.venvs/cs-train/bin/python ~/llama.cpp/convert_hf_to_gguf.py \
    src/$name --outtype bf16 --outfile $name-BF16.gguf
  CUDA_VISIBLE_DEVICES= ~/llama.cpp/build/bin/llama-quantize $name-BF16.gguf $name-Q4_K_M.converted.gguf Q4_K_M 16
  rm $name-BF16.gguf
done
```

- sha256 전체
  - Ministral `824e0f3373e69b84f2cae46fdcb9bd1ebc6ab3bfc7acc125d818b7b8178cc613`(LFS와 대조)
  - Midm 변환본 `5e2759fa04ca1414e8de25b127d76e8dbe0fa5ba3c8b666beaba656f416fc683`
  - Kanana 변환본 `c0713691b06d3d6f968bd3fee97b792ff341e180357dc28a8958c79e6371c069`

### 4-2. 서버 설정

- 작은 모델은 모두 GPU 1번(`CUDA_VISIBLE_DEVICES=1`)에만 띄운다. GPU 0번은 운영 26B다.
- 공통: `--jinja`(GGUF에 든 gemma 4 대화 템플릿), `-fa on`. 사고 모드는 요청마다 `chat_template_kwargs.enable_thinking=false`로 끈다(운영과 같은 인자).
- **정확도 측정(병렬):** `-ngl 99 -np 4 -c 32768`(요청 칸 4개, 칸마다 8192토큰). 여러 모델을 GPU 1번에 함께 띄워 동시에 쟀다(합계 23GB 이하).
- **시간·메모리 측정(단독):** 로컬 도구처럼 요청 하나만 받는 `-np 1 -c 8192`로 쟀다.
  - GPU 1번에 그 모델 하나만 띄웠고, 26B(8000)에는 이 작업의 다른 측정 요청을 보내지 않았다.
  - 메모리 기준: 8GB 카드(nvidia-smi 총량 8,188MiB)에서 화면 출력·드라이버 몫 약 0.3GB를 남긴 **최대 7,900MiB**다.
    - `-ngl`은 맞춤 요청(2,625토큰 요약 요청 하나)을 처리하는 동안 GPU 1번 사용량을 0.2초마다 읽은 최댓값으로 맞췄다. 아래 표의 메모리·속도가 그 값이다.
  - `-ngl`은 기준 안에 드는 가장 큰 값으로 정했다. 모자라는 층은 CPU가 계산한다.

| 설정 | `-ngl`(층 48개+출력층) | 최대 메모리(맞춤 요청) | 생성 속도(3090, 맞춤 요청) |
|---|---:|---:|---:|
| 12B QAT Q4_0 | 99(전부) | 7,706MiB | 83토큰/초 |
| 12B QAT Q4_0 + MTP | 47(층 1개+출력층 CPU) | 7,766MiB | 92토큰/초 |
| 12B Q4_K_M | 45 | 7,750MiB | 15토큰/초 |
| 12B Q4_K_M + MTP | 43 | 7,752MiB | 25토큰/초 |

- 기준을 넘은 설정
  - 12B Q4_K_M을 전부 올리면 `-c 4096`이어도 8,282MiB(`-c 8192` 8,350MiB)로 8GB를 넘는다. `-ngl 46`은 7,904MiB, `-ngl 47`은 8,056MiB였다.
  - QAT에 MTP를 붙이고 전부 올리면 8,026MiB다. MTP 드래프트 헤드가 약 0.3GB를 더 쓴다(4-7).
  - QAT+MTP `-ngl 48`(출력층만 CPU)은 맞춤 요청에서 7,896MiB였지만 하네스 시간 측정 중 7,912MiB까지 올라 기준을 넘었다. 그래서 `-ngl 47`로 다시 쟀다(4-6에 둘 다 적음).
  - 문맥 길이를 8192→4096으로 줄여도 70MiB쯤만 준다. gemma 4는 대부분 층이 1024토큰 슬라이딩 창이라 KV 캐시가 작기 때문이다.

```sh
# 시간(예: 12B QAT + MTP → 20330, 하나씩 차례로)
CUDA_VISIBLE_DEVICES=1 ~/llama.cpp/build/bin/llama-server -m ~/.cache/cs-llm/gemma-4-12b-it-qat-q4_0.google.gguf \
  --alias gemma-4-12B-QAT-Q4_0 --host 127.0.0.1 --port 20330 --jinja -ngl 47 -fa on -np 1 -c 8192 \
  -md ~/.cache/cs-llm/mtp-gemma-4-12B-it.unsloth.gguf --spec-type draft-mtp
cd ai-core && HARNESS_SETS_DIR=… RAG_DATA_DIR=… RAG_EMBED_MODEL=bge-m3 .venv/bin/python -m eval.harness_measure \
  --base-url http://127.0.0.1:20330/v1 --model gemma-4-12B-QAT-Q4_0 --tasks timing --gpu 1 --out outputs/harness-measure/12b-qat-mtp47
```

```sh
# 정확도(예: 12B Q4_K_M → 20322)
CUDA_VISIBLE_DEVICES=1 nohup ~/llama.cpp/build/bin/llama-server -m ~/.cache/cs-llm/gemma-4-12B-it-Q4_K_M.bartowski.gguf \
  --alias gemma-4-12B-Q4_K_M --host 127.0.0.1 --port 20322 --jinja -ngl 99 -fa on -np 4 -c 32768 > ~/cs-llm-logs/12b-q4.log 2>&1 &
```

- 포트: E4B Q4 20320, E2B Q4 20321, 12B Q4 20322, E4B Q8 20323, 12B QAT 20324, Midm 20326, Ministral 20327, 단독 시간 측정 20330
- llama.cpp에서 하네스를 바꾸지 않고 그대로 돌렸다. `response_format`(JSON schema)·`chat_template_kwargs`·`top_k`를 llama-server가 받는다. 그래서 운영(vLLM) 코드는 바뀌지 않았다.

### 4-3. 측정 방법

- **정확도는 병렬, 시간은 단독**으로 쟀다.
  - 정확도(범위·판정·채점·답 전체)는 여러 모델을 GPU 1번에 함께 띄우고 모델마다 동시 요청 4개(`--workers 4`)로 쟀다.
  - 시간·최대 메모리는 마지막에 GPU 1번에 모델 하나만 띄우고 요청을 하나씩 순서대로 보내 쟀다. 병렬 측정 때의 `gpu.csv`는 모델별 최대 메모리로 쓰지 않는다.
- 세트: 3절의 범위 세트 485문항, 판정 세트 616문장, 채점 세트 2,273답, 답 전체는 범위 세트 485문항 전부다(층화 표본 없이 전체).
- 시간 표본: 검색 세트에서 개념을 돌아가며 하나씩 뽑은 60문항이다(`--timing-n 60 --seed 0`, 모든 모델 같은 질문).
- 검색(정답 문단 상위 3개 회수율)은 모델과 무관하다. 2-3의 bge-m3 확정 색인 값을 그대로 쓴다.
- 노출 지표는 표시 없이 나간 문장(답변 완료 답의 verified 문장)을 26B(8000)로 다시 판정해 어긋난 비율이다.
  - 사용자 표본 기준 값은 사용자 확인이 끝나지 않아 "대기"로 둔다.
- 26B(운영 8000번)는 읽기 호출만 했다. 동시 요청 수는 측정 프로세스 전체 합으로 묶었다(`--prod-slots`, 잠금 파일 칸; 사용자 허락으로 정확도 측정 때 4, 시간·교차 측정 때 2).
- 26B 시간은 운영 서버를 다른 작업과 함께 쓰는 상태에서 잰 **참고값**이다. 조건: `--max-num-seqs 8`(사용자 승인으로 재시작, 다른 옵션 동일), Task 3 수집(harvest) 동시 실행.

### 4-4. 3090 → RTX 4060 보정

- 출처: llama.cpp CUDA 성능 게시판 [discussion #15013](https://github.com/ggml-org/llama.cpp/discussions/15013)(Llama 2 7B Q4_0, `llama-bench`, flash attention 켬)
  - RTX 3090: 생성(tg128) 161.89토큰/초, 프롬프트 처리(pp512) 5,560토큰/초
  - RTX 4060 Ti 8GB: 생성 64.03토큰/초, 프롬프트 처리 3,803토큰/초
  - 데스크톱 RTX 4060은 표에 없다. Vulkan 게시판([#10879](https://github.com/ggml-org/llama.cpp/discussions/10879))에도 노트북용 4060만 있다.
- 생성 속도 비는 161.89 / 64.03 = 2.53이다. 4060은 메모리 대역폭이 272GB/s로 4060 Ti(288GB/s)보다 낮아 2.53 × 288/272 ≈ **2.68**로 잡는다.
- 하네스 시간은 대부분 짧은 JSON 출력과 프롬프트 처리라 생성 비(2.68)를 전체에 곱하면 보수적(느린 쪽) 추정이다. 프롬프트 처리 비는 1.46이다.
- 일부 층을 CPU가 계산하는 설정(Q4_K_M `-ngl 45`·`43`, QAT+MTP `-ngl 48`·`47`)은 CPU 몫이 GPU 비를 따르지 않아 이 보정이 맞지 않는다. 이 측정 서버의 CPU 기준 참고값으로만 본다.

### 4-5. 정확도 결과 (병렬 측정)

- E4B·E2B는 사용자 결정(2026-10-29, 12B + 하네스 + RAG + 미세조정)으로 제외했다. 그 전에 끝난 값만 참고로 남긴다.
  - E4B Q8_0은 답 전체를 107개만 재고 멈췄다(부분 값). 앞쪽 직접 질문이 대부분이라 다른 모델과 직접 비교할 수 없다.
- Midm·Ministral은 거르기(범위·판정)만 쟀다(4-8).

| 지표 | 26B-A4B AWQ | 12B Q4_K_M | 12B QAT Q4_0 | E4B Q4_K_M | E4B Q8_0 (답 107개) | E2B Q4_K_M | Midm 2.0 Q4_K_M | Ministral 3 14B Q4_K_M |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 범위 정확도 | 89.7% | 87.8% | 85.8% | 84.5% | 88.2% | 73.2% | 15.0% | 72.8% |
| 판정 검출률 | 71.1% | 68.5% | 64.3% | 38.0% | 38.0% | 35.7% | 48.0% | 81.5% |
| 판정 오경보율 | 0.7% | 3.6% | 1.6% | 4.9% | 4.9% | 7.5% | 13.0% | 29.2% |
| 채점 거짓 통과율 | 4.7% | 5.2% | 4.9% | 10.6% | 10.2% | 25.1% | - | - |
| 채점 거짓 불통과율 | 40.5% | 33.6% | 38.0% | 29.5% | 27.3% | 43.5% | - | - |
| 범위 · direct | 87.2% | 82.0% | 79.0% | 82.3% | 85.9% | 77.7% | 2.0% | 62.0% |
| 범위 · compare | 86.7% | 95.0% | 95.0% | 90.0% | 90.0% | 68.3% | 78.3% | 91.7% |
| 범위 · outside | 98.3% | 98.3% | 98.3% | 76.7% | 90.0% | 30.0% | 13.3% | 98.3% |
| 범위 · follow_up | 96.7% | 100.0% | 98.3% | 98.3% | 96.7% | 98.3% | 20.0% | 83.3% |
| 검출 · addition | 51.2% | 59.4% | 52.8% | 9.8% | 10.6% | 10.6% | 25.2% | 73.2% |
| 검출 · swap_concept | 78.6% | 64.0% | 60.7% | 39.3% | 39.3% | 36.0% | 44.9% | 85.4% |
| 검출 · negation | 88.3% | 87.0% | 84.4% | 80.5% | 77.9% | 64.9% | 87.0% | 87.0% |
| 검출 · number | 94.7% | 73.7% | 73.7% | 42.1% | 47.4% | 79.0% | 52.6% | 94.7% |
| 노출(26B 판정) | 0.1% (2/1967) | 5.5% (118/2165) | 5.9% (125/2126) | 7.3% (148/2033) | 9.9% (53/534) | 10.5% (230/2197) | - | - |
| 노출(사용자 표본) | 대기 | 대기 | 대기 | 대기 | 대기 | 대기 | - | - |
| 확인 필요 비율 | 5.9% | 5.6% | 4.0% | 3.2% | 1.5% | 6.0% | - | - |
| 근거 없음 비율 | 16.5% | 16.8% | 16.6% | 17.6% | 4.7% | 22.3% | - | - |
| 원문 대체 비율 | 0.9% | 2.6% | 1.2% | 0.9% | 1.9% | 1.3% | - | - |
| 한자 섞인 문장 | 0 | 0 | 0 | 0 | 0 | 0 | - | - |
| 답 결과(answered/blocked/no_source/fallback/generation_failed) | 350/61/70/4/0 | 342/61/71/3/8 | 348/62/70/3/2 | 357/47/77/4/0 | 100/0/5/2/0 | 357/18/104/6/0 | - | - |

- 12B 두 파일 비교
  - Q4_K_M이 범위(87.8% 대 85.8%)와 판정 검출(68.5% 대 64.3%)에서 앞선다.
  - QAT는 오경보(1.6% 대 3.6%)와 채점 거짓 통과(4.9% 대 5.2%)가 조금 낮다. 노출은 비슷하다(5.5% 대 5.9%).
- 12B Q4_K_M의 원문 대체 2.6%에는 생성 실패 8건이 들어 있다. 병렬 부하에서 문장 쓰기 호출이 30초 제한을 넘은 경우다(4-11).

### 4-6. 시간·메모리 결과 (단독 측정)

- 60문항을 하나씩 보냈다. 시간은 질문을 보낸 때부터 첫 문장 표시(첫 `text_delta`)까지, 답 끝(`done`)까지다.
- 12B는 GPU 1번에 그 서버 하나만 띄웠다(`-np 1 -c 8192`, `-ngl`은 4-2 표). 최대 메모리는 측정 중 1초마다 읽은 GPU 1번 사용량의 최댓값이다.
- 26B는 운영 서버(8000)를 이 작업에서 옵션을 바꾸지 않고 그대로 쟀다. 조건은 `--max-num-seqs 8`, Task 3 수집(harvest) 동시 실행이다. 대기열이 생길 수 있어 참고값이다.

| 시간 지표 | 26B-A4B AWQ (운영 공유) | 12B Q4_K_M | 12B Q4_K_M + MTP | 12B QAT Q4_0 | 12B QAT Q4_0 + MTP (-ngl 47) | 12B QAT Q4_0 + MTP (-ngl 48, 7.9GB 초과) |
|---|---:|---:|---:|---:|---:|---:|
| 첫 문장 중앙값 / 90% (3090) | 7.5 / 9.7초 | 23.5 / 29.3초 | 18.2 / 22.7초 | 8.8 / 10.7초 | 9.6 / 11.7초 | 8.9 / 12.0초 |
| 첫 문장 90% (4060 추정 ×2.68) | - | 78.6초 | 60.8초 | 28.6초 | 31.3초 | 32.1초 |
| 전체 중앙값 / 90% (3090) | 15.3 / 21.5초 | 45.1 / 63.0초 | 34.9 / 49.0초 | 16.6 / 23.0초 | 18.0 / 24.0초 | 16.0 / 23.9초 |
| 전체 90% (4060 추정 ×2.68) | - | 168.7초 | 131.3초 | 61.5초 | 64.4초 | 64.2초 |
| 표본 수 (answered/blocked/no_source/기타) | 60 (52/5/3/0) | 60 (53/5/2/0) | 60 (54/4/2/0) | 60 (54/3/3/0) | 60 (54/3/3/0) | 60 (54/3/3/0) |
| GPU 1번 최대 메모리 | - | 7774 MiB | 7770 MiB | 7708 MiB | 7782 MiB | 7912 MiB |

- 답변 완료 질문의 단계별 시간 중앙값(3090, 초)

| 설정 | 범위 결정 | 검색 | 문장 쓰기 | 일치 검사 | 문장 수 |
|---|---:|---:|---:|---:|---:|
| 26B (운영 공유) | 1.0 | 0.1 | 5.2 | 8.4 | 6 |
| 12B QAT | 1.9 | 0.1 | 5.0 | 8.3 | 6 |
| 12B QAT + MTP (`-ngl 47`) | 2.1 | 0.1 | 5.5 | 9.1 | 5 |
| 12B Q4_K_M (`-ngl 45`) | 3.8 | 0.1 | 15.2 | 21.8 | 6 |
| 12B Q4_K_M + MTP (`-ngl 43`) | 3.0 | 0.1 | 12.0 | 17.8 | 6 |

- 읽는 법
  - 12B QAT는 8GB 안에 다 들어가 3090에서 26B와 비슷한 시간이다. 그래도 첫 문장 90% 값이 3090에서 이미 10.7초로 10초 기준을 넘는다.
  - 12B Q4_K_M은 8GB에 다 들어가지 않는다. 층 3개와 출력층을 CPU가 계산해 QAT보다 2.7배쯤 느리다.
  - 시간의 대부분은 일치 검사(문장마다 판정 호출)와 문장 쓰기다.
    - 하네스의 모든 역할(범위·쓰기·판정·고치기)이 JSON schema 출력을 쓴다. llama.cpp는 이 문법 제약을 CPU에서 처리한다.
    - 병렬 측정 때 12B의 GPU 사용률이 24%쯤에 머문 것도 이 때문으로 본다(추정).

### 4-7. MTP 추측 디코딩

- 드래프트 헤드: `unsloth/gemma-4-12B-it-qat-GGUF`(`980b060`)의 `mtp-gemma-4-12B-it.gguf`. 서버에 `-md <헤드> --spec-type draft-mtp`를 더한다.
- 메모리: 같은 `-ngl`에서 MTP를 켜면 GPU 1번 사용량이 **304MiB** 는다(QAT `-ngl 48` 7,562→7,866MiB, Q4_K_M `-ngl 43` 7,402→7,706MiB, 대기 상태).
  - 그래서 7.9GB 안에 넣으려면 QAT는 `-ngl 47`, Q4_K_M은 `-ngl 43`으로 층을 더 CPU에 내려야 한다.
- 속도
  - 일반 답(질문 2개, 온도 0, 최대 300토큰) 생성 속도는 두 배쯤이다.
    - QAT: 51~55 → 84~116토큰/초
    - Q4_K_M: 11~12 → 25~29토큰/초
    - 드래프트 수락률은 48~69%다.
  - 하네스 전체 시간(4-6)
    - Q4_K_M: 줄었다(전체 90% 63.0 → 49.0초).
    - QAT: 줄지 않았다(23.0 → 24.0초). 출력이 짧은 JSON 호출이 대부분이고, MTP 자리를 내느라 층 하나와 출력층을 CPU로 내렸기 때문이다.
- 출력 동일성(온도 0, 같은 `-ngl`, 일반 답 2개 + JSON 판정 1개; 원본 `ai-core/outputs/harness-measure/mtp-same/`)
  - QAT: 3개 모두 달랐다. 125~414번째 글자부터 표현이 갈렸고(예: "순서가 있는 방식으로" → "선형적(Linear)으로"), 내용 방향은 같았다.
  - Q4_K_M: 2개 달랐다(216·233번째 글자부터 쉼표·어미 차이). 1개는 같았다.
  - JSON 판정의 결론(`verdict`)은 네 경우 모두 같았다(`unsupported`). 이유 문장은 달랐다.
  - 즉 MTP를 켜면 온도 0이어도 글자 단위로 같은 출력이 나오지 않는다. 검증 단계가 여러 토큰을 한 번에 계산해 수치 경로가 달라지는 것으로 본다(추정).

### 4-8. 다른 12B급 모델 거르기 (범위·판정만)

- 통과 기준: 범위 정확도와 판정 검출률이 둘 다 gemma 12B 중 나은 쪽(Q4_K_M: 87.8%, 68.5%)보다 5%p 넘게 낮지 않을 것(즉 82.8% 이상, 63.5% 이상)
- 같은 하네스·세트·설정(`--workers 4`, `-ngl 99 -np 4 -c 32768`, 사고 모드 인자 동일)으로 쟀다.

| 모델 | 범위 정확도 (직접/비교/범위 밖/후속) | 판정 검출률 | 오경보율 | 결과 |
|---|---|---:|---:|---|
| Midm 2.0 Base Q4_K_M(변환) | 15.0% (2.0 / 78.3 / 13.3 / 20.0) | 48.0% | 13.0% | 불통과 |
| Ministral 3 14B Q4_K_M | 72.8% (62.0 / 91.7 / 98.3 / 83.3) | 81.5% | 29.2% | 불통과(범위 15.0%p 낮음) |
| Kanana 1.5 8B Q4_K_M(변환) | - | - | - | 측정 불가 |

- Midm: 직접 질문의 98%에서 개념을 2개 이상 골랐다(정답 개념은 95.7%에 들어 있음). 범위 결정은 정답 개념 집합과 정확히 같아야 맞으므로 거의 다 틀렸다. 범위 밖 질문도 대부분 지도 안 개념으로 보냈다.
- Ministral: 검출률은 26B보다 높지만 오경보가 29.2%로, 맞는 문장 셋 중 하나쯤을 "확인 필요"로 돌린다. 직접 질문에서 개념을 2개 이상 고른 비율이 37.7%다(12B Q4_K_M 16.7%).
- Kanana: 측정 불가
  - 변환한 GGUF에서 `<|eot_id|>`(128009)가 생성 끝으로 처리되지 않고 글자로 출력된다.
  - 그래서 JSON schema 응답 끝에 그 글자가 남고, llama-server가 500("The model produced output that does not match the expected peg-native format")을 낸다.
  - GGUF의 `tokenizer.ggml.eos_token_id`는 128009로 맞게 들어갔고 `eot_token_id`는 비어 있다.
  - 규칙대로 고치지 않았다. 메타데이터를 덮어쓰면(`--override-kv`) 될 수도 있지만 확인하지 않았다.
- 두 모델 다 통과하지 못해 채점·답 전체·시간 측정은 하지 않았다.

### 4-9. 26B 판정기 교차 (바탕 모델 QAT)

- 답 전체(485문항)를 다시 쟀다. 범위·쓰기·고치기는 12B QAT(GPU 1번, `-np 4 -c 32768`)가 하고, 판정 역할만 26B(8000)로 보냈다(`--judge-url`).
  - 처음에는 판정 검출이 나은 Q4_K_M으로 시작했다가, 바탕 모델이 QAT로 정해져 10문항에서 멈추고 QAT로 다시 쟀다. 멈춘 값은 쓰지 않는다.
  - 26B로 가는 판정·노출 호출은 합쳐 동시 2개 이하로 묶었다.

| 지표 | 12B QAT (자기 판정) | 12B QAT + 26B 판정 | 26B (기준) |
|---|---:|---:|---:|
| 노출(26B 판정) | 5.9% (125/2126) | 0.19% (4/2102) | 0.1% (2/1967) |
| 확인 필요 비율 | 4.0% | 5.4% | 5.9% |
| 근거 없음 비율 | 16.6% | 16.6% | 16.5% |
| 원문 대체 비율 | 1.2% | 1.7% | 0.9% |
| 한자 섞인 문장 | 0 | 0 | 0 |
| 답 결과(answered/blocked/no_source/fallback/generation_failed) | 348/62/70/3/2 | 346/62/70/5/2 | 350/61/70/4/0 |

- 읽는 법
  - 판정만 26B로 바꾸면 노출이 5.9%에서 0.19%로 떨어진다. 확인 필요 비율은 4.0%에서 5.4%로 26B 수준에 가까워진다.
  - 즉 12B 하네스의 노출은 주로 판정(일치 검사) 역할에서 생긴다. 미세조정에서 판정 역할을 먼저 다룰 근거다.
  - 다만 이 0.19%는 같은 26B가 판정하고 다시 검사한 값이라 26B 자기 판정(0.1%)처럼 낮게 나오는 쪽이다(4-11). 판정을 26B만큼 할 때 기대할 수 있는 가장 좋은 쪽 값으로 본다.

### 4-10. 8-4 전환 판단

- 기준: 단계별 지표가 26B보다 5%p 이상 나쁘지 않음, 노출 2% 이하, 한자 0건, 첫 문장 90% 10초·전체 90% 30초(35초까지 허용).
- 시간은 4060 추정(×2.68)으로 판단한다. 3090 값만으로도 결론이 같은 경우는 그렇다고 적는다.

| 모델 | 단계별(26B 대비) | 노출(26B 판정) | 한자 | 시간(4060 추정 90%) | 판정 |
|---|---|---:|---:|---|---|
| **12B Q4_K_M** | 통과(가장 큰 차이 오경보 +2.9%p) | 5.5% ✗ | 0 | 첫 78.6초·전체 168.7초 ✗(MTP 60.8·131.3초 ✗) | **불통과**: 노출·시간 |
| **12B QAT Q4_0** | 판정 검출 64.3%(26B 71.1%, −6.8%p) ✗ | 5.9% ✗ | 0 | 첫 28.6초·전체 61.5초 ✗(MTP 31.3·64.4초 ✗) | **불통과**: 검출·노출·시간 |
| E4B Q4_K_M | - | - | - | - | 제외(사용자 결정) |
| E4B Q8_0 | - | - | - | - | 제외(사용자 결정) |
| E2B Q4_K_M | - | - | - | - | 제외(사용자 결정) |
| Midm 2.0 / Ministral 3 14B | - | - | - | - | 거르기 불통과(4-8) |
| Kanana 1.5 8B | - | - | - | - | 측정 불가(4-8) |

- 시간은 3090 값만으로도 두 12B 모두 불통과다.
  - QAT 첫 문장 90% 값이 3090에서 10.7초다. Q4_K_M은 29.3초다.
  - Q4_K_M은 일부 층이 CPU라 4060 보정이 맞지 않는다(4-4). 그래도 3090 값이 이미 기준을 넘는다.
- 결론: 지금 하네스 그대로는 12B 두 파일 모두 8-4 기준을 통과하지 못한다.
  - 품질 쪽 걸림돌은 노출이다(5.5~5.9%, 기준 2%). 판정 검출률이 26B보다 낮아 틀린 문장이 "확인 필요" 없이 나간다.
  - 시간 쪽 걸림돌은 문장마다 판정을 부르는 일치 검사와 JSON 출력이다.

**바탕 모델 결정: 12B QAT Q4_0 (사용자 결정, 2026-10-29)**

- 설계의 선택 순서(노출 → 판정 → 8GB → 시간)를 그대로 쓰면 판정 검출 4.2%p 차이(68.5% 대 64.3%)로 Q4_K_M이다.
- 그러나 8GB 조건에서 Q4_K_M은 다 올라가지 않아 일부 층을 CPU가 계산한다. 그래서 QAT보다 2.7배 느리다(3090 단독, 전체 90% 63.0초 대 23.0초).
- 판정 차이는 미세조정으로 메울 수 있는 대상이지만, 속도 차이는 파일 크기에서 오는 구조적 차이다. 그래서 시간을 우선해 **QAT Q4_0**을 바탕 모델로 정했다.
- 근거 표

| 항목 | 12B Q4_K_M | 12B QAT Q4_0 |
|---|---:|---:|
| 범위 정확도 | 87.8% | 85.8% |
| 판정 검출률 / 오경보율 | 68.5% / 3.6% | 64.3% / 1.6% |
| 채점 거짓 통과 / 거짓 불통과 | 5.2% / 33.6% | 4.9% / 38.0% |
| 노출(26B 판정) | 5.5% | 5.9% |
| 8GB 안 `-ngl` | 45(층 3개+출력층 CPU) | 99(전부 GPU) |
| 최대 메모리(단독) | 7,774MiB | 7,708MiB |
| 첫 문장 / 전체 90%(3090 단독) | 29.3 / 63.0초 | 10.7 / 23.0초 |

### 4-11. 한계

- 판정 세트의 "맞는 문장"은 26B 하네스가 verified로 낸 문장에서 왔다(3-1). 그래서 26B 오경보율(0.7%)은 낮게 나오는 쪽으로 기울어 있다.
- 노출 지표는 26B 판정기로 쟀다.
  - 26B 자신의 노출(0.1%)은 같은 모델이 다시 판정한 값이라 낮게 나온다.
  - 26B 판정기의 틀린 문장 검출률이 71%이므로, 다른 모델의 노출도 실제보다 낮게 잡힌 하한이다.
  - 사용자 표본 기준 값은 아직 "대기"다.
- 정확도는 여러 모델을 GPU 1번에 함께 올린 병렬 부하에서 쟀다. 12B Q4_K_M은 문장 쓰기 호출 28번이 30초 제한을 넘었고, 그중 8건은 재시도도 넘어 생성 실패가 됐다.
- E4B Q8_0 답 전체는 107개만 재고 사용자 결정으로 멈췄다. 세트 앞쪽(직접 질문 위주)만 들어 있어 다른 모델 값과 직접 비교하지 않는다.
- 26B 시간은 운영 서버를 다른 작업과 함께 쓸 때 잰 참고값이다(`--max-num-seqs 8`, Task 3 수집 동시 실행). 26B의 GPU 메모리는 운영 서버라 따로 재지 않았다.
- 4060 값은 공개 게시판의 다른 모델(Llama 2 7B) 비율로 옮긴 추정이다. 데스크톱 4060 실측이 없어 4060 Ti 값을 대역폭 비로 보정했다. CPU로 일부 층을 내린 설정에는 맞지 않는다.
- 메모리 기준 7,900MiB는 화면 출력 몫을 어림한 값이다. 화면을 쓰지 않는 8GB 카드라면 Q4_K_M은 `-ngl 47`(8,056MiB)까지 올릴 수 있다.
- MTP 동일성은 질문 3개만 확인했다.
- 26B 판정기 교차(4-9)는 판정 역할과 노출 검사가 같은 26B라 노출 값이 낮게 나오는 쪽이다.
