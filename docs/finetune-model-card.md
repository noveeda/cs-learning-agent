# CS 학습 하네스용 gemma 4 12B 미세조정 모델 설명 (초안)

- 배포할 때 모델 파일과 함께 둘 설명의 초안이다. 지금 파일은 검증용 1차 모델 기준이다.
- 사용자가 받을 모델은 배포용 2차 학습(설계 7-1, 61개념 전부) 뒤에 이 문서를 그 모델 기준으로 고친다.
- 측정 근거: `docs/finetune.md` 9절(학습), 10절(측정)

## 1. 모델

| 항목 | 값 |
|---|---|
| 파일 | `model-Q4_0.gguf`(GGUF, Q4_0, 6,975,878,848바이트) |
| sha256 | `6e02ff93277a2018a2cde169ad7766ccbd03de24abb49f369c6ccade1e1b5d8c` |
| 바탕 모델 | `google/gemma-4-12B-it-qat-q4_0-unquantized`(리비전 `b6ed862`) |
| 바탕 모델 라이선스 | Apache-2.0 |
| 학습 방법 | QLoRA(4비트 바탕 + LoRA r=16, alpha=32, 모든 선형층), 학습률 1e-4, 2에폭(1,510스텝), 문맥 4096, seed 0 |
| 내보내기 | 어댑터 병합 → `convert_hf_to_gguf.py` → `llama-quantize` Q4_0 |
| 실행 | llama.cpp `llama-server --jinja`, 사고(thinking) 모드 끔 |

- 학습 행: train 12,067행, valid 635행. 검증 손실은 마지막 스텝이 가장 낮았다(0.0575).

## 2. 쓰임

- 이 프로젝트의 CS 학습 하네스가 모델에게 시키는 네 역할만 배웠다.
  - 범위 결정(scope): 질문이 어느 개념에 해당하는지
  - 근거 기반 문장 쓰기(ground): 교재 문단을 근거로 답 문장 쓰기
  - 일치 판정(judge): 문장이 근거 문단과 맞는지
  - 틀린 문장 고치기(repair)
- 사실의 근거는 계속 교재 문단(RAG 검색)이다. CS 지식을 외우게 하려고 학습한 모델이 아니다.
- 하네스의 지시문·JSON schema와 같은 형식으로만 학습했다. 하네스 밖의 일반 대화 성능은 재지 않았다.
- 서술 채점(grade_explanation)은 학습하지 않았다. 채점 측정 세트(Mohler)를 지키기 위해서다.

## 3. 학습 데이터

- 학습 예시를 만든 모델은 gemma 4 26B-A4B(AWQ 4비트, 이 프로젝트 운영 서버)뿐이다.
  - 판정·범위는 구성상 정답이다. 판정은 26B 하네스가 근거 대조를 통과시킨 문장을 맞음으로, 그 문장을 일부러 망가뜨린 것을 틀림으로 둔다.
  - 문장 쓰기·고치기는 하네스 검사(문단 글자 대조 + 26B 판정)를 통과한 26B 출력만 쓴다.
- 측정 검사에 쓴 외부 모델(gpt-6.1-sol)의 출력은 학습 데이터에 넣지 않았다.
- 측정 세트 질문과 공백·문장부호를 지운 뒤 같은 질문은 학습에서 버렸다.
- 근거 문단은 아래 공개 교재 8권에서 왔다.

