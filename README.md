# CS Learning Agent

CS 전반의 개념 학습을 돕는 AI 학습 도구입니다.
현재 프로젝트는 Java 백엔드의 초기 골격과 Python AI 작업용 디렉터리를 준비한 단계입니다.

## 디렉터리 구조

```text
cs-learning-agent/
├── backend/                  # Java · Spring Boot 백엔드
│   ├── src/main/java/        # 서버 애플리케이션 코드
│   ├── src/main/resources/   # application.yml 등 서버 설정·리소스
│   ├── src/test/java/        # 백엔드 테스트
│   ├── gradle/wrapper/       # Gradle Wrapper 실행 구성
│   ├── build.gradle          # 의존성 및 빌드 설정
│   └── settings.gradle       # Gradle 프로젝트 설정
├── ai-core/                  # Python 기반 자료 분석·모델 추론·평가 영역
│   ├── configs/              # 모델 연결 및 실행·평가 설정
│   ├── data/
│   │   ├── raw/              # 원본 자료
│   │   ├── processed/        # 추출·전처리한 자료
│   │   └── eval/             # 모델 평가용 데이터
│   ├── serving/              # 모델 서버 실행·추론 API 호출 관련 코드
│   ├── train/                # 필요 시 사용할 미세조정·학습 코드
│   ├── eval/                 # 평가 실행 코드와 지표 계산
│   ├── tests/                # AI 영역 코드 테스트
│   └── .venv/                # 로컬 Python 가상환경
├── codex/
│   └── planning-documents/   # 기획·요구사항·시스템·화면·ERD 문서
│       ├── assets/           # 문서용 이미지·구성도·스타일·폰트
│       └── sources/          # 문서 양식 원본과 출처 기록
├── .gitignore                # 프로젝트 전체 Git 제외 규칙
└── README.md                 # 프로젝트 및 디렉터리 안내
```

AI 영역의 설명은 각 폴더의 용도이며, 해당 기능의 구현 완료를 뜻하지 않습니다.
`ai-core/data/eval/`은 평가 데이터, `ai-core/eval/`은 평가 코드를 위한 폴더입니다.

모델 가중치는 프로젝트 밖의 `~/.cache/huggingface/hub/`에 저장합니다.
가상환경·원본/전처리 데이터·빌드 결과·`codex/`는 루트 `.gitignore`에서 제외합니다.
`.git/`은 Git 관리 정보, `.idea/`는 IDE 설정 폴더입니다.

## 도식 에이전트 실행

`ask.py`는 기존 텍스트 호출 예제이며, `ask_agent.py`는 Qwen이 필요에 따라
`create_diagram` 도구를 선택하고 결과를 받아 최종 설명을 작성하는 CLI입니다.

```bash
cd /home/infoserver/cs-learning-agent/ai-core
source .venv/bin/activate
python ask_agent.py "가상 페이지와 물리 프레임의 관계를 그림으로 설명해줘"
# 도구 호출 인자와 결과를 JSON 이벤트로 확인
python ask_agent.py --json "BFS의 실행 흐름을 도식으로 만들어줘"
python -m unittest discover -s tests -v
```

- `serving/agent.py`: 최대 3회 도구 실행 라운드, 모델 호출별 120초 제한, 오류 결과 재전달.
- `serving/tools/diagram.py`: 구조화된 노드·간선 검증과 SVG 렌더링. 모델 코드를 실행하지 않습니다.
- `outputs/diagrams/`: 생성 SVG 저장 위치(Git 제외). 파일을 브라우저로 열 수 있습니다.
- 이벤트: `text_start`, `text_delta`, `tool_started`, `tool_finished`, `tool_error`, `artifact`, `answer`, `done`.
  `text_delta`는 생성 즉시 전달하고, `answer`는 완성된 최종 답변입니다. 도구 인자는 스트림 완료 후 검증·실행합니다.
- 기본 서버는 `http://127.0.0.1:8000/v1`. `VLLM_BASE_URL`, `VLLM_MODEL`, `VLLM_API_KEY`로 변경할 수 있습니다.
- Python 의존성: `openai`, `pydantic>=2`(현재 가상환경에 설치됨).

vLLM 실행 시 기존 설정에 `--enable-auto-tool-choice --tool-call-parser qwen3_xml`을
추가해야 합니다. `--enforce-eager`는 추가하지 않습니다. 현재 설치 버전은 vLLM 0.32.0입니다.
도식은 간단한 격자 배치만 지원하며, 복잡한 그래프의 간선 교차를 자동으로 제거하지 않습니다.
검증은 형식과 연결 구조를 검사하며 CS 설명의 의미적 정확성까지 보장하지 않습니다.

## 웹 학습 화면

`frontend/`는 React + TypeScript + Vite 웹 화면입니다. 대화를 중심으로 표시하고,
도식이 생성되면 결과 패널을 엽니다. 모바일에서는 패널을 닫아 대화로 돌아갈 수 있습니다.

```text
브라우저 :5173 → Spring Boot :8080 → ai-core :8001 → vLLM :8000
```

vLLM이 도구 호출 옵션으로 실행된 상태에서, 아래 명령을 **각각 별도 터미널**에서 실행합니다.
이미 서버가 실행 중이면 같은 포트에 중복 실행하지 마세요.

