import { useRef, useState } from 'react';
import { requestCurriculum, type CurriculumDraft as Draft, type DraftInput } from './curriculum';

export function CurriculumDraft() {
  const [input, setInput] = useState<DraftInput>({ topic: '', goal: '', level: '' });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const inFlight = useRef(false);

  async function generate(event: React.FormEvent) {
    event.preventDefault();
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError('');
    setDraft(null);
    try {
      setDraft(await requestCurriculum(input));
    } catch (err) {
      setError(err instanceof Error ? err.message : '초안을 생성하지 못했습니다. 다시 시도해 주세요.');
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return <section className="curriculum" aria-labelledby="curriculum-title">
    <h1 id="curriculum-title">커리큘럼 초안</h1>
    <p>작은 주제부터 공부할 순서와 학습 목표를 받아보세요.</p>
    <p>초안은 저장되지 않으며, 새로고침하면 사라집니다.</p>
    <form onSubmit={generate}>
      {([['topic', '학습 주제', '예: 가상 메모리와 페이징'],
        ['goal', '학습 목적', '예: 취업 면접에서 동작 원리를 설명하고 싶어요'],
        ['level', '현재 수준', '예: 용어는 들어봤지만 페이지와 프레임이 헷갈려요']] as const).map(([key, label, placeholder]) =>
        <div key={key} className="curriculum-field"><label htmlFor={`curriculum-${key}`}>{label}</label>
          <textarea id={`curriculum-${key}`} value={input[key]} placeholder={placeholder}
            required maxLength={300} rows={2} disabled={busy}
            onChange={e => setInput({ ...input, [key]: e.target.value })} />
        </div>)}
      <button type="submit" disabled={busy || Object.values(input).some(value => !value.trim())}>
        {busy ? '초안 생성 중…' : error ? '다시 시도' : '초안 생성'}
      </button>
    </form>
    {busy && <p role="status">학습 순서를 구성하고 있어요. 잠시 기다려 주세요.</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {draft && <article aria-label="생성된 커리큘럼 초안">
      <h2>{draft.title}</h2>
      <p>AI가 생성한 초안입니다. 학습 순서와 목표가 적절한지 확인하세요.</p>
      <ol>{draft.lessons.map((lesson, index) => <li key={index}>
        <h3>{lesson.title}</h3>
        <ul>{lesson.objectives.map((objective, i) => <li key={i}>{objective}</li>)}</ul>
      </li>)}</ol>
    </article>}
  </section>;
}
