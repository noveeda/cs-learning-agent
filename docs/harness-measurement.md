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

```sh
# bge-m3 → 20310
CUDA_VISIBLE_DEVICES= nohup ~/llama.cpp/build/bin/llama-server -m ~/.cache/cs-embed/bge-m3-q8_0.gguf \
  --embeddings --pooling cls -ngl 0 -t 16 -c 8192 -b 8192 -ub 8192 \
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
# (2) 후보마다 색인을 다시 만들고 회수율을 잼(위 표의 환경 변수를 후보별로 바꿔 반복)
.venv/bin/python -m rag.index
.venv/bin/python -m eval.harness_retrieval --set outputs/harness-sets/retrieval.jsonl
```

- 세트 규칙
  - 문단 묶음은 색인과 같은 `rag.index.chunk_section`으로 자른다. 그래서 정답 id가 색인의 문단 id와 그대로 맞는다.
  - 300자 미만 묶음(장 머리말·한 줄 설명)은 질문 재료에서 뺀다.
  - 질문 생성은 temperature 0, 추론 끔이다. 잘린 출력, 200자를 넘는 출력, 한자가 섞인 출력은 그 문단을 세트에서 뺀다.
- 회수율은 운영 검색기(`Retriever.passages`)를 그대로 거쳐 잰다. 개념 꼬리표 거르기와 라이선스 계열 규칙이 들어간 값이다. 기준 점수는 끈다(`min_score=-1`).
- 선택 규칙(계획): 상위 3개 회수율이 높은 모델을 고른다. 차이가 2%p 이내면 작은 모델을 고른다.
  `RAG_MIN_SCORE`는 고른 모델의 정답 점수 분포 하위 5% 값이다.

### 2-2. 결과

- 절 대응표(Task 2) 병합 뒤 측정해 채운다.
