# 12B 하네스 역할 미세조정 측정 기록

- 설계: `docs/superpowers/specs/2026-10-29-finetune-12b-design.md`
- 계획: `docs/superpowers/plans/2026-10-29-finetune-12b.md`
- 이 문서는 측정 도구 확장(계획 Task 4)의 결과와 미세조정 전후 비교 기준을 적는다.
- 측정 원본은 Git 밖에 둔다.
  - 측정 세트: `ai-core/outputs/harness-sets/`
  - 보정 시험 원본: `ai-core/outputs/finetune/astra-calibration/`

## 1. 제외 개념 추가 측정 세트

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

## 2. 외부 검사 모델 보정 시험

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

## 3. 측정 도구에 더한 것

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

## 4. 미세조정 전 기준값

- 바탕 모델이 정해진 뒤(Task 11 결과) 잰다.
  - 조건: 제외 개념 추가 세트, `--holdout`, `--astra`
- 결과는 이 절에 적는다.
