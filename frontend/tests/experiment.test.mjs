// 파일 역할: 비교 실험 화면의 API 호출(중복 참가자 409·마감 직후 timeout 409)·남은 시간 계산·저장 복구를 UI 없이 검증
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  ExperimentError,
  allAnswered,
  createExperiment,
  formatClock,
  getExperiment,
  loadExperimentId,
  postEvent,
  remainingAt,
  saveExperimentId,
  submitPretest,
  validParticipant,
} from '../src/experiment.ts';

const id = '0123456789abcdef0123456789abcdef';

// 역할: 요청 순서대로 응답을 돌려주고 보낸 요청을 기록하는 가짜 fetch
function mockFetch(t, ...responses) {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, init = {}) => {
    calls.push({ url, method: init.method ?? 'GET', body: init.body && JSON.parse(init.body) });
    return responses.shift();
  });
  return calls;
}

// 역할: localStorage 대신 쓰는 메모리 저장소
function memoryStorage() {
  const data = new Map();
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => data.set(k, String(v)), removeItem: (k) => data.delete(k) };
}

// 검증 목적: 남은 시간은 서버가 준 remaining_seconds에서 계산하므로, 새로고침해 서버 값이 줄어 있으면 화면도 줄어든 값을 보여야 함(Review Focus 1)
test('counts down from the server remaining seconds and never goes below zero', () => {
  const loadedAt = 1_000_000;
  assert.equal(remainingAt(loadedAt + 300 * 1000, loadedAt), 300);
  assert.equal(remainingAt(loadedAt + 300 * 1000, loadedAt + 10_500), 290);
  assert.equal(remainingAt(loadedAt + 300 * 1000, loadedAt + 400_000), 0);
  assert.equal(formatClock(900), '15:00');
  assert.equal(formatClock(61), '01:01');
  assert.equal(formatClock(0), '00:00');
});

// 검증 목적: 진행자가 같은 참가자 코드를 다시 만들면 덮어쓰지 않고 기존 세션 id를 받아 재개를 고르게 함(Review Focus 5)
test('returns the existing session id when the participant already exists', async (t) => {
  const calls = mockFetch(t,
    Response.json({ message: 'S3 세션이 이미 있습니다', id }, { status: 409 }),
    Response.json({ id, participant: 'S4', stage: 'pretest', pretest: [] }));
  const conflict = await createExperiment({ participant: 'S3', grade: 3, dsa: 'taken', exclude: [] });
  assert.deepEqual(conflict, { conflict: true, id, message: 'S3 세션이 이미 있습니다' });
  const created = await createExperiment({ participant: 'S4', grade: 2, dsa: 'taking', exclude: ['bfs'] });
  assert.equal(created.conflict, false);
  assert.equal(created.view.stage, 'pretest');
  assert.deepEqual(calls[1], { url: '/api/experiments', method: 'POST',
    body: { participant: 'S4', grade: 2, dsa: 'taking', exclude: ['bfs'] } });
});

// 검증 목적: 마감 직후 도착한 timeout은 409여도 정상으로 보고, 다른 이벤트의 409는 오류로 올림(Review Focus 2)
test('treats a late timeout conflict as recorded but rejects other late events', async (t) => {
  const calls = mockFetch(t,
    Response.json({ message: '마감 전입니다' }, { status: 409 }),
    Response.json({ message: '마감이 지났습니다' }, { status: 409 }),
    Response.json({ passage_part2: '2부' }));
  assert.deepEqual(await postEvent(id, 'timeout'), {});
  await assert.rejects(postEvent(id, 'continue_reading'), (error) =>
    error instanceof ExperimentError && error.status === 409 && error.message === '마감이 지났습니다');
  assert.deepEqual(await postEvent(id, 'stuck_session', 'abc'), { passage_part2: '2부' });
  assert.deepEqual(calls.map((call) => call.body), [{ type: 'timeout' }, { type: 'continue_reading' }, { type: 'stuck_session', ref: 'abc' }]);
  assert.equal(calls[0].url, `/api/experiments/${id}/events`);
});

// 검증 목적: 답 제출 뒤에는 상태를 다시 읽어(GET) 서버가 정한 단계·조건만 화면에 씀
test('submits answers then reloads the session from the server', async (t) => {
  const learn = { id, participant: 'S1', stage: 'learn1', concept: { id: 'bst', title: '이진 탐색 트리' },
    condition: 'system', passage_part1: '1부', passage_part2: null, remaining_seconds: 900 };
  const calls = mockFetch(t, Response.json({ concept: 'bst' }), Response.json(learn));
  assert.deepEqual(await submitPretest(id, { 'exp-bst-A1': 'B' }), learn);
  assert.deepEqual(calls.map((c) => [c.method, c.url]), [
    ['POST', `/api/experiments/${id}/pretest`], ['GET', `/api/experiments/${id}`]]);
  assert.deepEqual(calls[0].body, { answers: { 'exp-bst-A1': 'B' } });
  mockFetch(t, Response.json({ message: '세션이 없습니다' }, { status: 404 }));
  await assert.rejects(getExperiment(id), (error) => error.status === 404);
});

// 검증 목적: 참가자 코드는 S1~S9만 받아 이름 같은 개인정보가 들어오지 않게 함, 빈 답 확인, 새로고침용 세션 id 저장
test('validates participant codes, checks answers and remembers the session id', () => {
  assert.equal(validParticipant('S1'), true);
  assert.equal(validParticipant('S10'), false);
  assert.equal(validParticipant('홍길동'), false);
  const items = [{ id: 'a' }, { id: 'b' }];
  assert.equal(allAnswered(items, { a: 'A', b: ' ' }), false);
  assert.equal(allAnswered(items, { a: 'A', b: '3' }), true);
  const storage = memoryStorage();
  assert.equal(loadExperimentId(storage), null);
  saveExperimentId(id, storage);
  assert.equal(loadExperimentId(storage), id);
  saveExperimentId(null, storage);
  assert.equal(loadExperimentId(storage), null);
});
