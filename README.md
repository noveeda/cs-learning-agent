# CS Learning Agent

CS 전반의 이론·개념 학습을 돕는 에이전트형 학습 도구입니다.
학습 주제 선택과 맞춤형 커리큘럼 구성부터 설명·질의응답·시각화·소단원 평가·보충 학습까지 연결해,
학습자가 이해도를 확인하며 체계적으로 학습하도록 돕는 것을 목표로 합니다.

운영체제·네트워크·자료구조·알고리즘·데이터베이스·컴퓨터 구조 등 CS 전반을 학습 범위로 삼습니다.
채팅과 도식은 학습 과정에서 사용하는 수단이며, 핵심은 커리큘럼을 따라 배우고 이해도를 확인하며 다음 단원으로 진행하는 경험입니다.
현재는 이 흐름의 기반 기능을 구현한 로컬 개발용 프로토타입으로, 전체 학습 과정은 아직 완성되지 않았습니다.

## 대상과 우선순위

| 우선순위 | 학습자 | 집중할 학습 문제 |
| --- | --- | --- |
| 1 | 기초를 보완하는 취업 준비생 | 단편적으로 아는 개념을 연결하고 원리·차이·선택 이유를 자기 말로 설명하기 |
| 2 | 전공 수업을 공부하는 학생 | 수업에서 이해하지 못한 이론·개념을 설명과 예시로 보충하기 |
| 3 | CS를 처음 배우는 입문자 | 낯선 용어와 선수 개념을 익히고 기초부터 학습하기 |

## 목표 학습 흐름

다음은 구현 목표이며, 현재 제공되는 기능 전체를 뜻하지 않습니다.

1. **학습 주제 선택:** 공부할 부분을 직접 고릅니다. 정하지 못했다면 AI에게 추천받습니다.
2. **커리큘럼 구성:** AI가 선택한 주제의 학습 순서와 단원·소단원별 목표를 구성합니다.
3. **커리큘럼에 따른 학습:** 정해진 순서에 따라 소단원의 이론과 개념을 공부합니다.
4. **질의응답과 이해 보조:** 모르는 내용을 에이전트에게 질문하고, 설명이 어렵다면 도식 등 시각화 자료로 보충합니다.
5. **소단원 이해도 확인:** 소단원이 끝날 때마다 시험·확인 문제 등으로 이해했는지 점검합니다.
6. **보충 또는 다음 단원:** 이해가 부족하면 보충 학습과 재평가를 진행하고, 충분히 이해했다면 다음 단원으로 넘어가 반복합니다.

평가 문항 형식, 채점·통과 기준, 학습자가 진행 여부를 조정하는 방식은 후속 설계에서 구체화합니다.
AI의 평가만으로 이해를 확정하거나 단일 정답만으로 학습 완료를 판단하지 않도록 설계할 예정입니다.

## 구현 상태

### 현재 구현된 기반 기능

- 웹에서 질문하고 답변을 실시간으로 확인합니다.
- 모델이 `create_diagram` 도구를 호출해 SVG 도식을 만들거나 후속 질문에 맞춰 수정합니다.
- 백엔드에 대화와 도식 구조를 저장하고 새로고침·백엔드 재시작 후 복구합니다.
- 최근 문답과 선택한 도식 정보를 모델에 전달하고, 토큰 예산을 넘으면 오래된 문답부터 제외합니다.

예시 질문:

> 가상 메모리와 페이징은 어떤 관계야? 가상 페이지가 물리 프레임에 매핑되는 모습을 도식으로 설명해줘.

이 기능들은 목표 학습 흐름의 질의응답·이해 보조 기반입니다.
대화 저장은 커리큘럼 진도나 학습 완료 상태를 관리하는 기능과는 구분됩니다.
현재 화면은 추후 디자인으로 교체할 임시 UI입니다.

### 앞으로 구현할 부분

- 학습 주제 선택과 AI 추천을 연결하는 학습 시작 흐름
- 단원·소단원별 목표와 선수 개념을 고려한 커리큘럼 생성·저장·조정
- 커리큘럼을 따라 학습 내용을 제공하고 현재 단원과 진도를 관리하는 기능
- 각 소단원의 학습 맥락과 질의응답·시각화 도구 연결
- 소단원별 평가 문항 생성, 응답 채점, 피드백과 평가 결과 저장
- 이해도에 따른 보충 학습·재평가·다음 단원 진행 및 학습 재개

일반 채팅에서 모델이 학습 계획이나 문제를 답변할 수 있더라도, 위 기능이 제품의 학습 과정으로 구현된 것은 아닙니다.
오래된 대화 요약·검색·장기 기억과 이미지 생성 모델 연동도 현재 미구현이며,
핵심 학습 흐름과 별도로 필요성과 우선순위를 검토할 항목입니다.

## 빠른 실행

### 준비 환경

| 구성 | 기준 |
| --- | --- |
| AI core | Python 3.12, [웹 의존성 목록](ai-core/requirements-web.txt) |
| Backend | JDK 21, 저장소에 포함된 Gradle Wrapper |
| Frontend | Node.js 24 및 npm(CI 기준) |
| 모델 서버 | 도구 호출과 `/tokenize`를 지원하는 vLLM |
| 현재 모델 구성 | `cyankiwi/Qwen3.5-27B-AWQ-4bit`, 컨텍스트 32,768토큰 |
| 기존 모델 실행 환경 | RTX 3090 24GB × 2, vLLM 0.32.0 |

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
다음은 기존 2-GPU·32K 구성에 맞춘 실행 예시이며, 다른 GPU에서의 메모리·CUDA 호환성을 보장하는 설정은 아닙니다.

