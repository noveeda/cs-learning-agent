---
name: verify-all
description: AGENTS.md의 현재 프로젝트 검증 명령(AI core 테스트, 백엔드 테스트·빌드, 프런트엔드 테스트·빌드, 공백 검사)을 실행하고 영역별 통과 여부를 보고한다. PR 검수 요청 전, 또는 사용자가 전체 검증·테스트 실행·/verify-all을 요청할 때 사용한다.
argument-hint: "[all|ai-core|backend|frontend]"
---

# 전체 검증

- `AGENTS.md`의 "현재 프로젝트 검증 명령"을 저장소 루트 기준으로 실행한다.
- 검증 명령이 바뀌면 `AGENTS.md`와 이 파일을 함께 갱신한다.

## 실행 범위

- 인자 없음 또는 `all`: 아래 네 단계를 모두 실행한다.
- `ai-core`, `backend`, `frontend`: 해당 영역과 공백 검사만 실행한다.
- 문서·주석만 바뀐 경우: 전체 테스트 대신 diff·링크 검토로 대신하고 그 사실을 보고한다.

## 명령

1. AI core: `cd ai-core && .venv/bin/python -m unittest discover -s tests`
2. 백엔드: `cd backend && ./gradlew test bootJar`
3. 프런트엔드: `cd frontend && npm test && npm run build`
4. 공백 검사: `git diff --check`, 스테이징한 변경이 있으면 `git diff --cached --check`

- 1~3은 서로 독립이라 백그라운드로 동시에 실행해도 된다. 백엔드 첫 실행은 Gradle 다운로드로 수 분 걸릴 수 있다.
- GPU 모델 서버는 필요 없다. 실제 모델 답변 검수는 README "테스트와 검수"의 직접 확인 항목을 따른다.
- UI 회귀 테스트(`cd frontend && npm run test:ui`)는 기본 CI에 없으므로 화면 동작이 바뀐 경우에만 추가로 실행한다.

## 실패 처리

- `ai-core/.venv`나 `frontend/node_modules`가 없는 준비 누락은 테스트 실패와 구분해 보고하고,
  README "빠른 실행" 1단계의 설치 명령을 안내한다. 사용자 확인 없이 의존성을 설치하지 않는다.
- 테스트 모듈 import 단계의 `ModuleNotFoundError`도 코드 실패가 아니라 가상환경과 `requirements-web.txt`의 불일치로 보고한다.
  uv로 만든 가상환경에는 pip가 없을 수 있으므로 `uv pip install --python ai-core/.venv/bin/python -r ai-core/requirements-web.txt`를 안내한다.
- 실패한 테스트 이름과 핵심 오류 메시지를 그대로 인용한다.
- 통과시키려고 테스트 조건을 바꾸거나 실패를 재실행으로 덮지 않는다.

## 보고 형식

| 영역 | 명령 | 결과 | 근거 |
| --- | --- | --- | --- |
| AI core | unittest | 통과/실패/미실행 | 실행 테스트 수, 실패 이름 |
| 백엔드 | test bootJar | … | … |
| 프런트엔드 | npm test, build | … | … |
| 공백 | git diff --check | … | … |

- 테스트 통과는 자체 리뷰나 사용자 검수를 대신하지 않는다(`AGENTS.md` 3절).
