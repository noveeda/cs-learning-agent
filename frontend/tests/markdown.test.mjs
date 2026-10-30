/**
 * @file
 * @brief 공통 마크다운의 표·수식·한글 강조·각주와 코드·원시 HTML 보존 경계 검증
 */
import { after, test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createElement, Fragment } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

// JSX·CSS를 앱과 같은 Vite 변환으로 읽되, HTTP 서버와 운영 API는 사용하지 않음
const server = await createServer({
  configFile: false,
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { middlewareMode: true, hmr: false, watch: null },
  appType: 'custom',
  optimizeDeps: { noDiscovery: true },
});
after(() => server.close());
const { LearningMarkdown } = await server.ssrLoadModule('/src/markdown.tsx');

/** @brief 실제 공통 컴포넌트를 HTML로 렌더해 사용자에게 보이는 요소를 검사 */
function render(source) {
  return renderToStaticMarkup(createElement(LearningMarkdown, null, source));
}

// GFM 설정이 빠져 표가 한 문단의 파이프로 되돌아가는 회귀 방지
test('GFM 표를 머리글과 본문 셀이 있는 table로 출력한다', () => {
  const html = render('| 장점 | 단점 |\n| :--- | :--- |\n| 간결함 | 호출 비용 |');
  assert.match(html, /<table>/);
  assert.match(html, /<th[^>]*>장점<\/th>/);
  assert.match(html, /<td[^>]*>호출 비용<\/td>/);
});

// 수식 원문의 달러 구분자가 화면에 남는 회귀 방지
test('인라인 수식을 KaTeX로 출력하고 달러 구분자를 제거한다', () => {
  const html = render(String.raw`계산: $n!$, $5! = 5 \times 4$.`);
  assert.equal((html.match(/class="katex"/g) ?? []).length, 2);
  assert.doesNotMatch(html, /\$/);
  assert.doesNotMatch(html, /katex-display/);
});

test('블록 수식을 별도 KaTeX display 영역으로 출력한다', () => {
  const html = render('$$\nn! = n \\times (n - 1)!\n$$');
  assert.match(html, /class="katex-display"/);
  assert.doesNotMatch(html, /\$/);
});

// 닫는 괄호·따옴표 다음의 한글 조사 때문에 강조 구분자가 글자로 남는 회귀 방지
test('한글 조사 앞의 괄호·따옴표가 있는 굵게와 기울임을 인식한다', () => {
  const html = render('**재귀(Recursion)**란 **반복문(Iteration)**으로 **"종료 조건"**을 *재귀(Recursion)*로 설명한다.');
  assert.match(html, /<strong>재귀\(Recursion\)<\/strong>란/);
  assert.match(html, /<strong>반복문\(Iteration\)<\/strong>으로/);
  assert.match(html, /<strong>&quot;종료 조건&quot;<\/strong>을/);
  assert.match(html, /<em>재귀\(Recursion\)<\/em>로/);
  assert.doesNotMatch(html, /\*/);
});

