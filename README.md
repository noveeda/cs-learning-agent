# CS Learning Agent

CS를 공부하다 막힌 학습자에게, 막힌 개념을 이해하는 데 부족한 선수 개념을 진단해 필요한 만큼만 보충하고,
이해를 확인한 뒤 원래 하던 학습으로 돌려보내는 학습 에이전트를 목표로 합니다.

1차 범위는 자료구조·알고리즘이며, 앱 안의 개념 지도 기반 로드맵 학습과 막힌 문장·문단 붙여넣기에서 시작합니다.
다른 과목에도 적용됨을 보이기 위해 운영체제 CPU 스케줄링 단원을 같은 개념 지도에 붙였습니다.
채팅과 도식은 보충 설명의 수단입니다. 핵심은 진단 → 최소 보충 → 이해 확인 → 원래 학습 복귀로 이어지는 경험입니다.
현재는 로컬 개발용 프로토타입입니다. 자료구조 36개와 운영체제 6개, 모두 42개 개념에서 막힘 → 진단 → 보충 → 이해 확인 → 복귀 흐름을 화면에서 끝까지 실행할 수 있습니다.

궁극 목표부터 성공 기준·학습 흐름·시스템 구조·모델과 평가까지의 결정과 근거는 [프로젝트 목표와 설계 방향](docs/project-direction.md)에 정리했습니다.

## 대상

| 구분 | 학습자 | 집중할 학습 문제 |
| --- | --- | --- |
| 주 사용자 | 전공 과목을 공부하는 컴공 학부생 | 수업·교재에서 막힌 개념의 부족한 선수 지식만 찾아 보충하고 원래 학습으로 돌아가기 |
| 보조 사용자 | CS 면접을 준비하는 취업 준비생 | 아는 개념을 다시 볼 때 빈 곳을 찾아 메우기 |
| 1차 범위 밖 | CS를 처음 배우는 입문자 | 빈틈이 넓어 진단 기반 보충보다 처음부터의 강의가 필요하므로 1차 범위에서 제외 |

## 목표 학습 흐름

다음은 구현 목표이며, 현재 제공되는 기능 전체를 뜻하지 않습니다.

1. **막힘 감지:** 학습 중 "막혔어요"·질문·소단원 확인 실패로 시작하고, 돌아올 지점을 저장합니다.
2. **막힌 개념 특정:** 질문이나 붙여넣은 문장을 개념 지도의 개념에 연결합니다.
3. **진단:** 선수 개념을 따라 짧은 자동 채점 문항으로 실제 빈틈을 찾습니다. 아는 개념 아래로는 내려가지 않습니다.
4. **최소 보충:** 찾은 빈틈만 가장 아래 개념부터 설명·예시·도식으로 채웁니다.
5. **이해 확인:** 자동 채점 적용 문항과 짧은 자기 말 설명을 모두 통과해야 다음으로 넘어갑니다.
6. **원래 학습 복귀:** 처음 막힌 개념을 다시 확인하고, 저장한 지점에서 원래 학습을 이어갑니다.

진단 문항 수·탐색 깊이·재보충 횟수의 상한과 성공 기준 수치는 예비 실험 후 확정하는 가설입니다.
진단·적용 문항은 검수된 정답으로 결정적으로 채점하고, AI 판단은 서술 채점에만 사용해 사람 채점과의 일치도로 검증할 예정입니다.

## 구현 상태

### 막힘 해결 흐름 (42개 개념)

- 대상 개념: 개념 지도의 42개 개념 전체(자료구조·알고리즘 36개, 운영체제 CPU 스케줄링 6개). 개념마다 진단 2·확인 2·서술 1문항이 있어 문항 은행은 모두 210문항입니다. 단답 정답은 테스트가 독립 계산해 대조합니다.
  - 운영체제 개념: 프로세스와 상태, 스케줄링 지표(대기·반환·응답 시간), FCFS, 라운드 로빈, 우선순위 스케줄링, SJF
