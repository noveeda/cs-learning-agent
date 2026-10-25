// 파일 역할: 막힘 세션 API 호출·오류 분류(409 다시 불러오기, 503 재시도)·진행 중 세션 저장과 복구를 UI 없이 검증
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  canRetry,
  createSession,
  entryKey,
  finishStuckSession,
  getSession,
  isFinished,
  loadStuckStore,
  rememberStuckSession,
  saveStuckStore,
  stepSession,
  supportedConcepts,
} from '../src/stuck.ts';

const id = '0123456789abcdef0123456789abcdef';
const entry = { kind: 'roadmap', concept: 'stack', return_to: '#/learn?concept=stack' };
const question = {
  kind: 'question', purpose: 'diagnosis', concept: { id: 'adt', title: '추상 자료형과 구현' },
  item: { id: 'adt-d1', format: 'short_exact', question: '...' },
};
const session = (version, view = question, stage = 'diagnose') => ({ id, version, stage, view, entry });
const storageKey = 'cs-stuck-sessions-v1';

// 역할: 요청 순서대로 응답을 돌려주고 보낸 요청을 기록하는 가짜 fetch
function mockFetch(t, ...responses) {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, init = {}) => {
    calls.push({ url, method: init.method ?? 'GET', body: init.body && JSON.parse(init.body) });
    const next = responses.shift();
    if (next instanceof Error) throw next;
    return next;
  });
  return calls;
}

test('starts a session with only the entry and reads it back for resuming', async (t) => {
  const calls = mockFetch(t, Response.json(session(1)), Response.json(session(3)));
  assert.deepEqual(await createSession(entry), session(1));
  assert.deepEqual(await getSession(id), session(3));
  assert.deepEqual(calls, [
    { url: '/api/stuck/sessions', method: 'POST', body: { entry } },
    { url: `/api/stuck/sessions/${id}`, method: 'GET', body: undefined },
  ]);
});

test('sends the last received version with the input and returns the next checkpoint', async (t) => {
  const result = session(2, { kind: 'diagnosis_result' }, 'remediate');
  const calls = mockFetch(t, Response.json(result));
  const input = { type: 'answer', item: 'adt-d1', answer: '스택' };
  assert.deepEqual(await stepSession(session(1), input), { session: result, reloaded: false });
  assert.deepEqual(calls[0], { url: `/api/stuck/sessions/${id}/step`, method: 'POST', body: { version: 1, input } });
});

test('reloads the latest saved step after 409 instead of showing a stale screen', async (t) => {
  const latest = session(4);
  const calls = mockFetch(t,
    Response.json({ message: '세션 상태가 변경됐습니다. 다시 불러와 주세요.' }, { status: 409 }),
    Response.json(latest));
  assert.deepEqual(await stepSession(session(2), { type: 'continue' }), { session: latest, reloaded: true });
  assert.equal(calls[1].url, `/api/stuck/sessions/${id}`);
});

test('keeps server messages and marks only temporary failures as retryable', async (t) => {
  mockFetch(t,
    Response.json({ message: '지금은 이 단계를 처리할 수 없습니다. 잠시 후 다시 시도해 주세요.' }, { status: 503 }),
    new Response('Bad Gateway', { status: 502 }),
    Response.json({ message: '처리 시간이 초과됐습니다.' }, { status: 504 }),
    new TypeError('fetch failed'),
    Response.json({ message: '막힘 세션을 찾을 수 없습니다.' }, { status: 404 }),
    Response.json({ message: '입력 형식이 올바르지 않습니다.' }, { status: 422 }));
  const failures = [];
  for (let i = 0; i < 6; i += 1) {
    await stepSession(session(1), { type: 'continue' }).catch((error) => failures.push(error));
  }
  assert.deepEqual(failures.map((error) => [error.status, canRetry(error)]), [
    [503, true], [502, true], [504, true], [0, true], [404, false], [422, false],
  ]);
  assert.match(failures[0].message, /잠시 후 다시 시도/);
  assert.match(failures[1].message, /막힘 세션을 처리하지 못했습니다/);
  assert.match(failures[3].message, /서버에 연결할 수 없습니다/);
});

test('keys sessions by entry and recognizes finished stages', () => {
  assert.equal(entryKey(entry), 'roadmap:stack');
  assert.equal(entryKey({ kind: 'excerpt', excerpt: '문장', return_to: '#/learn' }), 'excerpt:#/learn');
  assert.equal(isFinished('complete'), true);
  assert.equal(isFinished('out_of_scope'), true);
  assert.equal(isFinished('check'), false);
  // MVP는 문항 은행이 있는 선형 구조 11개 개념만 [막혔어요]를 지원
  assert.equal(supportedConcepts.size, 11);
  assert.ok(supportedConcepts.has('stack') && !supportedConcepts.has('bfs'));
});

test('remembers in-progress sessions per entry, reopens the active one and drops finished ones', () => {
  let saved = null;
  const storage = { getItem: () => saved, setItem: (key, value) => { assert.equal(key, storageKey); saved = value; } };
  rememberStuckSession('roadmap:stack', id, storage);
  rememberStuckSession('excerpt:#/learn', 'abcdef0123456789abcdef0123456789', storage);
  assert.deepEqual(loadStuckStore(storage), {
    sessions: { 'roadmap:stack': id, 'excerpt:#/learn': 'abcdef0123456789abcdef0123456789' },
    active: 'excerpt:#/learn',
  });
  finishStuckSession('excerpt:#/learn', storage);
  assert.deepEqual(loadStuckStore(storage), { sessions: { 'roadmap:stack': id }, active: null });
  saveStuckStore({ ...loadStuckStore(storage), active: 'roadmap:stack' }, storage);
  assert.equal(loadStuckStore(storage).active, 'roadmap:stack');
});

test('restarting drops only that entry so reopening starts a new session, keeping other sessions', () => {
  // "처음부터 다시": 서버 세션은 그대로 두고 브라우저의 진행 중 목록에서만 빼서 다음 열기가 이어서 열기가 되지 않게 함
  let saved = null;
  const storage = { getItem: () => saved, setItem: (_, value) => { saved = value; } };
  const other = 'abcdef0123456789abcdef0123456789';
  rememberStuckSession(entryKey(entry), id, storage);
  rememberStuckSession('roadmap:queue', other, storage);
  finishStuckSession(entryKey(entry), storage);
  assert.deepEqual(loadStuckStore(storage), { sessions: { 'roadmap:queue': other }, active: 'roadmap:queue' });
  assert.equal(entryKey(entry) in loadStuckStore(storage).sessions, false);
});

test('ignores damaged or blocked storage and malformed session IDs', () => {
  const empty = { sessions: {}, active: null };
  for (const raw of [null, '{broken', '[]', '{"version":2}', '{"version":1,"sessions":[]}']) {
    assert.deepEqual(loadStuckStore({ getItem: () => raw }), empty);
  }
  assert.deepEqual(loadStuckStore({ getItem() { throw new Error('denied'); } }), empty);
  assert.deepEqual(loadStuckStore({ getItem: () => JSON.stringify({
    version: 1,
    sessions: { 'roadmap:stack': '../../etc', 'roadmap:queue': id, other: id },
    active: 'roadmap:stack',
  }) }), { sessions: { 'roadmap:queue': id }, active: null });
});

test('reports save failures so the panel can explain that resuming may not work', () => {
  const storage = { getItem: () => null, setItem() { throw new Error('quota exceeded'); } };
  assert.throws(() => rememberStuckSession('roadmap:stack', id, storage), /quota exceeded/);
});
