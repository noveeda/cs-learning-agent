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