- 진입: 개념 학습 화면과 로드맵 상세의 **[막혔어요]**, 학습 화면의 **막힌 문장 붙여넣기**(최대 2000자). 붙여넣은 문장은 모델이 개념 지도의 개념으로 특정하고, 지도 밖이면 지원 범위 밖으로 안내합니다.
- 진단: 직접 선수 개념부터 너비 우선으로 자동 채점 문항을 내고, 맞히면 그 아래로 내려가지 않습니다(문항 최대 5개, 깊이 최대 3). 과목 경계도 그대로 넘습니다. 예: 운영체제 `priority-scheduling`에서 막히면 자료구조 `priority-queue`까지 진단합니다. 진단 결과에는 어떤 문항에 어떻게 답해 빈틈으로 판단했는지 보여 줍니다. **이미 알아요**·**바로 설명**으로 건너뛸 수 있으며 기록됩니다.
- 보충과 이해 확인: 빈틈을 선수 개념부터 모델이 설명하고, 장면이 정해진 26개 개념에서는 단계별 애니메이션을 함께 보여 줍니다(아래 "현재 구현된 기반 기능"의 애니메이션 참고). 자동 채점 적용 문항과 서술(루브릭 모델 채점)을 **둘 다** 통과해야 이해로 인정합니다. 실패하면 다른 방식으로 최대 2회 재보충하고, 그래도 실패하면 미해결로 기록하고, 학습자가 **[다음으로]**를 눌러 다음 단계로 넘어갑니다. 마지막에 처음 막힌 개념을 다시 확인합니다.
- 복귀: 채운 선수 개념과 원래 막힌 지점의 연결을 요약하고 시작한 화면으로 돌아갑니다.
- 재개: Spring이 단계마다 세션을 버전 체크포인트로 저장하고(`/api/stuck/sessions`), 브라우저는 진행 중 세션을 기억해 새로고침·재접속 후 같은 단계에서 이어 갑니다. **처음부터 다시**로 진행 중 세션을 내려놓고 새로 시작할 수 있습니다(이전 세션 기록은 서버에 남음).
- 정답 키·채점 근거·루브릭은 브라우저로 보내지 않습니다. 모델 호출이 실패하면 상태를 바꾸지 않고 다시 시도할 수 있습니다.

### 현재 구현된 기반 기능

- 사람이 검수한 과목별 개념 지도를 저장소에 두고(`ai-core/content/<과목>/`), 로더가 하나의 연결 지도로 합칩니다.
  - 자료구조·알고리즘 36개(직접 선수 관계 51개)와 운영체제 6개(7개)를 합쳐 개념 42개, 직접 선수 관계 58개입니다.
  - 과목 사이 선수 관계는 2개입니다: `process-state` → `queue`, `priority-scheduling` → `priority-queue`.
  - 순환·고립 개념·간접 관계 중복 등 그래프 규칙은 합친 지도 전체에 검증합니다. `GET /api/concept-map`으로 과목 목록과 개념별 과목을 함께 조회합니다.
- 로드맵은 개념 지도를 선수 → 후속 순서의 단계로 배치해 보여 줍니다. 마우스 호버·키보드 초점·선택 시 직접 선수·후속 관계를 화살표로 강조하고, 상세에서 학습 목표와 선수 관계 근거를 보여 줍니다.
  - 과목이 둘 이상이면 개념마다 과목 표식을 붙이고, 과목 필터(범례 겸용)로 과목별로 걸러 봅니다. 필터는 저장하지 않아 새로고침하면 전체로 돌아갑니다.
  - 상세에서 다른 과목의 선수 개념을 고르면 필터를 풀어 그 개념을 보여 줍니다. 학습 화면의 영역 표시는 개념의 과목 이름입니다.
- 애니메이션 5종: 선형(배열·연결 리스트·스택·큐), 계층(해시 테이블·이진 탐색 트리·힙), 그래프 탐색(BFS·DFS, 트리 순회), 정렬(삽입·병합·퀵), CPU 스케줄링 간트(FCFS·SJF·라운드 로빈·우선순위)
  - 모델은 장면 종류와 입력값만 고르고, 각 단계의 상태는 코드가 결정적으로 계산합니다. 입력 검증에 실패하면 장면을 보여 주지 않습니다.
  - 막힘 보충에서는 개념별로 허용한 장면만 고릅니다. 예: 라운드 로빈 → RR 간트, 우선순위 큐 → 최소 힙. 채팅에서는 모델이 `show_animation` 도구로 부릅니다.
- 개념별 설명을 보고 학습을 시작하며, 미학습·학습 중·완료 상태를 직접 변경합니다.
- 개념별 대화를 자유 학습과 분리하고, 선택한 개념·상태·대화 연결을 같은 브라우저에서 복구합니다.
- 웹에서 질문하고 답변을 실시간으로 확인합니다.
- 모델이 `create_diagram` 도구를 호출해 SVG 도식을 만들거나 후속 질문에 맞춰 수정합니다.
- 백엔드에 대화와 도식 구조를 저장하고 새로고침·백엔드 재시작 후 복구합니다.
- 최근 문답과 선택한 도식 정보를 모델에 전달하고, 토큰 예산을 넘으면 오래된 문답부터 제외합니다.