// 렌더 전 전역 치환으로 코드 예제까지 변형하는 문제 방지
test('인라인·펜스·들여쓰기 코드의 달러와 강조 기호를 그대로 둔다', () => {
  const html = render('`$n!$ **재귀(Recursion)**란`\n\n```python\n$5!$ **"종료"**를\n```\n\n    $n!$ **반복문(Iteration)**으로');
  assert.match(html, /<code>\$n!\$ \*\*재귀\(Recursion\)\*\*란<\/code>/);
  assert.match(html, /<pre><code class="language-python">\$5!\$ \*\*&quot;종료&quot;\*\*를\n<\/code><\/pre>/);
  assert.match(html, /<pre><code>\$n!\$ \*\*반복문\(Iteration\)\*\*으로\n<\/code><\/pre>/);
  assert.doesNotMatch(html, /class="katex|<strong>/);
});

// 스트리밍 도중 잘못되거나 미완성인 수식도 나머지 답변을 깨뜨리지 않아야 함
test('잘못된 수식은 오류 표시로 남기고 이후 문단도 출력한다', () => {
  const html = render(String.raw`$\unknowncommand{$` + '\n\n이후 설명은 유지');
  assert.match(html, /class="katex-error"/);
  assert.match(html, /<p>이후 설명은 유지<\/p>/);
});

test('미완성 스트림 이후 완성된 답변을 같은 설정으로 다시 렌더한다', () => {
  assert.match(render('계산 $n!'), /계산 \$n!/);
  assert.match(render('계산 $n!$'), /class="katex"/);
});

// 새 플러그인을 추가해도 원시 HTML과 위험한 링크를 실행하지 않는 기존 경계 유지
test('원시 HTML은 이스케이프하고 위험한 링크는 실행 가능한 URL로 만들지 않는다', () => {
  const html = render('<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[이동](javascript:alert%281%29)');
  assert.doesNotMatch(html, /<script|<img|href="javascript:/);
  assert.match(html, /&lt;script&gt;/);
});

// 수식에 링크를 만드는 KaTeX 명령이 있어도 LLM 출력에 trust 권한을 주지 않음
test('수식 명령으로 신뢰하지 않는 링크를 만들지 않는다', () => {
  const html = render(String.raw`$\href{javascript:alert(1)}{x}$ $\url{https://example.com}$`);
  assert.doesNotMatch(html, /<a\b|href=/);
});

// 하네스 답은 각주 이름이 늘 p1~p3이라, 한 화면의 두 답이 같은 id를 쓰면 두 번째 답의 각주 번호가 첫 답의 각주로 이동함
test('한 화면의 두 답은 각주 id가 겹치지 않고 각자 자기 각주를 가리킨다', () => {
  const answer = '스택은 마지막 값을 먼저 꺼낸다.[^p1]\n\n[^p1]: > 원문 인용';
  const html = renderToStaticMarkup(createElement(Fragment, null,
    createElement('div', { key: 'a' }, createElement(LearningMarkdown, null, answer)),
    createElement('div', { key: 'b' }, createElement(LearningMarkdown, null, answer))));
  const ids = [...html.matchAll(/ id="([^"]+)"/g)].map(m => m[1]);
  assert.equal(new Set(ids).size, ids.length, `겹친 id: ${ids}`);
  const [first, second] = html.split('</div>');
  for (const part of [first, second]) {
    const ref = part.match(/href="#([^"]+)"[^>]*data-footnote-ref/)[1];
    assert.match(part, new RegExp(` id="${ref}"`));
    // 각주 번호가 설명으로 가리키는 숨김 제목("각주")도 같은 답 안의 것이어야 함
    const label = part.match(/aria-describedby="([^"]+)"/)[1];
    assert.match(part, new RegExp(` id="${label}"`));
  }
});

// 화면 읽기 프로그램이 각주 영역·되돌아가기 링크를 영어로 읽지 않게 함
test('각주 숨김 라벨과 되돌아가기 링크 이름을 한국어로 출력한다', () => {
  const html = render('본문[^p1]\n\n[^p1]: 원문');
  assert.match(html, /<h2[^>]*class="sr-only"[^>]*>각주<\/h2>/);
  assert.match(html, /aria-label="본문으로 돌아가기"/);
  assert.doesNotMatch(html, /Footnotes|Back to reference/);
});

// 출처의 라이선스 링크를 누르면 앱 화면이 그 사이트로 바뀌어 학습 중인 답을 잃는 회귀 방지
test('바깥 링크는 새 탭으로 열고 각주 링크는 주소 해시를 쓰지 않는다', () => {
  const html = render('본문.[^p1]\n\n[^p1]: 원문. [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)');
  assert.match(html, /<a href="https:\/\/creativecommons\.org\/licenses\/by\/4\.0\/" target="_blank" rel="noopener noreferrer">/);
  assert.match(html, /data-footnote-ref/);
  assert.doesNotMatch(html, /<a[^>]*data-footnote-ref[^>]*target="_blank"/);
});
