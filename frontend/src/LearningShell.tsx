import { useRef, type ReactNode } from "react";
import {
  BookOpen,
  Code2,
  FileText,
  History,
  Home,
  Layers,
  Map,
  Menu,
  Plus,
  Settings,
  X,
} from "lucide-react";

const upcoming = [
  { label: "문제 풀이", icon: Code2 },
  { label: "에이전트 기록", icon: History },
  { label: "복습 노트", icon: FileText },
];

function Navigation({
  page,
  busy,
  onNavigate,
}: {
  page: "roadmap" | "learn";
  busy: boolean;
  onNavigate: (page: "roadmap" | "learn") => void;
}) {
  return (
    <nav className="navigation" aria-label="주 메뉴">
      <button disabled>
        <Home size={17} aria-hidden="true" />
        <span>대시보드</span>
        <small>준비 중</small>
      </button>
      <button
        className={page === "roadmap" ? "nav-current" : ""}
        aria-current={page === "roadmap" ? "page" : undefined}
        disabled={busy}
        onClick={() => onNavigate("roadmap")}
      >
        <Map size={17} aria-hidden="true" />
        <span>로드맵</span>
      </button>
      <button
        className={page === "learn" ? "nav-current" : ""}
        aria-current={page === "learn" ? "page" : undefined}
        disabled={busy}
        onClick={() => onNavigate("learn")}
      >
        <BookOpen size={17} aria-hidden="true" />
        <span>학습하기</span>
      </button>
      {upcoming.map(({ label, icon: Icon }) => (
        <button key={label} disabled>
          <Icon size={17} aria-hidden="true" />
          <span>{label}</span>
          <small>준비 중</small>
        </button>
      ))}
      <button disabled>
        <Settings size={17} aria-hidden="true" />
        <span>설정</span>
        <small>준비 중</small>
      </button>
    </nav>
  );
}

function Brand() {
  return (
    <div className="brand">
      <span className="brand-icon">
        <Layers size={17} aria-hidden="true" />
      </span>
      CS Studio
    </div>
  );
}

export function LearningShell({
  children,
  headerExtra,
  busy,
  onNew,
  onAsk,
  page,
  onNavigate,
}: {
  children: ReactNode;
  headerExtra?: ReactNode;
  busy: boolean;
  onNew: () => void;
  onAsk: () => void;
  page: "roadmap" | "learn";
  onNavigate: (page: "roadmap" | "learn") => void;
}) {
  const menu = useRef<HTMLDialogElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  return (
    <div className="app">
      <aside className="sidebar">
        <Brand />
        <Navigation page={page} busy={busy} onNavigate={onNavigate} />
        <div className="sidebar-footer">
          <span className="local-dot" /> 로컬 AI 학습 공간
        </div>
      </aside>
      <dialog
        className="mobile-menu"
        id="mobile-menu"
        aria-label="주 메뉴"
        ref={menu}
        onClose={() => menuButton.current?.focus()}
        onClick={(e) => {
          if (e.target === e.currentTarget) menu.current?.close();
        }}
      >
        <div className="mobile-menu-content">
          <div className="mobile-menu-heading">
            <Brand />
            <button
              className="icon-button"
              aria-label="메뉴 닫기"
              onClick={() => menu.current?.close()}
            >
              <X size={20} />
            </button>
          </div>
          <Navigation
            page={page}
            busy={busy}
            onNavigate={(next) => {
              menu.current?.close();
              onNavigate(next);
            }}
          />
        </div>
      </dialog>
      <div className="workspace">
        <header className="page-header">
          <button
            ref={menuButton}
            className="icon-button menu-toggle"
            aria-label="메뉴 열기"
            aria-haspopup="dialog"
            aria-controls="mobile-menu"
            onClick={() => menu.current?.showModal()}
          >
            <Menu size={20} />
          </button>
          <div className="page-heading">
            <h1>{page === "roadmap" ? "로드맵" : "개념 학습"}</h1>
            <p>
              {page === "roadmap"
                ? "연결된 개념을 따라 학습해요"
                : "개념을 그림으로 연결해요"}
            </p>
          </div>
          <div className="header-actions">
            {headerExtra}
            {page === "learn" && (
              <>
                <button className="button secondary ask-button" onClick={onAsk}>
                  에이전트에게 질문하기
                </button>
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={onNew}
                >
                  <Plus size={16} aria-hidden="true" />새 학습
                </button>
              </>
            )}
          </div>
        </header>
        {children}
      </div>
    </div>
  );
}

export function AgentNotes({
  hasMessages,
  disabled,
  onSuggest,
  children,
}: {
  hasMessages: boolean;
  disabled: boolean;
  onSuggest: (text: string) => void;
  children?: ReactNode;
}) {
  return (
    <aside className="learning-support" aria-label="학습 도움말">
      <section className="card support-card">
        <span className="badge">에이전트 노트</span>
        <h2>{hasMessages ? "이해를 한 단계 더" : "어디부터 시작할까요?"}</h2>
        <p>
          {hasMessages
            ? "어려운 부분을 다시 물어보세요. 다른 예시나 도식으로 함께 살펴볼 수 있어요."
            : "궁금한 개념을 질문해 보세요. 필요할 때 에이전트가 도식으로 정리해 줘요."}
        </p>
        {(hasMessages
          ? ["다른 예시로 다시 설명해줘", "핵심 개념을 도식으로 정리해줘"]
          : [
              "프로세스와 스레드는 어떻게 달라?",
              "TCP 연결 과정을 도식으로 보여줘",
            ]
        ).map((text) => (
          <button
            className="button secondary suggestion-action"
            disabled={disabled}
            key={text}
            onClick={() => onSuggest(text)}
          >
            {text}
          </button>
        ))}
      </section>
      {children}
    </aside>
  );
}
