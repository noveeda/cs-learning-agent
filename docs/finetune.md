# 12B 하네스 역할 미세조정 학습 데이터와 측정 기록

- 1~4절은 학습 데이터 생성(계획 Task 3), 5~8절은 측정 도구 확장과 미세조정 전 기준값(계획 Task 4)이다.

- 설계: `docs/superpowers/specs/2026-10-29-finetune-12b-design.md`(4·5장, 7-1)
- 계획: `docs/superpowers/plans/2026-10-29-finetune-12b.md` Task 3
- 생성기: `ai-core/finetune/build_data.py`
- 결과 위치(Git 밖): `/home/infoserver/cs-learning-agent/ai-core/outputs/finetune/`

## 1. 무엇을 만드는가

| 역할 | 정답 출처 | 1차 목표 | 제외 개념 몫 목표(× 8/61) |
|---|---|---:|---:|
| judge | 구성상 정답. 26B 하네스가 verified로 낸 문장은 supported, 그 문장을 망가뜨린 것은 unsupported, 덧붙인 것은 partial. 근거 구절은 원래 문장의 26B 근거 구절 | 6,000(맞음·틀림 반반, 틀림 중 덧붙이기 40%) | 약 790 |
| ground | 26B 하네스 답 중 모든 문장이 수정 없이 verified이고, 26B 별도 검사도 모두 통과한 답의 ground 호출 | 3,000 | 약 390 |
| repair | 수정 뒤 재판정이 supported로 끝난 repair 호출 | 1,000 | 약 130 |
| scope | 구성상 정답. 그 개념용으로 만든 질문, 지도 밖·코드 요청은 none, 후속 질문은 직전 개념 | 3,000 | 약 390 |

- 모든 행은 운영 하네스와 같은 지시문(`PROMPTS[role]`), 같은 user JSON, 공백 없는 한 줄 assistant JSON이다(`finetune/examples.py`).
- 각 역할의 5%를 seed 고정으로 검증용(`valid.jsonl`)으로 뗀다.
- 1차 학습 파일(`train.jsonl`, `valid.jsonl`)에는 제외 개념 8개가 나오지 않는다.
  - 질문, 정답 개념, 직전 개념, 판정·수정 문장, ground·repair 출력은 제외 개념 ID와 이름 조각으로 검사한다.
  - 근거 문단은 절 대응표로 검사한다. 제외 개념에 대응된 절의 문단은 쓰지 않는다. 학습 가능한 절의 영어 문단에 우연히 나오는 낱말(예: subtree 안의 btree, 이진 탐색 트리 절의 linked list)은 남는다. 판정 정답의 근거 구절도 이 문단에서 따온 것이라 같다.
  - 제외 개념에 닿는 행은 `holdout_{judge,ground,repair,scope}.jsonl`로 따로 모은다.
  - 이 파일들은 배포용 2차 학습(61개념, 설계 7-1)에 쓴다.

## 2. 실행

`ai-core` 폴더에서 아래 환경 변수를 둔다.

```sh
export RAG_DATA_DIR=/home/infoserver/cs-learning-agent/ai-core/data/rag
export HARNESS_SETS_DIR=/home/infoserver/cs-learning-agent/ai-core/outputs/harness-sets
OUT=/home/infoserver/cs-learning-agent/ai-core/outputs/finetune
PY=.venv/bin/python
```

| 단계 | 명령 | 결과 |
|---|---|---|
| 1. 질문 | `$PY -m finetune.build_data --stage questions --out $OUT` | `questions.jsonl` |
| 2. 하네스 녹화 | `$PY -m finetune.build_data --stage harvest --out $OUT --limit 1000 --workers 8` | `records.jsonl`, `ground.jsonl`, `repair.jsonl`, `holdout_{ground,repair}.jsonl` |
| 3. 판정 | `$PY -m finetune.build_data --stage judge --out $OUT --workers 8` | `judge.jsonl`, `holdout_judge.jsonl`, `perturb-cache.jsonl` |
| 4. 범위 | `$PY -m finetune.build_data --stage scope --out $OUT` | `scope.jsonl`, `holdout_scope.jsonl` |
| 5. 분할 | `$PY -m finetune.build_data --stage split --out $OUT` | `train.jsonl`, `valid.jsonl` |
| 6. 다시 거르기 | `$PY -m finetune.build_data --stage refilter --out $OUT --measured <scope.jsonl> <retrieval.jsonl> <holdout-scope.jsonl>` | `holdout_*.jsonl`을 제외 개념 측정 세트로 한 번 더 거름 |