예시 질문:

> 가상 메모리와 페이징은 어떤 관계야? 가상 페이지가 물리 프레임에 매핑되는 모습을 도식으로 설명해줘.

이 기능들은 목표 학습 흐름의 질의응답·이해 보조 기반입니다.
대화 본문·도식은 백엔드에 저장하고, 기본 로드맵의 선택·상태·개념별 대화 ID는 브라우저 localStorage에 저장합니다.
학습 상태는 사용자가 직접 표시하며, 자동 평가 결과나 AI가 생성한 커리큘럼 진도를 뜻하지 않습니다.
현재 화면은 디자인 시안을 바탕으로 구성했으며, 후속 수정에 따라 조정합니다.

로드맵은 사이드바의 **로드맵** 또는 `/#/roadmap`에서 엽니다. 개념 선택 후 **학습 시작**을 누르면
해당 개념의 질문이 채워진 학습 화면으로 이동합니다. 전송 버튼을 눌러야 모델 요청이 시작됩니다.
기존 개념은 **학습 이어가기**로 대화를 복구하며, 완료 상태는 재학습해도 유지합니다.
모바일은 그래프 대신 개념 목록을 제공하고 선택 시 하단 상세로 이동합니다.

로드맵 데이터는 같은 브라우저·접속 주소에서만 유지되며 기기 간 동기화는 제공하지 않습니다.
사이트 데이터를 지우면 로드맵 상태와 대화 연결이 초기화됩니다. 백엔드 대화 파일 자체는 삭제하지 않습니다.
저장 공간·권한 문제로 저장에 실패하면 안내를 표시하고 해당 변경을 적용하지 않습니다.
학습 중 **새 학습**은 현재 개념의 대화 연결만 새로 만들며, 사이드바 **학습하기**는 자유 학습을 엽니다.

### AI 커리큘럼 초안 생성 (1차 학습 흐름 밖)

주제·학습 목적·현재 수준을 입력하면 개요 구성 → 단원 생성 → 단원 검토 → 전체 검토·보완 순서로 커리큘럼 초안을 만드는 기능입니다.
웹 상단의 **커리큘럼 초안**에서 사용합니다. 새 설계 방향에서는 기능을 유지하되 1차 학습 흐름에는 포함하지 않습니다.
단계별 생성·문맥 관리·검토 스트리밍·저장과 재개·API는 [커리큘럼 초안 생성](docs/curriculum-draft.md)에 정리했습니다.

### 평가·실험 도구

- 비교 실험 모드: 화면 `/#/experiment`, API `/api/experiments`. 사전 시험 → 두 개념을 이 도구와 일반 채팅에 하나씩 배정해 학습 2회 → 사후 시험 → 설문 순서로 진행하고, 기록을 내보냅니다. 진행 절차는 [비교 실험 진행 절차서](docs/experiment/protocol.md)를 따릅니다.
- `ai-core/eval/`의 측정 스크립트. 결과 원본은 Git 밖(`ai-core/outputs/eval/`)에만 씁니다.
  - `sim_learner/run.py`: 시뮬레이션 학습자로 막힘 세션을 공개 API로 끝까지 진행해 진단 정확도(M1 recall·precision)와 과잉 보충률(M2)을 잽니다.
    - `--subject <과목ID>`(예: `os`)로 그 과목 개념만 막힌 개념으로 고릅니다. 요약에는 과목 경계 빈틈의 recall·precision이 따로 들어갑니다.
    - 놓친 빈틈은 이유별로 분류합니다: 문항 상한, 찍어 맞힘으로 가지치기, 그 밖
  - `mohler.py`(영어 단답 채점 데이터), `ai_grade.py`·`grading_sheet.py`·`agreement.py`(한국어 서술 답): 서술 채점의 사람 채점 대비 일치도(kappa, 거짓 통과율)를 잽니다.
  - `quality_floor.py`(서빙 모델의 자료구조·운영체제 지식 정답률), `animation_choice.py`(보충 장면 선택 성공률), `cs300_overlap.py`(문항 은행과 평가 전용 CS-300의 문장 중복 검사), `experiment_report.py`(비교 실험 결과 계산)
- 측정 수치는 이 README에 옮기지 않습니다. 결과는 각 측정 PR 본문에 있습니다.

### 앞으로 구현할 부분

- 운영체제의 나머지 단원과 네트워크·데이터베이스·컴퓨터개론 과목
- 보충 설명에 도식 도구(`create_diagram`) 사용

