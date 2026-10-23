# CLAUDE.md

- 이 저장소의 협업·개발·검수·병합 규칙과 학습용 주석 규칙은 `AGENTS.md`가 단일 원본이다.
- Claude Code는 아래 import로 같은 규칙을 읽는다. 규칙을 바꿀 때는 `AGENTS.md`만 수정하고 이 파일에 복사하지 않는다.

@AGENTS.md

## Claude Code 전용 보충

- 커밋 메시지와 PR 본문에 `Co-Authored-By` 트레일러나 생성 도구 문구를 붙이지 않는다.
  `.claude/settings.json`의 `attribution`을 빈 값으로 두어 기본 서명을 끈다.
- 검증은 `/verify-all` 스킬로 `AGENTS.md`의 "현재 프로젝트 검증 명령"을 실행하고 영역별 결과를 보고한다.
  문서·주석만 바뀐 경우에는 `AGENTS.md` 규칙대로 전체 테스트 대신 diff·링크를 검토한다.
- PR은 `gh pr create`로 main 대상으로 열고 `.github/pr-templates.md`에 따라 템플릿을 고른다.
  병합은 merge commit 방식(`gh pr merge --merge`)으로 하고, 병합한 브랜치는 삭제한다.
- 병합 승인 위임 (2026-10-23 사용자 결정): `AGENTS.md` 4절의 명시적 병합 승인을 아래처럼 미리 받아 둔 것으로 본다.
  - 코드·문서 PR: 최신 커밋의 CI가 모두 통과하고 Claude가 전체 diff를 자체 리뷰했으면 Claude가 병합한다.
  - 데이터 PR(개념 지도·문항 은행·루브릭·평가 세트처럼 사람이 내용을 보증해야 하는 것): 사용자가 직접 검수하고 승인해야 병합한다.
  - Claude가 작성하지 않은 PR이나 실행 중인 서버·모델 호출 동작을 바꾸는 PR은 영향 범위를 먼저 확인하고, 조율이 필요하면 병합하지 않고 보고한다.
- 팀 공유 권한·설정은 `.claude/settings.json`, 개인 설정은 Git에서 제외되는 `.claude/settings.local.json`에 둔다.
- 로컬 `codex/` 폴더는 Codex로 작업하던 시기의 기획·모델 평가 자료 보관 위치이며 Git에 포함하지 않는다.
  참고 자료로 읽을 수 있지만 구현된 기능 목록으로 취급하지 않는다.