```bash
# 1. Python API
cd /home/infoserver/cs-learning-agent/ai-core
.venv/bin/python -m uvicorn serving.api:app --host 127.0.0.1 --port 8001

# 2. Spring 중계 서버
cd /home/infoserver/cs-learning-agent/backend
./gradlew bootRun --args='--server.address=127.0.0.1'

# 3. 웹 화면
cd /home/infoserver/cs-learning-agent/frontend
npm ci
npm run dev
```

브라우저에서 `http://localhost:5173`을 엽니다. 원격 서버를 사용한다면 VS Code의
Ports에서 **5173 포트만 전달**하거나 SSH 터널을 사용합니다.

```bash
# 내 PC에서 실행. 서버주소는 실제 SSH 주소로 대체합니다.
ssh -N -L 5173:127.0.0.1:5173 infoserver@서버주소
```

- `POST /api/chat`: `{conversation_id, question, artifact_id?}` → 줄바꿈으로 구분된 JSON 이벤트(NDJSON).
- `GET /api/conversations/{id}`: 저장한 대화와 도식 구조 복구.
- `GET /api/artifacts/{id}`: 생성된 SVG를 이미지로 표시·저장.
- Spring 백엔드가 `backend` 작업 디렉터리 기준 `data/conversations/{id}.json`에 원문과 도식 구조를 원자적으로 저장합니다.
  위치는 `--chat.storage-dir=/absolute/path`로 지정할 수 있습니다. 단일 백엔드 프로세스용 로컬 저장소입니다.
- 브라우저는 대화 ID만 localStorage에 보관합니다. 새로고침 후 서버에서 복구하며, 새 학습은 새 ID를 만듭니다.
  기존 대화 파일은 삭제하지 않습니다. 이전 버전에서 서버에 저장하지 않았던 대화는 소급 복구할 수 없습니다.
- AI core는 vLLM `/tokenize`로 시스템 지시·도구 스키마·최근 문답·도식 구조를 포함한 실제 입력 토큰 수를 계산합니다.
  모델의 `max_model_len`에서 출력 4,096토큰과 여유 256토큰을 뺀 예산을 사용합니다.
  예산 초과 시 오래된 문답 쌍부터 제외하고 화면에 알립니다. 현재 질문·활성 도식·현재 도구 호출 묶음은 유지합니다.
  이 필수 내용만으로 예산을 초과하면 오류로 종료합니다. 도구 실행 후에도 다시 계산합니다.
- 현재 선택한 도식 ID는 해당 대화에 속한 것만 허용합니다. 도식의 노드·연결을 전달하며 SVG 바이너리는 보내지 않습니다.
- 답변 텍스트·상태·도식은 도착 즉시 표시합니다. 도구 호출 전 안내문과 도구 실행 후 설명은 라운드별로 구분합니다.
- 응답이 중단되면 이미 생성된 텍스트를 남기고 오류를 표시합니다. 실패한 대화는 후속 요청 이력에서 제외합니다.
- 생성 중 전송을 막으며, 실패 시 질문을 입력창에 복원합니다. 중단된 문답은 저장하되 후속 문맥에서 제외하고 표시합니다.
- 현재는 인증 없는 로컬 개발 화면입니다. 모든 서버는 loopback에 바인딩하며 공개 배포용 사용자 인증·접근 제어는 아직 없습니다.
- 오래된 대화 요약·검색·장기 기억은 아직 구현하지 않았습니다. 저장된 모든 대화가 매번 모델에 전달되는 것은 아닙니다.
- 브라우저는 SVG를 `<img>`로 표시하고 답변의 HTML을 실행하지 않습니다.

검증: `frontend`에서 `npm run build`, `backend`에서 `./gradlew test`,
`ai-core`에서 `.venv/bin/python -m unittest discover -s tests -v`.

한글 도식은 `serving/assets/NotoSansKR.woff2`에서 필요한 글자만 추출해 SVG에
포함합니다(SIL Open Font License 동봉). 웹/도식 의존성 목록은
`ai-core/requirements-web.txt`이며 모델용 vLLM 설치 목록과는 분리되어 있습니다.
현재 실행 로그: `/tmp/cs-core.log`, `/tmp/cs-backend.log`, `/tmp/cs-web.log`.

### 임시 프런트엔드와 디자인 교체

현재 화면은 **질문·도구 호출·도식 반환을 검증하기 위한 임시 UI**입니다.
추후 제공되는 디자인과 예시를 기준으로 교체하며, 현재 레이아웃·색상·컴포넌트를
최종 디자인으로 간주하지 않습니다.

- `frontend/src/main.tsx`, `frontend/src/style.css`: 임시 화면과 표시 상태.
- `frontend/src/api.ts`: 화면과 독립적인 요청·NDJSON 해석·시간 제한·오류 처리.
  디자인을 바꾸더라도 이 통신 코드와 기존 `/api/chat`, `/api/artifacts/{id}`를 재사용합니다.
- 프런트엔드 통신 회귀 테스트: `cd frontend && npm test` (Node.js 22.18 이상).

새 디자인 전에는 디자인 시스템이나 추가 화면 계층을 만들지 않습니다.
