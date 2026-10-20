import { test } from 'node:test';
import assert from 'node:assert/strict';
import { requestCurriculum } from '../src/curriculum.ts';
const input = { topic: '페이징', goal: '취업 준비', level: '기초' };
const draft = { title: '페이징', lessons: [
  { title: '페이지', objectives: ['프레임과 구분한다'] },
  { title: '주소 변환', objectives: ['변환을 설명한다'] },
] };
test('sends learning input and returns a structured draft', async t => {
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/api/curriculum/draft');
    assert.deepEqual(JSON.parse(options.body), input);
    return Response.json(draft);
  });
  assert.deepEqual(await requestCurriculum(input), draft);
});
test('failed generation can be retried without saving state', async t => {
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => ++calls === 1
    ? new Response('private error', { status: 503 }) : Response.json(draft));
  await assert.rejects(requestCurriculum(input), /다른 요청/);
  assert.deepEqual(await requestCurriculum(input), draft);
});
test('rejects malformed successful responses', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ title: '빈 초안', lessons: [] }));
  await assert.rejects(requestCurriculum(input), /형식/);
});

test('network failures show a recoverable Korean message', async t => {
  t.mock.method(globalThis, 'fetch', async () => { throw new TypeError('Failed to fetch'); });
  await assert.rejects(requestCurriculum(input), /서버에 연결하지 못했습니다/);
});

test('accepts one lesson and outputs beyond the old lesson and objective caps', async t => {
  for (const count of [1, 7]) {
    const generated = { title: '학습', lessons: Array.from({ length: count }, (_, i) => ({
      title: `소단원 ${i}`, objectives: ['목표 A', '목표 B', '목표 C', '목표 D'],
    })) };
    t.mock.method(globalThis, 'fetch', async () => Response.json(generated));
    assert.deepEqual(await requestCurriculum(input), generated);
  }
});
test('still rejects lessons without learning objectives', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ title: '학습', lessons: [{ title: '소단원', objectives: [] }] }));
  await assert.rejects(requestCurriculum(input), /형식/);
});