- 질문 단계는 61개념마다 직접 질문 110개, 선수 관계 쌍마다 비교 질문 6개, 지도 밖 250개, 코드 요청 120개, 후속 질문 430개를 목표로 한다. 다시 실행하면 모자란 만큼만 더 만든다.
- 하네스 단계는 시작 전 26B 서버를 확인하고, 최근 40건 중 오류가 10건 이상이면 남은 질문을 건너뛰고 실패로 끝난다. 오류 난 질문은 기록하지 않아 다시 실행하면 이어 녹화한다.
- 하네스 단계는 이미 녹화한 질문을 건너뛴다. 목표 분량에 닿을 때까지 `--limit`을 주어 이어서 돌린다. 질문 순서를 seed로 섞어 두어 일부만 돌려도 개념이 고르게 섞인다.
- 26B 동시 요청은 `--workers`(상한 12, 26B 서버 자체는 max-num-seqs 8)개다. 판정 단계의 26B 교란 결과는 `perturb-cache.jsonl`에 남아 다시 실행하면 재사용한다.
- 조건(seed, 26B 모델, 절 대응표 커밋, 제외 개념과 이름 조각, 측정 세트, 단계별 행 수와 거른 이유)은 `meta.json`에 쌓인다.

### 26B 두 대로 나눠 녹화하기

`--shard K/N`은 seed로 섞은 질문 순서에서 인덱스 `i % N == K`인 질문만 녹화한다. 기본값 `0/1`은 전체다. 서버는 `VLLM_BASE_URL`로 고른다.

1. 지금 돌고 있는 녹화를 멈춘다. 이미 쓴 행은 그대로 둔다.
2. 두 번째 폴더를 만들고 `questions.jsonl`과 지금까지의 `records.jsonl`을 복사한다. 이미 녹화한 질문을 두 번째 서버가 다시 녹화하지 않게 하기 위해서다.
   ```sh
   OUT_B=/home/infoserver/cs-learning-agent/ai-core/outputs/finetune-b
   mkdir -p $OUT_B && cp $OUT/questions.jsonl $OUT/records.jsonl $OUT_B/
   ```
3. 두 프로세스를 따로 돌린다.
   ```sh
   $PY -m finetune.build_data --stage harvest --out $OUT --shard 0/2 --workers 12 --limit 1000
   VLLM_BASE_URL=http://127.0.0.1:20340/v1 $PY -m finetune.build_data --stage harvest --out $OUT_B --shard 1/2 --workers 12 --limit 1000
   ```
4. 둘 다 끝나면 두 번째 폴더의 새 행만 원본에 붙이고(같은 id는 건너뜀), 녹화 없이 ground·repair 행을 다시 만든다.
   ```sh
   $PY -c "import json,sys; a,b=sys.argv[1:]; seen={json.loads(l)['id'] for l in open(a)}; new=[l for l in open(b) if json.loads(l)['id'] not in seen]; open(a,'a').writelines(new); print(len(new),'행 추가')" $OUT/records.jsonl $OUT_B/records.jsonl
   $PY -m finetune.build_data --stage harvest --out $OUT --limit 0
   ```
5. `--limit 0`으로 남은 질문이 0개인지 확인한다. 남았으면(오류로 멈춘 경우) 원본 폴더에서 `--shard` 없이 이어 녹화한다.

## 3. 거르는 규칙

| 이유 | 뜻 | 처리 |
|---|---|---|
| measured | 측정 세트(범위·검색, 나중에 제외 개념 세트) 질문과 공백·문장부호를 지운 뒤 같은 질문, 또는 측정 판정 세트와 같은 문장 | 버림 |
| han | 질문·출력·교란 문장에 한자가 섞임 | 버림 |
| holdout | 제외 개념 ID, 제외 개념에 대응된 절의 문단, 제외 개념 이름 조각(예: 연결 리스트, BFS, 캐시, 정규화)이 나옴 | 제외 개념 몫 파일로 옮김 |
| not_answered | 하네스 답이 차단·근거 없음·원문 대체·오류로 끝남 | ground에서 버림 |
| not_all_verified | 답에 확인 필요 문장이나 수정한 문장이 있음 | ground에서 버림 |
| audit | 26B 별도 검사(측정 도구의 노출 지표 검사와 같은 호출)에서 통과하지 못한 문장이 있음 | ground에서 버림 |
| rejudge_failed | 수정 뒤 재판정이 supported가 아님 | repair에서 버림 |
| gateway | 녹화 응답이 운영 게이트웨이 parse를 다시 통과하지 못하거나 하네스가 받아들인 문장과 다름 | 버림 |
| duplicate | 같은 맞는 문장이 이미 있음 | judge 후보에서 버림 |

