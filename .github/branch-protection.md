# main 보호 설정

이 문서는 적용할 설정을 설명한다. 파일을 커밋해도 GitHub 설정은 자동으로 바뀌지 않는다.

## 적용 순서

1. 작업 브랜치를 push하고 main 대상 PR을 연다. 기존 로컬 main 커밋이 원격에 없으면
   그 차이도 검수 범위에 포함되므로 PR에서 명시한다. main에 직접 push해서 우회하지 않는다.
2. PR의 `CI` workflow에서 `AI core`, `Backend`, `Frontend` 검사가 생성되고 통과하는지 확인한다.
3. 저장소 Settings → General → Pull Requests에서 **Allow merge commits**가 켜져 있는지 확인한다.
   기본 병합은 Create a merge commit을 사용한다.
4. Settings → Branches → Add branch protection rule에서 `main`을 대상으로 아래 설정을 적용한다.
   기존 ruleset이 있다면 중복 규칙을 추가하기 전에 상충 여부를 확인한다.
5. GitHub에서 main의 보호 상태와 필수 검사 목록을 다시 확인한다.
   이후 사용자 검수와 명시적 병합 승인을 기다린다.

## 보호 항목

- Require a pull request before merging: 켬.
- Require status checks to pass before merging: 켬.
  - `AI core`
  - `Backend`
  - `Frontend`
- Require branches to be up to date before merging: 켬.
- Require conversation resolution before merging: 켬.
- Do not allow bypassing the above settings: 켬.
- Allow force pushes / Allow deletions: 끔.
- Require linear history: 끔. Merge commit 정책과 충돌하기 때문이다.
- 사용자 승인 없이 auto-merge를 활성화하지 않는다.

## 리뷰 계정

PR 작성자와 다른 검수 계정이 있으면 Require approvals를 켜고 승인 1개 이상을 요구한다.
새 커밋이 올라오면 오래된 승인을 무효화하도록 설정한다.

PR을 사용자 본인 계정으로 만들면 같은 계정으로 Approve할 수 없다.
별도 검수 계정이 없다면 이 항목을 설정하기 전에 사용자와 계정 구성을 결정해야 한다.
필수 PR·CI는 유지하되, 채팅에서의 명시적 승인과 GitHub의 독립 리뷰 승인을 혼동하지 않는다.

## 현재 적용 상태

2026-10-20 확인 시 원격 main은 `protected: false`였다.
Git push용 로컬 인증은 없으며, 연결된 GitHub 앱의 보호 설정 조회는
`403 Resource not accessible by integration`을 반환했다.
따라서 이 문서 작성 시점에는 원격 보호 설정을 적용하지 않았다.
관리 권한이 있는 GitHub 웹 화면 또는 인증된 관리 도구로 적용한 뒤 이 상태를 갱신한다.