```bash
python3.12 -m venv "$HOME/.venvs/cs-vllm"
"$HOME/.venvs/cs-vllm"/bin/python -m pip install 'vllm==0.32.0'
"$HOME/.venvs/cs-vllm"/bin/vllm serve cyankiwi/Qwen3.5-27B-AWQ-4bit \
  --host 127.0.0.1 --port 8000 \
  --tensor-parallel-size 2 \
  --max-model-len 32768 \
  --max-num-seqs 1 \
  --gpu-memory-utilization 0.9 \
  --disable-custom-all-reduce \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_xml
```

첫 실행은 모델 다운로드와 초기화에 시간이 걸립니다. 기본 모델 캐시는 `~/.cache/huggingface/hub/`입니다.
위 명령은 포그라운드에서 실행하므로 해당 터미널을 유지합니다. 모델 목록 요청이 성공한 뒤 앱을 실행합니다.
`--disable-custom-all-reduce`는 기존 GPU 간 P2P 미지원 환경을 고려한 옵션입니다.
기존 구성에서는 CUDA graph를 사용하기 위해 `--enforce-eager`를 넣지 않습니다.
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
  → Spring Boot :8080       대화 저장·복구, 요청 및 스트림 중계
    → FastAPI AI core :8001 문맥 구성, 모델 호출, 도식 도구 실행
      → vLLM :8000          Qwen 추론
```

프런트엔드는 React·TypeScript·Vite, 백엔드는 Java 21·Spring Boot,
AI core는 Python·FastAPI·OpenAI Python SDK를 사용합니다.
SDK는 로컬 vLLM의 OpenAI 호환 API를 호출하며, 기본 실행에 OpenAI 유료 API 키는 필요하지 않습니다.

```text
cs-learning-agent/
├── frontend/
│   ├── src/                   # 임시 UI, API 요청·NDJSON 스트림 처리
│   └── tests/                 # 프런트엔드 통신 회귀 테스트
├── backend/
│   ├── src/main/              # Spring API, JSON 대화 저장소, 설정
│   ├── src/test/              # 백엔드 테스트
│   └── gradle/wrapper/        # Gradle 실행 구성
├── ai-core/
│   ├── serving/
│   │   ├── api.py             # FastAPI 엔드포인트
│   │   ├── agent.py           # 스트리밍 모델 호출·도구 실행 루프
│   │   ├── context.py         # 토큰 예산에 맞춘 최근 문답 선택
│   │   ├── tools/diagram.py   # 도식 입력 검증·SVG 렌더링
│   │   └── assets/            # 한글 도식 폰트와 라이선스
│   ├── tests/                 # 에이전트·문맥·도식·API 테스트
│   └── requirements-web.txt   # 앱 의존성(vLLM 별도)
├── .github/                   # CI, PR 템플릿, 보호 설정 안내
├── AGENTS.md                  # 협업·개발·검수·병합 규칙
└── README.md
```

로컬의 `codex/` 기획 문서, 데이터·학습·평가용 준비 폴더는 구현된 기능 목록이 아닙니다.
`codex/`, 가상환경, 모델 가중치, 대화 기록, 생성물은 Git에 포함하지 않습니다.
새 clone에는 로컬 전용 파일이 없습니다.

### API와 대화 저장

| API | 역할 |
| --- | --- |
| `POST /api/chat` | `{conversation_id, question, artifact_id?}`를 받아 NDJSON 이벤트 스트리밍 |
| `GET /api/conversations/{id}` | 저장된 대화와 도식 구조 복구 |
| `GET /api/artifacts/{id}` | 생성 SVG 조회 |

백엔드를 `backend/`에서 실행하면 대화는 `backend/data/conversations/{id}.json`에 저장됩니다.
SVG 파일은 `ai-core/outputs/diagrams/`에 저장되므로 둘 다 보존해야 대화와 도식 파일을 함께 복구할 수 있습니다.
브라우저는 대화 ID를 localStorage에 저장합니다. 새 학습은 새 ID를 만들며 기존 파일을 삭제하지 않습니다.
과거 대화 목록 UI는 없으므로 브라우저의 ID를 잃으면 화면에서 이전 대화를 바로 다시 선택할 수 없습니다.

AI core는 `/tokenize`로 시스템 지시·도구 정의·문답·도식 정보를 포함해 토큰 수를 셉니다.
32,768토큰 중 출력 4,096토큰과 여유 256토큰을 빼면 입력 예산은 28,416토큰입니다.
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
모바일 대화상자, 오류 후 재시도를 확인하며 GPU 모델은 필요하지 않습니다.

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
- 현재 UI는 디자인 시안에 맞춰 공통 레이아웃과 개념 학습 화면을 적용한 단계입니다. 미구현 메뉴는 준비 중으로 표시합니다. 모바일에서는 메뉴를 접고 도식을 별도 대화상자로 표시하며, 통신 코드와 스트리밍·복구 동작은 유지 대상으로 검수합니다.
- 모델 실행 환경의 의존성 잠금 파일과 자동 배포 구성은 아직 없습니다. 위 모델 실행 예시는 새 GPU 환경에서 재검증이 필요합니다.

작업은 논의·결정 후 별도 브랜치에서 진행하고 테스트·PR·사용자 검수·명시적 승인을 거쳐 병합합니다.
자세한 규칙은 다음 문서를 따릅니다.

- [개발·커밋·검수·병합 규칙](AGENTS.md)
- [PR 템플릿 선택 및 작성 방법](.github/pr-templates.md)
- [main 보호 설정 안내](.github/branch-protection.md) — 안내 파일의 존재가 원격 보호 적용을 뜻하지는 않습니다.