- 제외 개념 이름 조각은 개념 제목 조각, 개념 ID, 다른 이름(`EXTRA_TERMS`)에서 만든다. 4자 이하 영문 조각(osi, bfs)은 단어 경계로만 찾는다.
- 범위 행의 user JSON에는 운영과 같게 61개념 목록 전체가 들어간다. 이 목록은 질문·정답이 아니므로 제외 개념 검사 대상이 아니다.

## 4. 결과 (2026-10-29, seed 0)

- 26B: `gemma-4-26b-a4b-it-awq`, 리비전 `0ef577a`, 절 대응표 커밋 `d7c313f`
- 측정 세트: `harness-sets/scope.jsonl`, `retrieval.jsonl`(질문), `judge.jsonl`(판정 문장)
- 녹화: 운영 26B(8000) 단독으로 시작했다. 나머지는 같은 리비전의 두 번째 26B(5090, 20340, PyTorch 샘플러)와 나눠 `--shard`로 녹화했다. 어느 서버가 어느 질문을 녹화했는지는 각 폴더의 `harvest.log`에 있다.
  - 걸린 시간: 08:28~12:00 UTC(중간에 26B 재시작으로 멈춘 시간 포함). 판정 단계 7분, 범위·분할은 1분 이내
  - 26B 재시작 동안 쌓인 오류 기록 6,000건은 지우고 다시 녹화했다. 그 뒤 오류는 0건이다.

### 4-1. 역할별 행 수

| 역할 | 1차(train + valid) | train | valid | 1차 목표 | 제외 개념 몫 | 제외 개념 몫 목표 |
|---|---:|---:|---:|---:|---:|---:|
| judge | 6,000 | 5,700 | 300 | 6,000 | 786 | 약 790 |
| ground | 3,088 | 2,934 | 154 | 3,000 | 1,263 | 약 390 |
| repair | 616 | 585 | 31 | 1,000 | 136 | 약 130 |
| scope | 2,998 | 2,848 | 150 | 3,000 | 390 | 약 390 |
| 합계 | 12,702 | 12,067 | 635 | 13,000 | 2,575 | |

- judge 1차 6,000행: 맞음 3,000, 틀림 3,000(덧붙이기 1,200 = 40%, 개념 바꾸기 794, 부정 817, 숫자 189)
- judge 제외 개념 몫 786행: 맞음 393, 덧붙이기 157, 개념 바꾸기 120, 부정 93, 숫자 23
- scope 1차: 직접 1,900, 비교 360, 지도 밖·코드 368, 후속 370. 지도 밖 질문 370개 중 2개는 제외 개념 이름이 들어 있어 빠졌다(제외 개념 몫 범위 행에는 지도 밖 종류가 없음).
- scope 제외 개념 몫: 직접 250, 비교 80, 후속 60
- repair는 목표의 62%다. 녹화 가능한 직접·비교 질문 7,200개를 모두 돌렸고, 수정 3,156회 중 재판정이 supported로 끝난 것이 752회(24%)뿐이었다.
- ground 제외 개념 몫이 목표보다 많은 것은 `캐시`, `지역성`, `파이프라인` 같은 이름 조각이 다른 개념 답에도 자주 나와 제외 개념 몫으로 옮겨졌기 때문이다.

### 4-2. 녹화 결과

| 결과 | 질문 수 |
|---|---:|
| answered | 6,567 |
| no_source | 514 |
| fallback | 86 |
| blocked | 33 |
| 합계 | 7,200 |

- 26B 호출: scope 7,200, ground 6,653, judge 41,081, repair 3,156
- 판정 단계 교란 호출 2,806회. 그중 개념 바꾸기 122회는 26B 출력이 JSON 한 개가 아니어서 실패했고, 다음 교란 방식으로 넘어갔다.

