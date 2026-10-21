// 파일 역할: 생성된 SVG의 확대·저장·실패 안내; 작은 화면에서는 모달로 도식 표시
import { useEffect, useRef, useState } from "react";
import { Download, X } from "lucide-react";
import type { Artifact } from "./api";

// 역할: 현재 도식과 화면 너비에 따라 데스크톱 패널 또는 모바일 대화상자 구성
export function DiagramPanel({
  artifact,
  onClose,
  onImageError,
}: {
  artifact: Artifact;
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
  const content = (
    <>
      <div className="panel-header">
        <div>
          <span className="eyebrow">학습 도식</span>
          <h2 id="diagram-title">{artifact.diagram.title}</h2>
        </div>
        <button
          className="icon-button"
          aria-label="도식 닫기"
          onClick={onClose}
        >
          <X size={20} aria-hidden="true" />
        </button>
      </div>
      <div className={`diagram-stage ${zoomed ? "zoomed" : ""}`}>
        <img
          key={artifact.artifact_id}
          src={artifact.url}
          alt={artifact.diagram.title}
          onError={() => {
            setImageFailed(true);
            onImageError();
          }}
        />
      </div>
      {imageFailed && (
        <p className="error" role="alert">
          도식을 불러오지 못했습니다. 서버 연결을 확인해 주세요.
        </p>
      )}
      <div className="panel-footer">
        <button
          aria-label={zoomed ? "도식 화면에 맞춤" : "도식 확대"}
          onClick={() => setZoomed(!zoomed)}
        >
          {zoomed ? "화면에 맞춤" : "확대"}
        </button>
        <a href={artifact.url} download={`${artifact.diagram.title}.svg`}>
          <Download size={15} aria-hidden="true" />
          SVG 저장
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
    <aside className="artifact-panel card" aria-label="생성된 도식">
      {content}
    </aside>
  );
}
