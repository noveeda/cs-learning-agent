# PR 템플릿 사용 방법

작은 수정·문서·리팩터링은 기본 템플릿, 나머지는 작업 목적에 맞는 템플릿을 사용합니다.
필요 없는 선택 항목은 삭제하고, 완료 기준·검증·자체 리뷰·사용자 검수·병합 규칙은 유지합니다.

| 용도 | 파일 |
| --- | --- |
| 기본 | [pull_request_template.md](pull_request_template.md) |
| 기능 추가 | [feature.md](PULL_REQUEST_TEMPLATE/feature.md) |
| 버그 수정 | [bugfix.md](PULL_REQUEST_TEMPLATE/bugfix.md) |
| 인프라·CI | [infra-ci.md](PULL_REQUEST_TEMPLATE/infra-ci.md) |

## GitHub 웹

템플릿이 기본 브랜치인 main에 병합된 뒤 사용합니다. 기본 템플릿은 PR 작성 시 자동 입력됩니다.
유형별 템플릿은 이슈 템플릿처럼 선택 화면이 뜨는 방식이 아니라 compare URL의 `template` 매개변수로 지정합니다.
아래 예시에서 `YOUR_BRANCH`를 작업 브랜치 이름으로 바꿉니다.

```text
https://github.com/noveeda/cs-learning-agent/compare/main...YOUR_BRANCH?expand=1&template=feature.md
https://github.com/noveeda/cs-learning-agent/compare/main...YOUR_BRANCH?expand=1&template=bugfix.md
https://github.com/noveeda/cs-learning-agent/compare/main...YOUR_BRANCH?expand=1&template=infra-ci.md
```

## GitHub CLI

원하는 템플릿을 임시 파일로 복사해 내용을 작성하고 PR 본문으로 전달합니다.
이 방식은 main 병합 전에도 사용할 수 있습니다. 저장소 루트에서 실행합니다.

```bash
cp .github/PULL_REQUEST_TEMPLATE/feature.md /tmp/pr-body.md
${EDITOR:-vi} /tmp/pr-body.md
gh pr create --base main --head YOUR_BRANCH --title "feat(scope): 변경 결과" --body-file /tmp/pr-body.md
```

기본은 `.github/pull_request_template.md`, 버그 수정은 `bugfix.md`, 인프라·CI는 `infra-ci.md`를 복사합니다.
검증에는 실제 명령과 결과를 남기고, 미실행 항목은 이유를 적습니다.
위험·롤백 설명은 기본·기능·버그 템플릿에서도 해당할 경우 추가합니다.
사용자 검수와 명시적 병합 승인은 별도 절차이며 체크박스나 CI 성공으로 대신하지 않습니다.

근거: [GitHub 템플릿 생성 안내](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/creating-a-pull-request-template-for-your-repository),
[PR URL 매개변수 안내](https://docs.github.com/en/pull-requests/reference/using-query-parameters-to-create-a-pull-request).