### 4-3. 거른 이유별 개수

| 역할 | measured | han | not_answered | not_all_verified | audit | gateway | rejudge_failed | duplicate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ground(녹화 7,200건 기준) | 0 | 0 | 633 | 2,090 | 44 | 82 | | |
| repair(수정 호출 3,156건 기준) | 0 | 0 | | | | 0 | 2,404 | |
| judge(맞는 문장 후보 기준) | 699 | 0 | | | | | | 8,648 |
| scope(질문 기준) | 0 | 0 | | | | | | |

- judge의 measured 699건은 측정 판정 세트와 같은 문장이거나 측정 질문에서 나온 문장이다. duplicate는 같은 문장이 여러 답에 나온 경우다.
- 거르고 남은 judge 후보는 1차 19,076개, 제외 개념 몫 5,968개다. 이 중 seed 순서로 목표 분량만 썼다.
- 측정 세트와 같은 질문이 0건인 것은 질문 단계에서 이미 측정 질문과 같은 질문을 만들지 않았기 때문이다.
- 제외 개념 추가 측정 세트(Task 4의 `holdout-scope.jsonl`)가 생기면 6단계(`--stage refilter`)로 `holdout_*.jsonl`을 한 번 더 거른다. 아직 하지 않았다.

## 5. 측정 도구 확장(Task 4) 개요

- 이 절부터는 측정 도구 확장(계획 Task 4)의 결과와 미세조정 전후 비교 기준을 적는다.
- 측정 원본은 Git 밖에 둔다.
  - 측정 세트: `ai-core/outputs/harness-sets/`
  - 보정 시험 원본: `ai-core/outputs/finetune/astra-calibration/`
  - 측정 결과: `ai-core/outputs/harness-measure/`

### 5-1. 제외 개념 추가 측정 세트

- 대상: 제외 개념 8개(`ai-core/content/finetune/holdout.json`, seed 1)
  - 과목마다 2개다. OS는 절 공유 때문에 제외 개념이 없다(holdout.json `skipped_subjects`).
- 파일: `ai-core/outputs/harness-sets/holdout-scope.jsonl` **240행**(8개념 × 직접 질문 30개)
  - 행 ID는 `holdout-scope-0000`부터다. 기존 범위 세트(`scope-0000`…)와 겹치지 않는다.
- 만든 모델: gemma-4-26B(`gemma-4-26b-a4b-it-awq`, 운영 8000번 읽기 호출). 조건은 `holdout-scope.meta.json`에 있다.
- 만든 명령(ai-core 폴더)

```sh
python -m eval.harness_sets_judge scope --only-concepts content/finetune/holdout.json \
  --per-concept 30 --out outputs/harness-sets/holdout-scope.jsonl
```

- 확인한 것
  - 개념마다 30행이다.
  - 세트 안 중복(정규화 기준) 0개, 한자 섞인 질문 0개다.
  - 기존 범위 세트·문항 은행과 같은 질문은 공백 기준으로 버렸다.
  - 정규화(공백·문장부호 제거) 기준으로는 기존 범위·검색 세트와 같은 질문이 1개 있다.
    - 둘 다 측정 세트라 학습 오염은 아니다. 학습 데이터 생성(Task 3)은 두 세트를 모두 `measured_questions`에 넣어 거른다.

## 6. 외부 검사 모델 보정 시험

- 목적: 노출 지표를 26B와 다른 계열 모델로도 잰다. 이때 쓸 검사 모델을 고른다.
- 표본: 판정 세트(`judge.jsonl`, 정답이 교란 방식으로 정해짐)에서 seed 0으로 100행을 뽑았다(맞음 50, 틀림 50).
- 방법
  - 두 모델에 같은 40행 배치로 보냈다(`python -m eval.astra_audit calibrate --model <모델>`).
  - 보낸 것은 지시문, 교재 문단, 모델 문장뿐이다.
  - codex는 빈 임시 폴더에서 읽기 전용·세션 미저장·사용자 설정 무시로 실행했다.
- 결과(2026-10-29)

| 모델 | 틀린 문장 검출률 | 오경보율 | 검사 실패 |
|---|---|---|---|
| gpt-6.1-sol | 0.90 (45/50) | 0.02 (1/50) | 0 |
| gpt-6-astra | 0.90 (45/50) | 0.04 (2/50) | 0 |

