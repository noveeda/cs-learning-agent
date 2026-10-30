/**
 * @file
 * @brief 채팅·막힘 패널의 표·수식·한글 강조·각주를 같은 설정으로 렌더링
 */
import { useId } from "react";
import Markdown, { type Components, type Options } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import remarkCjkFriendly from "remark-cjk-friendly/parseOnly";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

/** @brief 긴 표를 키보드로도 가로 스크롤할 수 있는 영역에 넣고 표 구조는 유지 */
const components: Components = {
  table({ node, ...props }) {
    return (
      <div
        className="markdown-table"
        role="region"
        aria-label="표 (가로 스크롤)"
        tabIndex={0}
      >
        <table {...props} />
      </div>
    );
  },
  /**
   * @brief 링크를 앱 화면을 벗어나지 않게 염
   * - 각주 번호·되돌아가기(#…): 앱 화면 전환이 주소 해시(#/learn)를 쓰므로, 해시를 바꾸면 학습 화면이 다른 화면으로 바뀜
   *   그래서 해시는 그대로 두고 같은 id의 각주 요소로 스크롤만 함
   * - 바깥 링크(출처의 라이선스 등): 새 탭으로 열어 학습 중인 답을 잃지 않게 함
   */
  a({ node, href, ...props }) {
    if (href?.startsWith("#")) {
      const id = decodeURIComponent(href.slice(1));
      return (
        <a
          href={href}
          {...props}
          onClick={(event) => {
            event.preventDefault();
            document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "center" });
          }}
        />
      );
    }
    return <a href={href} target="_blank" rel="noopener noreferrer" {...props} />;
  },
};

/**
 * @brief 네 출력 위치가 공유할 파서 확장·수식 안전 옵션·표 출력기
 * - 파서 확장으로 강조를 인식하므로 코드 안의 달러·별표와 답변 원문은 치환하지 않음
 */
const options: Options = {
  remarkPlugins: [remarkGfm, remarkMath, remarkCjkFriendly],
  // 미완성·오류 수식이 들어오는 스트리밍에서도 나머지 문단을 계속 표시
  rehypePlugins: [[rehypeKatex, { throwOnError: false, trust: false }]],
  components,
};

// 각주 처리(mdast-util-to-hast)가 각주 영역 제목 id를 접두어 없이 "footnote-label"로 고정해 넣는 부분
const FOOTNOTE_LABEL = "footnote-label";
type HastNode = { properties?: Record<string, unknown>; children?: HastNode[] };

/**
 * @brief 각주 영역 제목의 id와 각주 번호의 aria-describedby에도 렌더 위치별 접두어를 붙이는 rehype 단계
 * - clobberPrefix는 각주 본문·번호 id에만 붙어서, 이 단계가 없으면 두 답의 제목 id가 여전히 겹침
 */
function prefixFootnoteLabel(prefix: string) {
  const label = prefix + FOOTNOTE_LABEL;
  const visit = (node: HastNode) => {
    const props = node.properties;
    if (props?.id === FOOTNOTE_LABEL) props.id = label;
    if (Array.isArray(props?.ariaDescribedBy))
      props.ariaDescribedBy = props.ariaDescribedBy.map((id) => (id === FOOTNOTE_LABEL ? label : id));
    node.children?.forEach(visit);
  };
  return () => visit;
}

/**
 * @brief 완성 답변·스트림 조각·보충 설명을 표·수식·한글 강조·각주가 있는 요소로 변환
 * - 각주 id 접두어(clobberPrefix)를 렌더 위치마다 다르게 둠
 *   - 하네스 답은 각주 이름이 늘 p1부터라, 접두어가 같으면 한 화면의 두 답이 같은 id를 써서 각주 링크가 첫 답으로 이동함
 *   - useId 값의 기호(_, :, « 등)는 버리고 영문·숫자만 남겨 주소 조각(#...)에 그대로 쓸 수 있게 함
 *   - 각주 영역 제목 id는 clobberPrefix가 닿지 않아 prefixFootnoteLabel로 따로 붙임
 * - 각주 영역의 숨김 제목과 되돌아가기 링크 이름을 한국어로 바꿔 화면 읽기 프로그램이 영어로 읽지 않게 함
 */
export function LearningMarkdown({ children }: { children: string }) {
  const prefix = `user-content-${useId().replace(/[^A-Za-z0-9]/g, "")}-`;
  return (
    <Markdown
      {...options}
      rehypePlugins={[...(options.rehypePlugins ?? []), prefixFootnoteLabel(prefix)]}
      remarkRehypeOptions={{
        clobberPrefix: prefix,
        footnoteLabel: "각주",
        footnoteBackLabel: "본문으로 돌아가기",
      }}
    >
      {children}
    </Markdown>
  );
}
