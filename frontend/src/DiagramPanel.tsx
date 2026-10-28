/**
 * @file
 * @brief 생성된 SVG 도식이나 교재 그림 PNG의 확대·저장·실패 안내; 작은 화면에서는 모달로 표시
 * - 교재 그림(kind 'figure')은 캡션과 출처(교재명·저자·절·라이선스)를 그림 아래에 함께 보여 줌
 */
import { useEffect, useRef, useState } from "react";
import { Download, X } from "lucide-react";
import { artifactNoun, artifactTitle, isFigureArtifact, type Artifact, type FigureArtifact } from "./api";

/** @brief 현재 도식·교재 그림과 화면 너비에 따라 데스크톱 패널 또는 모바일 대화상자 구성 */
export function DiagramPanel({
  artifact,
  onClose,
  onImageError,
}: {
  artifact: Artifact | FigureArtifact;
  onClose: () => void;
  onImageError: () => void;
}) {
  const [zoomed, setZoomed] = useState(false);
  const [imageFailed, setImageFailed] = useState(false);
  const [compact, setCompact] = useState(
    () => window.matchMedia("(max-width: 850px)").matches,
  );
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 850px)");
    const update = () => setCompact(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    setZoomed(false);
    setImageFailed(false);
  }, [artifact.artifact_id]);
  useEffect(() => {
    // 네이티브 모달로 키보드 초점을 도식 안에 유지하고 Esc 닫기를 지원
    if (compact && dialog.current && !dialog.current.open)
      dialog.current.showModal();
  }, [compact]);
  // 교재 그림은 PNG 원본을 그대로 보여 주므로 이름·저장 형식만 도식과 다름
  const figure = isFigureArtifact(artifact) ? artifact.figure : null;
  const title = artifactTitle(artifact);
  const noun = artifactNoun(artifact);
  const format = figure ? "PNG" : "SVG";
  const content = (
    <>
      <div className="panel-header">
        <div>
          <span className="eyebrow">{figure ? "교재 그림" : "학습 도식"}</span>
          <h2 id="diagram-title">{title}</h2>
        </div>
        <button
          className="icon-button"
          aria-label={`${noun} 닫기`}
          onClick={onClose}
        >
          <X size={20} aria-hidden="true" />
        </button>
      </div>
      <div className={`diagram-stage ${zoomed ? "zoomed" : ""}`}>
        <img
          key={artifact.artifact_id}
          src={artifact.url}
          alt={title}
          onError={() => {
            setImageFailed(true);
            onImageError();
          }}
        />
      </div>
      {figure && (
        <div className="figure-caption">
          <p>{figure.caption}</p>
          <p className="figure-source">{figure.source}</p>
        </div>
      )}
      {imageFailed && (
        <p className="error" role="alert">
          {noun}을 불러오지 못했습니다. 서버 연결을 확인해 주세요.
        </p>
      )}
      <div className="panel-footer">
        <button
          aria-label={zoomed ? `${noun} 화면에 맞춤` : `${noun} 확대`}
          onClick={() => setZoomed(!zoomed)}
        >
          {zoomed ? "화면에 맞춤" : "확대"}
        </button>
        <a href={artifact.url} download={`${title}.${format.toLowerCase()}`}>
          <Download size={15} aria-hidden="true" />
          {format} 저장
        </a>
      </div>
    </>
  );
  return compact ? (
    <dialog
      className="artifact-panel card mobile-diagram"
      ref={dialog}
      aria-labelledby="diagram-title"
      onClose={onClose}
    >
      {content}
    </dialog>
  ) : (
    <aside className="artifact-panel card" aria-label={figure ? "교재 그림" : "생성된 도식"}>
      {content}
    </aside>
  );
}