- 교란 방식별 검출(두 모델 같음)
  - 덧붙이기 18/22
  - 개념 바꾸기 14/15
  - 반대로 말하기 12/12
  - 숫자 바꾸기 1/1
- 두 모델의 판정이 100행 중 99행에서 같았다.
- **결정: 기본 검사 모델은 gpt-6.1-sol**
  - 기준: sol이 두 지표 모두 astra와 3%p 이내이면 sol을 쓴다.
  - 검출률 차이는 0%p, 오경보율 차이는 2%p다.
  - `eval.astra_audit.DEFAULT_MODEL`에 반영했다. `--astra-model`로 바꿀 수 있다.
- 한계: 덧붙이기 4개는 두 모델 모두 맞다고 봤다. 외부 검사 노출 지표는 덧붙이기 쪽을 낮게 볼 수 있다.

## 7. 측정 도구에 더한 것

- `eval.harness_measure`
  - `--extra-scope PATH`: 범위·생성 측정에 세트를 덧붙인다. 제외 개념 추가 세트를 넣는 데 쓴다.
  - `--holdout PATH`: 학습 개념과 제외 개념 표를 따로 낸다(`report.md`, `metrics-by-group.json`).
    - 행의 개념은 concept → expected(정답) → concepts(흐름이 고른 개념) 순으로 본다.
  - `--astra`, `--astra-model`: 노출 대상 문장(답변 완료 답의 확인 필요 표시 없는 문장)을 외부 검사 모델로도 판정한다.
    - 결과는 행의 `astra` 필드와 `exposure_astra` 지표로 남는다.
    - 검사 실패는 불일치로 세지 않고 `errors`로 따로 센다.
  - 판정 지표에 교란 출처별 검출률(`by_origin`)을 더했다.
    - rule: 숫자·부정
    - model: 덧붙이기·개념 바꾸기
  - `bootstrap_diff(before, after)`: 미세조정 전후 비율 차이의 95% 구간이다(2000번 복원 추출, seed 0).
- 생성 행(answer)에 `expected`, `concept`, `passages`(키 → 문단 ID)를 더 남긴다.
  - 이 필드가 없는 예전 측정 행은 외부 검사를 하지 않는다.

## 8. 미세조정 전 기준값 (2026-10-29)

- 바탕 모델: 12B QAT Q4_0(`gemma-4-12b-it-qat-q4_0.google.gguf`). 사용자 결정이고 근거는 `docs/harness-measurement.md` 4절에 있다.
- 이 값이 Task 6(미세조정 뒤 측정)의 비교 기준이다.
- 결과 원본(Git 밖): `ai-core/outputs/harness-measure/12b-qat-base/`
  - `rows.jsonl`, `metrics.json`, `metrics-by-group.json`, `report.md`, `meta.json`, `gpu.csv`, `run.log`
- 조건
  - 서버: 이 서버 GPU 1번 llama-server 20324. 옵션은 Task 11과 같다(`-ngl 99 -fa on -np 4 -c 32768`).
  - 판정 역할도 12B가 한다(`--judge-url` 없음). 미세조정 대상이 판정 역할을 포함하기 때문이다.
  - 노출 지표 26B 판정기: 운영 8000 읽기 호출, `--prod-slots 2`
  - 외부 검사: gpt-6.1-sol(`--astra`), 검사 실패 0
  - 세트: 기존 측정 세트 전체와 제외 개념 추가 세트 240행(`--extra-scope`), `--holdout content/finetune/holdout.json`
  - 걸린 시간: 3시간 53분(12:57~16:50). GPU 1번 최대 17,227MiB(`-np 4 -c 32768` KV 캐시 포함)
- 명령(ai-core 폴더, 환경 변수는 `docs/harness-measurement.md` 1-3절과 같음)

```sh
python -m eval.harness_measure --base-url http://127.0.0.1:20324/v1 --model gemma-4-12B-QAT-Q4_0 \
  --tasks scope,judge,grading,answers --gpu 1 \
  --extra-scope $HARNESS_SETS_DIR/holdout-scope.jsonl --holdout content/finetune/holdout.json --astra \
  --out outputs/harness-measure/12b-qat-base
```

### 8-1. 전체