초안 생성 실험과 일반 채팅에서 계획·문제를 답변하는 기능은, 저장·진도·평가를 포함한 전체 학습 과정과 구분됩니다.
오래된 대화 요약·검색·장기 기억과 이미지 생성 모델 연동도 현재 미구현이며,
핵심 학습 흐름과 별도로 필요성과 우선순위를 검토할 항목입니다.

## 기술 스택

현재 main의 의존성과 구현을 기준으로 정리했습니다. 버전 범위는 각 영역의 의존성 파일을 기준으로 하며,
모델 서버는 앱과 분리된 실행 환경입니다.

| 영역 | 프레임워크·라이브러리 / 기술 | 용도 |
| --- | --- | --- |
| 프런트엔드 | React 19, React DOM 19, TypeScript 5 (`^5.7.0`) | 학습 화면과 UI 상태 관리, 타입 검사 |
| 프런트엔드 | react-markdown 10, lucide-react 0.468 계열 | 답변 Markdown 표시, UI 아이콘 |
| 프런트엔드 개발·빌드 | Vite 6 (`^6.1.0`), Node.js 24, npm | 로컬 개발 서버, API 프록시, 웹 번들 빌드와 의존성 관리 |
| 백엔드 | Java 21, Spring Boot 4.1.1, Spring Web MVC | REST API, 대화 저장·복구, AI core 응답 스트림 중계 |
| 백엔드 | Jackson, Java HttpClient | 대화·이벤트 JSON 처리, AI core HTTP 요청 |
| 백엔드 빌드 | Gradle Wrapper 9.8.1, Spring Dependency Management 플러그인 1.1.7 | 의존성 관리, 테스트와 실행 JAR 빌드 |
| AI core | Python 3.12, FastAPI, Uvicorn | 에이전트 API와 스트리밍 응답 제공, ASGI 서버 실행 |
| 커리큘럼 실행 | LangGraph 1.2.16 | 요청당 한 단계 실행, 기존 Spring JSON 체크포인트 유지 |
| AI core | OpenAI Python SDK, HTTPX | vLLM의 OpenAI 호환 API 호출, 모델 정보·토큰 수 조회 |
| AI core | Pydantic 2, fontTools(woff 지원) | 요청·도식 구조 검증, SVG에 포함할 한글 폰트 부분 집합 생성 |
| 모델 추론 | vLLM 0.32.0, gemma-4-26B-A4B-it AWQ 4bit | 별도 GPU 환경에서 언어 모델 추론과 도구 호출 제공([모델 선택 근거](docs/model-selection.md)) |
| 저장·시각화 | 로컬 JSON 파일, SVG, 브라우저 localStorage | 대화·도식 구조 저장, 도식 표시, 자유 학습·개념별 대화 ID와 로드맵 상태 유지 |
| 테스트 | Python unittest·unittest.mock, FastAPI TestClient | AI core의 모델 호출·문맥·도식·API 테스트 |
| 테스트 | JUnit Jupiter, Mockito, Spring Boot Test | 백엔드 API·저장소·스트리밍 테스트 |
| 테스트 | Node.js 내장 테스트 러너 | 프런트엔드 통신 회귀 테스트 |
| CI | GitHub Actions | main 대상 PR과 main push에서 영역별 테스트·빌드 실행 |

백엔드에는 LangChain4j OpenAI Spring Boot 4 Starter와 Lombok도 의존성으로 선언되어 있습니다.
LangChain4j는 현재 `/api/chat` 요청 경로에서 사용하지 않으며, 해당 경로의 모델 호출은 Python AI core가 담당합니다.
대화 저장은 로컬 JSON 파일 방식이며, 현재 데이터베이스나 ORM은 사용하지 않습니다.

의존성의 구체적인 버전과 실행 설정은 다음 파일에서 확인할 수 있습니다.

- [프런트엔드 의존성·스크립트](frontend/package.json), [npm 잠금 파일](frontend/package-lock.json)
- [백엔드 의존성·빌드](backend/build.gradle), [Gradle Wrapper](backend/gradle/wrapper/gradle-wrapper.properties)
- [AI core 의존성](ai-core/requirements-web.txt), [CI 설정](.github/workflows/ci.yml)

## 빠른 실행

### 준비 환경

