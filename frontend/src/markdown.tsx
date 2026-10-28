/**
 * @file
 * @brief 채팅·막힘 패널의 표·수식·한글 강조를 같은 설정으로 렌더링
 */
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

/** @brief 완성 답변·스트림 조각·보충 설명을 표·수식·한글 강조가 있는 요소로 변환 */
export function LearningMarkdown({ children }: { children: string }) {
  return <Markdown {...options}>{children}</Markdown>;
}