| 교재 | 저자 | 라이선스 |
|---|---|---|
| [Open Data Structures](https://opendatastructures.org/) | Pat Morin | [CC BY 2.5 CA](https://creativecommons.org/licenses/by/2.5/ca/) |
| [Algorithms](https://jeffe.cs.illinois.edu/teaching/algorithms/) | Jeff Erickson | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| [Computer Networks: A Systems Approach](https://book.systemsapproach.org/) | Larry Peterson, Bruce Davie | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| [Database Design – 2nd Edition](https://opentextbc.ca/dbdesign01/) | Adrienne Watt, Nelson Eng | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| [Operating Systems and Middleware (Rev. 1.3.1)](https://gustavus.edu/+max/os-book/) | Max Hailperin | [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) |
| [Microprocessor Design](https://en.wikibooks.org/wiki/Microprocessor_Design) | Wikibooks contributors | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) |
| [Introduction to Computer Science](https://openstax.org/details/books/introduction-computer-science) | OpenStax | [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) |
| [Computer Organization and Assembly Language Programming](https://mavmatrix.uta.edu/oer_mavsopenpress2/3) | Chenxi Wang, Muhammad Rashed | [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) |

## 4. 배포 조건

- 비상업 오픈소스로 배포한다. 유료로 팔거나 유료 서비스에 넣지 않는다.
  - 교재 중 2권이 비상업(NC) 조건이라서다.
- 교재로 학습한 모델 가중치가 교재의 2차 저작물인지는 법적으로 정해지지 않았다.
  - 그래서 같은 조건 배포(SA) 조건이 가중치에 미치는지는 남은 위험이다(설계 9-1).
- 바탕 모델의 Apache-2.0 고지와 위 교재 출처 표기를 함께 배포한다.

## 5. 제외 개념과 측정 결과 요약

- 1차 모델은 처음 보는 개념에서도 나아지는지 보려고 8개념을 학습에서 통째로 뺐다.
  - `bfs`, `btree-index`, `cache-locality`, `linked-list`, `network-layers`, `normalization`, `pipelining-hazards`, `tcp-reliability`
  - 이 개념의 질문·근거 문단·교란 문장은 학습 예시에 나오지 않는다(절 대응표로 검사).
- 측정은 미세조정 전 같은 바탕 모델(12B QAT Q4_0)과 같은 도구·세트·서버 옵션으로 했다.

| 지표 | 미세조정 전 | 1차 모델 | 판정(95% 구간) |
|---|---:|---:|---|
| 범위 결정 정확도 | 85.5% | 89.9% | 좋아짐 |
| 일치 판정 검출률 | 64.3% | 83.8% | 좋아짐 |
| 일치 판정 검출률 · 제외 개념 | 67.3% | 87.8% | 좋아짐 |
| 일치 판정 오경보율 | 2.3% | 1.0% | 변화 없음 |
| 서술 채점 거짓 통과 / 거짓 불통과 | 4.7% / 37.5% | 4.3% / 37.6% | 변화 없음 |
| 노출(26B 검사 / sol 검사) | 5.6% / 4.9% | 6.6% / 4.2% | 둘 다 변화 없음 |
| 노출 · 제외 개념(26B / sol) | 4.7% / 5.2% | 5.0% / 4.3% | 둘 다 변화 없음 |
| 한자 섞인 문장 | 0 | 0 | - |
| 첫 문장 / 전체 90% (RTX 3090 단독) | 10.7 / 23.0초 | 10.4 / 19.7초 | - |
| GPU 최대 메모리 (단독, `-np 1 -c 8192`) | 7,708MiB | 7,708MiB | - |

- 노출은 "확인 필요" 표시 없이 나간 문장 중 검사기가 근거와 어긋난다고 본 비율이다. 이 프로젝트의 전환 기준(2% 이하)에는 아직 못 미친다.
- 응답 시간도 전환 기준(RTX 4060 추정 첫 문장 10초)에 못 미친다. 시간의 대부분은 문장마다 부르는 일치 검사다.

## 6. 한계

- 노출 지표 판정에 쓴 26B는 학습 데이터를 만든 교사 모델이다. 26B 검사 점수는 26B를 닮기만 해도 좋아질 수 있어 외부 검사(sol)를 함께 적었다.
- 두 검사기는 같은 문장을 불일치로 고르는 경우가 적다(3,508문장 중 27개). 어느 쪽이 맞는지는 사용자 표본 50문장 검수 뒤에 판단한다.
- 판정 측정 세트의 틀린 문장 중 덧붙이기·개념 바꾸기는 학습 데이터와 같은 26B가 만든 것이다. 이 점수는 교사의 교란 모양을 배운 효과가 섞일 수 있다. 규칙 교란(숫자·부정) 점수를 따로 적는 이유다.
- 1차 학습 데이터에 판정 측정 세트와 같은 문장·문단이 5행 들어 있었다. 빼고 다시 계산해도 결론은 같다(`docs/finetune.md` 10-1). 2차 학습 데이터에서는 뺐다.
