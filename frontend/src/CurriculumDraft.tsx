import { useEffect, useRef, useState } from 'react';
import { createCurriculum, loadCurriculum, stepCurriculum, type CurriculumJob, type DraftInput } from './curriculum';

const KEY = 'cs-curriculum-job';
const stages = { outline: '개요 구성', generate: '단원 생성', review: '단원 검토', final: '전체 검토',
  repair: '부분 보완', complete: '초안 완성', blocked: '확인 필요' };

export function CurriculumDraft() {
  const [input, setInput] = useState<DraftInput>({ topic: '', goal: '', level: '' });
  const [job, setJob] = useState<CurriculumJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [pausing, setPausing] = useState(false);
  const inFlight = useRef(false);
  const stop = useRef(false);

  useEffect(() => {
    let cancelled = false;
    const id = localStorage.getItem(KEY);
    if (!id) { setLoading(false); return; }
    loadCurriculum(id).then(saved => {
      if (!cancelled) { setJob(saved); setInput(saved.state.request); }
    }).catch(err => { if (!cancelled) setError(err.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; stop.current = true; };
  }, []);

  async function generate(event?: React.FormEvent) {
    event?.preventDefault();
    if (inFlight.current || loading) return;
    inFlight.current = true;
    stop.current = false;
    setBusy(true); setPausing(false); setError('');
    try {
      const id = job?.id || localStorage.getItem(KEY);
      let current = id ? await loadCurriculum(id) : await createCurriculum(input);
      localStorage.setItem(KEY, current.id);
      setJob(current); setInput(current.state.request);
      while (!stop.current && !['complete', 'blocked'].includes(current.state.stage)) {
        current = await stepCurriculum(current);
        setJob(current);
        if (current.state.retries) {
          setError(current.state.message || '생성에 실패했습니다. 이어서 생성해 주세요.');
          break;
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '초안을 생성하지 못했습니다. 이어서 생성해 주세요.');
    } finally { inFlight.current = false; setBusy(false); setPausing(false); }
  }

  return <section className="curriculum" aria-labelledby="curriculum-title">
    <h1 id="curriculum-title">커리큘럼 초안</h1>
    <p>개요를 구성하고 단원별 학습 목표를 생성한 뒤 전체를 검토합니다.</p>
    <p>완료된 단계는 서버에 저장됩니다. 중단하거나 새로고침해도 이어서 생성할 수 있어요.</p>
    <form onSubmit={generate}>
      {([['topic', '학습 주제', '예: 가상 메모리와 페이징'],
        ['goal', '학습 목적', '예: 취업 면접에서 동작 원리를 설명하고 싶어요'],
        ['level', '현재 수준', '예: 페이지와 프레임이 헷갈려요']] as const).map(([key, label, placeholder]) =>
        <div key={key} className="curriculum-field"><label htmlFor={`curriculum-${key}`}>{label}</label>
          <textarea id={`curriculum-${key}`} value={input[key]} placeholder={placeholder}
            required maxLength={300} rows={2} disabled={busy || loading || !!job}
            onChange={e => setInput({ ...input, [key]: e.target.value })} />
        </div>)}
      <button type="submit" disabled={busy || loading || ['complete', 'blocked'].includes(job?.state.stage || '')
        || Object.values(input).some(value => !value.trim())}>
        {busy ? '생성 진행 중…' : job?.state.stage === 'complete' ? '초안 완성'
          : job?.state.stage === 'blocked' ? '생성 중단' : job ? '이어서 생성' : '초안 생성'}
      </button>
      {busy && <button type="button" disabled={pausing} onClick={() => { stop.current = true; setPausing(true); }}>
        {pausing ? '현재 단계 저장 후 멈춥니다' : '일시 중단'}
      </button>}
      {!busy && !loading && <button type="button" onClick={() => {
        localStorage.removeItem(KEY); setJob(null); setError('');
      }}>새 초안</button>}
    </form>
    {loading && <p role="status">저장된 생성 작업을 불러옵니다.</p>}
    {job && <p role="status">{stages[job.state.stage]} · {job.state.message || '생성을 시작합니다.'}
      {!busy && !['complete', 'blocked'].includes(job.state.stage) && ' 이어서 생성 버튼으로 계속할 수 있습니다.'}</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {job && job.state.units.length > 0 && <article aria-label="생성된 커리큘럼 초안">
      <h2>{job.state.request.topic}</h2>
      <p>{job.state.stage === 'complete' ? 'AI 검토를 마친 초안입니다. 내용 정확성은 직접 확인하세요.' : '작성 중인 결과입니다. 아직 전체 검토가 끝나지 않았습니다.'}</p>
      <ol>{job.state.units.map(unit => <li key={unit.id}>
        <h3>{unit.plan.title}</h3><p>{unit.plan.scope}</p>
        <p>{unit.reviewed ? '단원 검토됨' : '작성·검토 중'}</p>
        <ol>{unit.plan.topics.map((topic, i) => <li key={i}>
          <strong>{topic}</strong>
          {unit.lessons[i] ? <ul>{unit.lessons[i].objectives.map((objective, j) => <li key={j}>{objective}</li>)}</ul> : <p>학습 목표 생성 대기</p>}
        </li>)}</ol>
        {unit.feedback?.length > 0 && <div><strong>보완 필요</strong><ul>{unit.feedback.map((item, i) => <li key={i}>{item}</li>)}</ul></div>}
      </li>)}</ol>
    </article>}
  </section>;
}
