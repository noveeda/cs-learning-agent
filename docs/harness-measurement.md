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

- 세트 규칙
  - 문단 묶음은 색인과 같은 `rag.index.chunk_section`으로 자른다. 그래서 정답 id가 색인의 문단 id와 그대로 맞는다.
  - 300자 미만 묶음(장 머리말·한 줄 설명)은 질문 재료에서 뺀다.
  - 질문 생성은 temperature 0, 추론 끔이다. 잘린 출력, 200자를 넘는 출력, 한자가 섞인 출력은 그 문단을 세트에서 뺀다.
- 회수율은 운영 검색기(`Retriever.passages`)를 그대로 거쳐 잰다. 개념 꼬리표 거르기와 라이선스 계열 규칙이 들어간 값이다. 기준 점수는 끈다(`min_score=-1`).
- 선택 규칙(계획): 상위 3개 회수율이 높은 모델을 고른다. 차이가 2%p 이내면 작은 모델을 고른다.
  `RAG_MIN_SCORE`는 고른 모델의 정답 점수 분포 하위 5% 값이다.

### 2-2. 결과 (2026-10-28, 초안 대응표 기준)

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
#     색인과 임베딩은 2장에서 고른 값(bge-m3, RAG_MIN_SCORE=0.51)
RAG_EMBED_MODEL=bge-m3 RAG_MIN_SCORE=0.51 .venv/bin/python -m eval.harness_sets_judge harvest \
  --index $HARNESS_SETS_DIR/index-bge-m3.sqlite --limit 120
# (3) 판정 세트: verified 문장 절반은 그대로, 절반은 망가뜨림 → judge.jsonl, 표본 시트 ~/cs-handoff/judge-sample.md
.venv/bin/python -m eval.harness_sets_judge perturb --index $HARNESS_SETS_DIR/index-bge-m3.sqlite --map-commit 69edcaf
# (4) 채점 세트: Mohler 사람 채점 원본(영어)과 검수한 루브릭 → grading.jsonl
.venv/bin/python -m eval.harness_sets_judge grading \
  --data /home/infoserver/cs-eval-data/raw/mohler/open_ended.jsonl \
  --rubrics /home/infoserver/cs-eval-data/processed/mohler/rubrics.jsonl
```

- 절 대응표가 검수로 바뀌면 색인을 다시 만든 뒤 (2)·(3)을 다시 실행한다. 이전 `harvest-logs/`는 지우고 실행한다.
- 지금 세트(3-2)는 **초안 색인**으로 만들었다. 확정 대응표(PR #113, `bbbce57`)의 색인이 `RAG_DATA_DIR/index.sqlite`에 생기면 `--index`를 빼고 다시 만든다.

  ```bash
  rm -rf $HARNESS_SETS_DIR/harvest-logs
  RAG_EMBED_MODEL=bge-m3 RAG_MIN_SCORE=0.51 .venv/bin/python -m eval.harness_sets_judge harvest --limit 120
  .venv/bin/python -m eval.harness_sets_judge perturb --map-commit bbbce57
  ```
- (1)·(4)는 대응표와 무관하다.

### 3-2. 결과 (2026-10-29, 초안 대응표 PR #113 `69edcaf` 기준 색인)

| 세트 | 행 | 구성 | 정답의 근거 |
|---|---:|---|---|
| `scope.jsonl` | 485 | 직접 305(개념당 5), 비교 60, 지도 밖 60(그중 코드 요청 20), 후속 60 | 질문을 만든 규칙(개념·선수 관계 쌍·지도 밖·직전 개념) |
| `judge.jsonl` | 590 | 맞음 295, 틀림 295(덧붙이기 109, 개념 바꾸기 84, 반대로 말하기 80, 숫자 바꾸기 22) | 망가뜨린 방식 |
| `grading.jsonl` | 2,273 | 루브릭이 있는 81문항 전체, 사람 통과(평균 4.0 이상) 1,639 | 사람 채점 점수 |

- 범위 세트
  - 비교 질문은 선수 관계(개념 → 선수 개념) 82쌍 중 60쌍에서 만들었다.
  - 문항 은행과 같은 문장, 한자가 섞인 질문은 버리고 다시 만들었다.
- 판정 세트
  - 맞는 문장은 26B 하네스가 verified로 낸 문장(근거 문단 1개) 590개다. harvest 120질문의 결과는 답변 110, 근거 없음 7, 원문 대체 3이다.
  - 숫자 바꾸기·반대로 말하기는 코드 규칙으로, 덧붙이기·개념 바꾸기는 26B로 했다.
  - 숫자가 있는 문장은 숫자 바꾸기를 먼저 시도한다. 영문자에 붙은 숫자(`L1`, `IPv4`)는 이름이라 바꾸지 않는다.
- 채점 세트: `split`(dev·report)은 `eval/mohler.py`의 문항 묶음 분할과 같다.
- **사용자 표본 확인 요청**
  - `~/cs-handoff/scope-sample.md`: 개념당 직접 질문 1개 + 비교·지도 밖·후속 각 5개
  - `~/cs-handoff/judge-sample.md`: 맞음 25 + 틀림 25

### 3-3. 한계와 발견

- **채점 측정은 영어 답 기준이다.** Mohler는 번역하지 않았다. 번역도 모델 생성이라 검수 대상이 늘어나기 때문이다(2026-10-29 코디네이터 결정). 한국어 답에서의 거짓 불통과는 석사생 채점 세트가 생기면 다시 잰다.
- **판정 세트는 초안 대응표 기준이다.** 맞는 문장이 초안 색인의 문단에서 나왔다. 대응표가 바뀌면 3-1의 (2)·(3)으로 다시 만든다.
- **26B와 strict JSON schema의 `:` 출력**
  - 문자열 하나(`{"sentence": ...}`)를 schema로 묶으면 26B가 `{"sentence":": "}`를 내는 경우가 많았다. 같은 요청을 schema 없이 보내면 정상 문장이 나왔다.
  - 그래서 교란 요청은 schema 없이 받고 결과를 코드로 검사한다.
  - 하네스의 생성·수정 역할에서 나온 `:` 문장(PR #115 실측, harvest 중 수정 역할의 `:` 응답)도 같은 원인으로 보인다.
