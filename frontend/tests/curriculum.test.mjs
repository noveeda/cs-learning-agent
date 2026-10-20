import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createCurriculum, loadCurriculum, stepCurriculum } from '../src/curriculum.ts';
const input = { topic: '페이징', goal: '취업 준비', level: '기초' };
const job = { id: 'a'.repeat(32), version: 0, state: { request: input, stage: 'outline', units: [] } };
test('creates a job, restores it and advances using a checkpoint version', async t => {
  const seen = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    seen.push([url, options.body && JSON.parse(options.body)]);
    return Response.json(job);
  });
  assert.deepEqual(await createCurriculum(input), job);
  assert.deepEqual(await loadCurriculum(job.id), job);
  assert.deepEqual(await stepCurriculum(job), job);
  assert.deepEqual(seen, [['/api/curriculum/draft', input], [`/api/curriculum/draft/${job.id}`, undefined],
    [`/api/curriculum/draft/${job.id}/step`, { version: 0 }]]);
});
test('failed step can retry with the same version', async t => {
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => ++calls === 1
    ? new Response('private error', { status: 503 }) : Response.json(job));
  await assert.rejects(stepCurriculum(job), /다른 요청/);
  assert.deepEqual(await stepCurriculum(job), job);
});
test('rejects malformed checkpoint', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ ...job, state: { ...job.state, stage: 'unknown' } }));
  await assert.rejects(loadCurriculum(job.id), /형식/);
});
test('network failures preserve a recoverable user message', async t => {
  t.mock.method(globalThis, 'fetch', async () => { throw new TypeError('Failed to fetch'); });
  await assert.rejects(stepCurriculum(job), /이어서 생성/);
});

test('rejects malformed units instead of crashing the result screen', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ ...job, state: { ...job.state, units: [{ id: 'u1' }] } }));
  await assert.rejects(loadCurriculum(job.id), /형식/);
});
