# 12B 하네스 역할 미세조정 학습 데이터

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

## 4. 결과

(생성 중. 끝나면 역할별 행 수와 거른 이유별 개수를 적는다.)