| 지표 | 값 |
|---|---:|
| 범위 정확도 (725행) | 85.5% |
| 판정 검출률 (616행) | 64.3% |
| 판정 검출률 · rule(숫자·부정, 96행) | 83.3% |
| 판정 검출률 · model(덧붙이기·개념 바꾸기, 212행) | 55.7% |
| 판정 오경보율 | 2.3% |
| 채점 거짓 통과율 (2,273행) | 4.7% (30/634) |
| 채점 거짓 불통과율 | 37.5% (615/1639) |
| 노출(26B 판정) | 5.6% (201/3606) |
| 노출(외부 검사 sol) | 4.9% (176/3606) |
| 확인 필요 비율 | 3.6% |
| 근거 없음 비율 | 11.2% |
| 원문 대체 비율 | 0.6% |
| 한자 섞인 문장 | 0 |
| 답 결과(answered/blocked/no_source/fallback/generation_failed) | 583/64/74/3/1 |

### 8-2. 학습 개념 / 제외 개념

| 지표 | 학습 개념 | 제외 개념 |
|---|---:|---:|
| 범위 행 수 | 424 | 301 |
| 범위 정확도 | 85.4% | 85.7% |
| 노출 판정 문장 수 | 1,785 | 1,821 |
| 노출(26B 판정) | 6.5% | 4.7% |
| 노출(외부 검사 sol) | 4.5% | 5.2% |
| 확인 필요 비율 | 4.4% | 2.8% |

- 제외 개념 쪽 301행은 추가 세트 240행에, 기존 세트에서 정답 개념이 제외 개념인 61행을 더한 것이다.
- 판정·채점 세트 행에는 개념이 없어 모두 학습 쪽에 들어간다. 이 표는 범위·생성 지표만 비교한다(7절).
- 미세조정 전이라 두 쪽 모두 학습하지 않은 상태다. 그래서 차이는 표본 차이다.
  - 추가 세트는 직접 질문만 있고 거의 모두 답변 완료(235/240)다. 기존 세트에는 지도 밖·코드 요청·근거 없음 질문이 섞여 있다.
- Task 6은 같은 조건으로 다시 재서 쪽마다 `bootstrap_diff`로 전후 차이의 95% 구간을 낸다.
  - 학습 개념 쪽만 좋아지고 제외 개념 쪽이 그대로면 문단을 미리 본 효과로 읽는다.

### 8-3. Task 11 측정과 맞춰 보기

- 기존 세트 부분은 Task 11의 12B QAT 측정(`outputs/harness-measure/12b-qat`)과 같은 조건이다. 값이 거의 같아 측정이 재현된다.

| 지표 | Task 11 (12b-qat) | 이번 기존 세트 부분 |
|---|---:|---:|
| 범위 정확도 (485행) | 85.8% | 85.8% |
| 판정 검출률 | 64.3% | 64.3% |
| 판정 오경보율 | 1.6% | 2.3% |
| 채점 거짓 통과율 | 4.9% | 4.7% |
| 노출(26B 판정) | 5.9% (125/2126) | 6.1% (131/2143) |
| 노출(외부 검사 sol) | — | 4.5% (96/2143) |

- 추가 세트 부분(240행)만 보면 범위 정확도 85.0%, 노출 26B 4.8%(70/1463), sol 5.5%(80/1463)다.

### 8-4. 두 검사기의 일치

- 노출 대상 3,606문장에서 26B와 sol의 판정

| | sol 맞음 | sol 불일치 |
|---|---:|---:|
| 26B 맞음 | 3,250 | 155 |
| 26B 불일치 | 180 | 21 |

- 두 검사기가 같은 문장을 불일치로 본 것은 21개뿐이다. 노출률은 비슷하지만(5.6%, 4.9%) 서로 다른 문장을 잡는다.
- 그래서 Task 6에서는 두 노출 지표를 따로 비교한다. 한쪽만 좋아지면 그 검사기 쪽으로 맞춰 배웠을 수 있다고 본다.
- 한계
  - 보정 시험(6절)은 교란 문장이라 두 모델이 99/100행에서 같았다. 실제 12B 문장에서는 판정이 이만큼 갈린다.
  - 어느 쪽이 맞는지 사람이 본 표본은 아직 없다.
- 측정 중 쓰기(ground) 호출 시간 초과가 4번 났다. 3번은 재시도로 이어졌고, 1번은 두 번째 시도도 실패했다. 답 결과의 생성 실패는 1건이다.
