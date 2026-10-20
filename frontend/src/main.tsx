import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import Markdown from 'react-markdown';
import { ArrowUp, Plus, MessageSquare, ArrowUpRight, X, Download, PanelRightOpen, Check, LoaderCircle, Layers } from 'lucide-react';
import { requestChat, loadConversation, newConversation, type Artifact, type Message, type AgentEvent } from './api';
import './style.css';
import { CurriculumDraft } from './CurriculumDraft';

function App() {
  const [curriculumOpen, setCurriculumOpen] = useState(false);
  const [conversationId, setConversationId] = useState(() => localStorage.getItem('cs-conversation') || newConversation());
  const [loading, setLoading] = useState(true);
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  const [contextNotice, setContextNotice] = useState('');
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [zoomed, setZoomed] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const inFlight = useRef(false);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadConversation(conversationId).then(restored => {
      if (cancelled) return;
      setMessages(restored);
      const latest = restored.filter(m => m.artifact).at(-1)?.artifact;
      setArtifact(latest || null);
      setPanelOpen(Boolean(latest));
      setLoading(false);
    }).catch(err => { if (!cancelled) setError(err.message); });
    return () => { cancelled = true; };
  }, [conversationId]);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [messages, status, draft]);

  async function send(text: string) {
    const value = text.trim();
    if (!value || inFlight.current || loading) return;
    inFlight.current = true;
    setMessages(old => [...old, { role: 'user', content: value }]);
    let partialAnswer = '';
    setDraft('');
    setQuestion(''); setBusy(true); setError(''); setStatus('질문을 살펴보고 있어요');
    function showProgress(event: AgentEvent) {
      if (event.type === 'context') setContextNotice(event.dropped_messages
        ? `대화는 모두 저장돼 있어요. 이번 답변에는 토큰 한도에 맞는 최근 대화와 활성 도식을 사용해요.`
        : '');
      if (event.type === 'text_start' || event.type === 'tool_started') {
        partialAnswer = '';
        setDraft('');
      }
      if (event.type === 'text_delta') {
        partialAnswer += event.text;
        setDraft(partialAnswer);
        setStatus('답변을 작성하고 있어요');
      }
      if (event.type === 'tool_started') setStatus('개념을 도식으로 정리하고 있어요');
      if (event.type === 'tool_error') setStatus('도식의 연결을 다시 확인하고 있어요');
      if (event.type === 'artifact') {
        setArtifact(event);
        setZoomed(false);
        setPanelOpen(true);
        setStatus('그림에 맞춰 설명을 정리하고 있어요');
      }
    }
    try {
      const { answer, artifact: generated } = await requestChat(value, conversationId, showProgress, artifact?.artifact_id);
      setMessages(old => [...old, {role: 'assistant', content: answer, artifact: generated}]);
    } catch (err) {
      setError(err instanceof Error && err.name !== 'AbortError' ? err.message : '응답 대기 시간이 초과됐습니다. 다시 시도해 주세요.');
      setMessages(old => [
        ...old.map((m, i) => i === old.length - 1 ? {...m, failed: true} : m),
        ...(partialAnswer ? [{role: 'assistant' as const, content: partialAnswer, failed: true}] : []),
      ]);
      setQuestion(value);
    } finally { setDraft(''); inFlight.current = false; setBusy(false); setStatus(''); input.current?.focus(); }
  }
  const suggestions = ['가상 메모리와 페이징을 그림으로 설명해줘', 'BFS와 DFS는 어떻게 달라?', 'TCP 연결 과정을 도식으로 보여줘'];
  return <div className="app">
    <nav className="rail" aria-label="주 메뉴"><div className="brand-mark" aria-label="CS Studio"><Layers aria-hidden="true" size={23}/></div><button title="새 학습" aria-label="새 학습" disabled={busy} onClick={() => {setConversationId(newConversation());setContextNotice('');setMessages([]);setArtifact(null);setPanelOpen(false);setError('');setQuestion(''); input.current?.focus();}}><Plus aria-hidden="true"/></button><div className="rail-active" title="학습 대화"><MessageSquare aria-hidden="true" size={20}/></div><span className="rail-bottom">CS</span></nav>
    <div className="workspace"><header><a className="wordmark" href="/">CS Studio<span>생각을 연결하는 학습</span></a><div className="header-right">
            <button className="curriculum-toggle" aria-pressed={curriculumOpen} onClick={() => setCurriculumOpen(!curriculumOpen)}>
              {curriculumOpen ? "학습 대화로" : "커리큘럼 초안"}
            </button><span className="local-label"><i/> 로컬 AI</span>{artifact && !panelOpen && <button aria-label="도식 열기" onClick={() => setPanelOpen(true)}><PanelRightOpen aria-hidden="true" size={20}/></button>}</div></header>
    <div hidden={!curriculumOpen} className="curriculum-container"><CurriculumDraft /></div>
    <main style={curriculumOpen ? { display: 'none' } : undefined} className={panelOpen && artifact ? 'with-panel' : ''}>
      <section className="conversation" aria-label="학습 대화"><div className="scroll-area">
        {messages.length === 0 ? <div className="welcome"><div className="eyebrow">YOUR SPACE TO UNDERSTAND</div><h1>어떤 개념을<br/>함께 풀어볼까요?</h1><p>질문에서 이해로.<br className="mobile-break"/> 복잡한 개념은 그림으로 연결해 보세요.</p><div className="suggestions">{suggestions.map(s => <button key={s} onClick={() => send(s)}>{s}<ArrowUpRight aria-hidden="true" size={16}/></button>)}</div></div> : <div className="messages">{messages.map((m, i) => <article className={`message ${m.role}`} key={i}>{m.role === 'assistant' && <div className="assistant-label"><Layers aria-hidden="true" size={16}/> CS Studio</div>}{m.failed && <p role="status">완료되지 않은 문답 · 다음 답변의 대화 기록에서 제외됩니다.</p>}<div className="message-content">{m.role === 'assistant' ? <Markdown>{m.content}</Markdown> : m.content}</div>{m.artifact && <button className="artifact-chip" onClick={() => {setArtifact(m.artifact!);setPanelOpen(true);}}><Check aria-hidden="true" size={14}/>{m.artifact.diagram.title}<ArrowUpRight aria-hidden="true" size={14}/></button>}</article>)}</div>}
        {busy && draft && <article className="message assistant streaming"><div className="assistant-label"><Layers aria-hidden="true" size={16}/> CS Studio</div><div className="message-content"><Markdown>{draft}</Markdown></div></article>}
        {busy && <div className="status" role="status"><LoaderCircle aria-hidden="true" size={15} className="spin"/>{status}</div>}
        {error && <div className="error" role="alert">{error}{artifact && <span> 생성된 도식은 오른쪽 패널에서 확인할 수 있습니다.</span>}</div>}<div ref={bottom}/>
      </div><div className="composer-area"><form onSubmit={e => {e.preventDefault();send(question);}}><label className="sr-only" htmlFor="question">질문</label><textarea id="question" ref={input} value={question} maxLength={4000} disabled={busy || loading} onChange={e => setQuestion(e.target.value)} onKeyDown={e => {if(e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing){e.preventDefault();send(question);}}} placeholder="궁금한 개념을 질문하세요" rows={2}/><div className="composer-footer"><span><Layers aria-hidden="true" size={14}/> 필요할 때 도식으로</span><button className="send" aria-label="질문 보내기" disabled={busy || loading || !question.trim()}>{busy ? <LoaderCircle aria-hidden="true" size={20} className="spin"/> : <ArrowUp aria-hidden="true" size={20}/>}</button></div></form><p className="footnote">{loading ? '대화를 불러오는 중입니다. ' : contextNotice} AI의 설명에는 오류가 있을 수 있어요. 중요한 개념은 자료와 함께 확인하세요.</p></div></section>
      {panelOpen && artifact && <aside className="artifact-panel" aria-label="생성된 도식"><div className="panel-header"><div><span className="eyebrow">VISUAL NOTE</span><h2>{artifact.diagram.title}</h2></div><button aria-label="도식 닫기" onClick={() => setPanelOpen(false)}><X aria-hidden="true" size={20}/></button></div><div className={`diagram-stage ${zoomed ? 'zoomed' : ''}`}><img key={artifact.artifact_id} src={artifact.url} alt={artifact.diagram.title} onError={() => setError('도식을 불러오지 못했습니다. 서버 연결을 확인해 주세요.')}/></div><div className="panel-footer"><button aria-label={zoomed ? "도식 화면에 맞춤" : "도식 확대"} onClick={() => setZoomed(!zoomed)}>{zoomed ? "화면에 맞춤" : "확대"}</button><a href={artifact.url} download={`${artifact.diagram.title}.svg`}><Download aria-hidden="true" size={15}/> SVG 저장</a></div></aside>}
    </main></div></div>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