| 구성 | 기준 |
| --- | --- |
| AI core | Python 3.12, [웹 의존성 목록](ai-core/requirements-web.txt) |
| Backend | JDK 21, 저장소에 포함된 Gradle Wrapper |
| Frontend | Node.js 24 및 npm(CI 기준) |
| 모델 서버 | 도구 호출과 `/tokenize`를 지원하는 vLLM |
| 현재 모델 구성 | `cyankiwi/gemma-4-26B-A4B-it-AWQ-4bit`, 컨텍스트 65,536토큰, GPU 1장 |
| 기존 모델 실행 환경 | RTX 3090 24GB × 2(모델 서버는 0번만 사용), vLLM 0.32.0 |

웹·백엔드·AI core 자체는 GPU를 요구하지 않지만, 실제 답변에는 별도로 실행 중인 모델 서버가 필요합니다.
모델의 가중치와 실행용 vLLM 환경은 저장소에 포함하지 않습니다.

### 1. 소스와 앱 의존성 설치

```bash
git clone https://github.com/noveeda/cs-learning-agent.git
cd cs-learning-agent
python3.12 -m venv ai-core/.venv
ai-core/.venv/bin/python -m pip install -r ai-core/requirements-web.txt
(cd frontend && npm ci)
```

이미 저장소와 가상환경이 있다면 clone과 가상환경 생성은 생략합니다.
이후 명령은 별도 안내가 없으면 **저장소 루트**에서 실행합니다.
Gradle 의존성은 첫 실행 때 다운로드합니다.

### 2. 모델 서버 준비

**이미 실행 중인 경우:** 다음 요청으로 모델 목록을 확인하고 3단계로 이동합니다.

```bash
curl --fail http://127.0.0.1:8000/v1/models
```

응답이 오더라도 도구 호출 설정과 `/tokenize` 지원이 필요합니다. AI core는 모델 목록의
`max_model_len`과 실제 입력 토큰 수를 사용하므로 일반적인 OpenAI 호환 서버만으로는 충분하지 않습니다.

**처음 준비하는 경우:** GPU 서버에서 모델용 환경을 별도로 준비합니다.
다음은 RTX 3090 24GB 한 장(0번)·64K 문맥 구성에 맞춘 실행 예시이며, 다른 GPU에서의 메모리·CUDA 호환성을 보장하는 설정은 아닙니다.
모델 선택 근거, 각 인자의 이유, AI core의 역할별 호출 설정은 [모델 선택 문서](docs/model-selection.md)에 있습니다.

```bash
python3.12 -m venv "$HOME/.venvs/cs-vllm"
"$HOME/.venvs/cs-vllm"/bin/python -m pip install 'vllm==0.32.0'
CUDA_VISIBLE_DEVICES=0 "$HOME/.venvs/cs-vllm"/bin/vllm serve cyankiwi/gemma-4-26B-A4B-it-AWQ-4bit \
  --revision 0ef577a5710035bd2d3a3f27e4f5cb2e86a9a9ba \
  --served-model-name gemma-4-26b-a4b-it-awq \
  --host 127.0.0.1 --port 8000 \
  --max-model-len 65536 \
  --max-num-seqs 1 \
  --gpu-memory-utilization 0.9 \
  --enable-auto-tool-choice \
  --tool-call-parser gemma4 \
  --reasoning-parser gemma4 \
  --limit-mm-per-prompt '{"image":0,"audio":0,"video":0}' \
  --structured-outputs-config '{"backend":"xgrammar","disable_any_whitespace":true}'
```

첫 실행은 모델 다운로드와 초기화에 시간이 걸립니다. 기본 모델 캐시는 `~/.cache/huggingface/hub/`입니다.
위 명령은 포그라운드에서 실행하므로 해당 터미널을 유지합니다. 모델 목록 요청이 성공한 뒤 앱을 실행합니다.
GPU 1번은 시뮬레이션 학습자·실험용으로 비워 둡니다. CUDA graph를 사용하기 위해 `--enforce-eager`를 넣지 않습니다.
Gemma 4 채팅 템플릿은 도구 스키마의 `$defs`·`$ref`와 `pattern`·`maximum` 같은 제약을 모델에 보여 주지 않습니다.
도식 도구 스키마는 이를 펼쳐서 보내야 하며, 그 전에는 도식 요청이 텍스트 답변으로 끝날 수 있습니다.
새 환경에서의 모델 설치·기동은 GPU 드라이버와 CUDA 조합을 별도로 확인해야 합니다.

### 3. 앱 실행

모델 서버 외에 **각각 별도 터미널**을 열고 저장소 루트에서 실행합니다.
이미 서비스가 실행 중이면 같은 포트에 중복 실행하지 않습니다.

```bash
# 터미널 1: Python AI core
cd ai-core
.venv/bin/python -m uvicorn serving.api:app --host 127.0.0.1 --port 8001
```

