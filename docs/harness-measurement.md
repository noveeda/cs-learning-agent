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