```bash
# 터미널 2: Spring Boot 백엔드
cd backend
./gradlew bootRun --args='--server.address=127.0.0.1'
```

```bash
# 터미널 3: React 개발 서버
cd frontend
npm run dev
```

브라우저에서 [학습 화면](http://localhost:5173)을 엽니다.
AI core 프로세스 상태는 `curl --fail http://127.0.0.1:8001/health`로 확인할 수 있습니다.
이 health 응답은 모델 연결이나 답변 생성 성공까지 검사하지 않습니다.

원격 서버에서 실행한다면 **내 PC의 터미널**에서 5173 포트만 전달합니다.
아래 사용자와 서버 주소는 실제 SSH 접속 정보로 바꿉니다.

```bash
ssh -N -L 5173:127.0.0.1:5173 사용자@서버주소
```

### 연결 설정과 CLI

| 설정 | 기본값·용도 |
| --- | --- |
| `VLLM_BASE_URL` | `http://127.0.0.1:8000/v1` — AI core의 모델 접속 주소 |
| `VLLM_MODEL` | 미설정 시 모델 목록의 첫 모델 사용 |
| `VLLM_API_KEY` | `not-needed` — 모델 서버에 인증이 있으면 해당 키 설정 |
| Spring `ai-core.base-url` | `http://127.0.0.1:8001` — 백엔드의 AI core 접속 주소 |
| Spring `chat.storage-dir` | 실행 디렉터리 기준 `./data/conversations` |

환경 변수는 AI core를 시작할 터미널에 설정합니다. `.env` 자동 로딩은 구현하지 않았습니다.
Spring 설정은 실행 인자에 `--ai-core.base-url=...`, `--chat.storage-dir=/절대/경로`로 지정할 수 있습니다.
`application.yml`의 기존 `langchain4j` 모델 설정은 현재 `/api/chat` 요청 경로에서 사용하지 않습니다.

웹 없이 도구 호출을 확인하려면 모델 서버를 켠 뒤 다음을 실행합니다.
CLI는 웹의 대화 저장·복구와 별개인 단일 질문 예제입니다.

```bash
cd ai-core
.venv/bin/python ask_agent.py "페이징 구조를 도식으로 설명해줘"
.venv/bin/python ask_agent.py --json "BFS 실행 흐름을 도식으로 만들어줘"
```

`ask.py`는 도구 없이 모델을 직접 호출하는 별도 예제입니다.

## 구조와 데이터 흐름

```text
브라우저 / React :5173
  → Spring Boot :8080       대화·막힘 세션 저장·복구, 요청 및 스트림 중계
    → FastAPI AI core :8001 문맥 구성, 모델 호출, 도식 도구 실행, 막힘 흐름 단계 실행·결정적 채점
      → vLLM :8000          gemma-4-26B-A4B 추론(GPU 0번)
```

프런트엔드는 React·TypeScript·Vite, 백엔드는 Java 21·Spring Boot,
AI core는 Python·FastAPI·OpenAI Python SDK를 사용합니다.
SDK는 로컬 vLLM의 OpenAI 호환 API를 호출하며, 기본 실행에 OpenAI 유료 API 키는 필요하지 않습니다.

```text
cs-learning-agent/
├── frontend/
│   ├── src/                   # 화면(로드맵·학습·막힘 패널), API 요청·NDJSON 스트림 처리
│   └── tests/                 # 프런트엔드 통신 회귀 테스트
├── backend/
│   ├── src/main/              # Spring API, JSON 대화·막힘 세션 저장소, 설정
│   ├── src/test/              # 백엔드 테스트
│   └── gradle/wrapper/        # Gradle 실행 구성
├── ai-core/
│   ├── serving/
│   │   ├── api.py             # FastAPI 엔드포인트
│   │   ├── agent.py           # 스트리밍 모델 호출·도구 실행 루프
│   │   ├── context.py         # 토큰 예산에 맞춘 최근 문답 선택
│   │   ├── concept_map.py     # 개념 지도 스키마·그래프 규칙 검증
│   │   ├── item_bank.py       # 진단·확인 문항 스키마·정답 정규화·불러오기
│   │   ├── stuck.py           # 막힘 흐름 상태 전환(LangGraph, 요청당 한 단계)
│   │   ├── grading.py         # 진단·확인 문항의 결정적 채점
│   │   ├── stuck_llm.py       # 막힘 흐름 LLM 역할(개념 특정·보충·장면 선택·서술 채점·요약)과 역할별 설정
│   │   ├── animation/         # 애니메이션 장면 요청 검증·결정적 시뮬레이터(선형·계층·그래프·정렬·CPU 스케줄링)
│   │   ├── tools/diagram.py   # 도식 입력 검증·SVG 렌더링
│   │   └── assets/            # 한글 도식 폰트와 라이선스
│   ├── content/dsa/           # 검수한 자료구조·알고리즘 개념 지도와 문항 은행(36개념·180문항)
│   ├── content/os/            # 검수한 운영체제 CPU 스케줄링 개념 지도와 문항 은행(6개념·30문항)
│   ├── eval/                  # 시뮬레이션 학습자·채점 일치도·품질 하한·비교 실험 결과 측정 스크립트
│   ├── tests/                 # 에이전트·문맥·도식·개념 지도·문항 은행·채점·막힘 흐름·API 테스트
│   └── requirements-web.txt   # 앱 의존성(vLLM 별도)
├── docs/                      # 목표·설계 방향, 커리큘럼 초안 기능 설명
├── .github/                   # CI, PR 템플릿, 보호 설정 안내
├── .claude/                   # Claude Code 공유 권한 설정, /verify-all 검증 스킬
├── AGENTS.md                  # 협업·개발·검수·병합 규칙(단일 원본)
├── CLAUDE.md                  # Claude Code용 진입점: AGENTS.md import와 전용 보충 규칙
└── README.md
```

로컬의 `codex/` 기획 문서, 데이터·학습·평가용 준비 폴더는 구현된 기능 목록이 아닙니다.
`codex/`의 기획 문서는 설계 방향을 바꾸기 전 내용이며, 목표·범위는 [프로젝트 목표와 설계 방향](docs/project-direction.md)을 따릅니다.
`codex/`, 가상환경, 모델 가중치, 대화 기록, 생성물은 Git에 포함하지 않습니다.
새 clone에는 로컬 전용 파일이 없습니다.

### API와 대화 저장

| API | 역할 |
| --- | --- |
| `POST /api/chat` | `{conversation_id, question, artifact_id?}`를 받아 NDJSON 이벤트 스트리밍 |
| `GET /api/conversations/{id}` | 저장된 대화와 도식 구조 복구 |
| `GET /api/artifacts/{id}` | 생성 SVG 조회 |
| `GET /api/concept-map` | 검수한 과목별 개념 지도를 합친 연결 지도와 과목 목록 조회 (AI core `GET /concept-map` 중계) |
| `POST /api/stuck/sessions` | `{entry}`로 막힘 세션 시작. 응답 `{id, version, stage, view, entry}` |
| `GET /api/stuck/sessions/{id}` | 저장된 막힘 세션의 현재 단계 복구 |
| `POST /api/stuck/sessions/{id}/step` | `{version, input}`으로 한 단계 진행. 낮은 버전 재전송은 저장본 반환, 버전 불일치는 409 |
| `/api/experiments/**` | 비교 실험 세션 생성·조회, 사전·사후 시험, 사건 기록, 다음 단계, 설문, 기록 내보내기(`GET /export`) |

백엔드를 `backend/`에서 실행하면 대화는 `backend/data/conversations/{id}.json`에 저장됩니다.
막힘 세션은 `backend/data/stuck-sessions/{id}.json`에 저장되며 측정 기록(진단·답변·판정·시각)을 포함합니다. 브라우저에는 `{id, version, stage, view, entry}`만 보내고 진단 기록(`log`·`probes`)은 서버에만 둡니다.
SVG 파일은 `ai-core/outputs/diagrams/`에 저장되므로 둘 다 보존해야 대화와 도식 파일을 함께 복구할 수 있습니다.
브라우저는 대화 ID를 localStorage에 저장합니다. 새 학습은 새 ID를 만들며 기존 파일을 삭제하지 않습니다.
과거 대화 목록 UI는 없으므로 브라우저의 ID를 잃으면 화면에서 이전 대화를 바로 다시 선택할 수 없습니다.

AI core는 `/tokenize`로 시스템 지시·도구 정의·문답·도식 정보를 포함해 토큰 수를 셉니다.
문맥 한도는 모델 서버 `/v1/models`의 `max_model_len`을 그대로 씁니다.
현재 서빙 설정(65,536토큰)에서 출력 4,096토큰과 여유 256토큰을 빼면 입력 예산은 61,184토큰입니다.
모델 서버의 `--max-model-len`을 바꾸면 이 예산도 함께 바뀝니다.
오래된 문답 쌍부터 제외하되 현재 질문·선택한 도식·현재 도구 실행 문맥은 유지합니다.
필수 내용만으로도 예산을 넘으면 오류를 반환합니다. 중단된 문답은 저장하되 후속 모델 문맥에서 제외합니다.

## 테스트와 검수

의존성을 설치한 뒤 저장소 루트에서 실행합니다. 아래 자동 테스트에는 실행 중인 GPU 모델이 필요하지 않습니다.

```bash
(cd ai-core && .venv/bin/python -m unittest discover -s tests -v)
(cd backend && ./gradlew --no-daemon test bootJar)
(cd frontend && npm test && npm run build)
git diff --check
```

UI 회귀 테스트는 별도로 실행합니다. API 응답을 격리해 메뉴, 대화·도식 복구, 확대·다운로드,
모바일 대화상자, 오류 후 재시도, 로드맵 선택·상태 복구와 개념별 대화 분리를 확인하며 GPU 모델은 필요하지 않습니다.

```bash
cd frontend
npx playwright install chromium  # 최초 1회 브라우저 설치
npm run test:ui
```

테스트는 전용 Vite 서버(127.0.0.1:5197)를 시작하고 종료합니다. 해당 포트를 비워 두세요.
이미 설치한 Chromium을 사용하려면 `BROWSER_EXECUTABLE_PATH=/절대/경로/chrome npm run test:ui`로 실행합니다.
기본 CI의 `Frontend` 검사는 기존 API 테스트와 빌드를 실행하며 브라우저 테스트는 별도 실행입니다.

[CI 설정](.github/workflows/ci.yml)은 main 대상 PR과 main push에서
`AI core`, `Backend`, `Frontend` 검사를 실행합니다. [GitHub Actions 결과](https://github.com/noveeda/cs-learning-agent/actions)를 확인할 수 있습니다.
CI는 GPU 모델을 다운로드하거나 실행하지 않으며 답변의 CS 정확성까지 보장하지 않습니다.

실제 모델을 켠 상태에서는 다음을 직접 검수합니다.

1. 질문 후 완성된 답변을 기다리는 동안 텍스트가 실시간 표시되는지 확인합니다.
2. 도식을 요청하고 후속 질문으로 연결 방향을 바꿔 봅니다.
3. 새로고침 후 대화와 도식이 복구되는지 확인합니다.
4. 새 학습을 시작하고 이전 대화가 섞이지 않는지 확인합니다.
5. 답변과 도식이 CS 개념을 정확히 설명하는지 별도로 검토합니다.

## 제약 및 관련 문서

- 현재는 인증 없는 로컬 개발 환경입니다. loopback 바인딩과 SSH 터널을 사용하며 공개 서비스용 인증·권한 제어는 구현하지 않았습니다.
- 대화 저장은 단일 백엔드 프로세스용 로컬 JSON 방식입니다. AI core는 프로세스당 한 요청을 처리합니다.
- 도식은 구조화된 노드·간선을 SVG로 그립니다. 간단한 격자 배치이며 복잡한 간선 교차나 설명의 의미적 정확성을 보장하지 않습니다.
- 브라우저는 SVG를 이미지로 표시합니다. 한글 글꼴은 필요한 글자만 SVG에 포함하며 [폰트 라이선스](ai-core/serving/assets/NotoSansKR-OFL.txt)를 동봉합니다.
- 현재 UI는 디자인 시안에 맞춰 공통 레이아웃·개념 학습 화면과 기본 로드맵을 적용한 단계입니다. 미구현 메뉴는 준비 중으로 표시합니다. 모바일에서는 메뉴를 접고 도식을 별도 대화상자로 표시하며, 통신 코드와 스트리밍·복구 동작은 유지 대상으로 검수합니다.
- 모델 실행 환경의 의존성 잠금 파일과 자동 배포 구성은 아직 없습니다. 위 모델 실행 예시는 새 GPU 환경에서 재검증이 필요합니다.

작업은 논의·결정 후 별도 브랜치에서 진행하고 테스트·PR·사용자 검수·명시적 승인을 거쳐 병합합니다.
자세한 규칙은 다음 문서를 따릅니다.

- [개발·커밋·검수·병합 규칙](AGENTS.md)
- [Claude Code 작업 설정](CLAUDE.md) — `AGENTS.md`를 불러오고 서명·검증·PR 사용 방법을 보충합니다.
- [PR 템플릿 선택 및 작성 방법](.github/pr-templates.md)
- [main 보호 설정 안내](.github/branch-protection.md) — 안내 파일의 존재가 원격 보호 적용을 뜻하지는 않습니다.
