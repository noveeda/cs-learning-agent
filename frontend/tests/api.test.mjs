// 파일 역할: 네트워크 조각 경계와 중단·도식 이벤트 및 대화 복구를 UI 없이 검증
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { requestChat, loadConversation } from '../src/api.ts';

// 역할: 일부러 응답을 조각내 한글 바이트와 JSON 줄 경계 처리 검증
function stream(text) {
  const bytes = new TextEncoder().encode(text);
  return new Response(new ReadableStream({
    start(controller) {
      // Split through UTF-8 characters and JSON lines, like a real network stream.
      for (let i = 0; i < bytes.length; i += 7) controller.enqueue(bytes.slice(i, i + 7));
      controller.close();
    },
  }));
}

test('preserves Korean chunks, artifacts and the final line without newline', async (t) => {
  const events = [
    { type: 'artifact', artifact_id: 'id', url: '/api/artifacts/id', diagram: { title: '도식' } },
    { type: 'answer', text: '페이지와 프레임' },
    { type: 'done' },
  ];
  t.mock.method(globalThis, 'fetch', async (_, request) => {
    assert.deepEqual(JSON.parse(request.body), { question: '그려줘', conversation_id: 'test-session' });
    return stream(events.map(e => JSON.stringify(e)).join('\n'));
  });
  const observed = [];
  const result = await requestChat('그려줘', 'test-session', e => observed.push(e));
  assert.equal(result.answer, '페이지와 프레임');
  assert.equal(result.artifact.artifact_id, 'id');
  assert.deepEqual(observed, events);
});

test('keeps an emitted artifact when the server then reports an error', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => stream(
    '{"type":"artifact","artifact_id":"id","url":"/api/artifacts/id","diagram":{"title":"도식"}}\n' +
    '{"type":"error","message":"생성 실패"}\n'));
  const observed = [];
  await assert.rejects(requestChat('test', 'test-session', e => observed.push(e)), /생성 실패/);
  assert.equal(observed[0].type, 'artifact');
});

test('rejects incomplete responses and HTTP failures', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => stream('{"type":"answer","text":"미완료"}\n'));
  await assert.rejects(requestChat('test', 'test-session', () => {}), /중단/);
  fetch.mock.mockImplementation(async () => new Response('', { status: 422 }));
  await assert.rejects(requestChat('test', 'test-session', () => {}), /너무 깁니다/);
});


test('delivers text deltas before the final response arrives', async (t) => {
  let source;
  t.mock.method(globalThis, 'fetch', async () => new Response(new ReadableStream({
    start(controller) { source = controller; },
  })));
  const encoder = new TextEncoder();
  let resolveDelta;
  const firstDelta = new Promise(resolve => { resolveDelta = resolve; });
  let finished = false;
  const request = requestChat('질문', 'test-session', event => {
    if (event.type === 'text_delta') resolveDelta(event.text);
  }).then(result => { finished = true; return result; });
  source.enqueue(encoder.encode('{"type":"text_start"}\n{"type":"text_delta","text":"첫 문장"}\n'));
  assert.equal(await firstDelta, '첫 문장');
  assert.equal(finished, false);
  source.enqueue(encoder.encode('{"type":"answer","text":"첫 문장"}\n{"type":"done"}\n'));
  source.close();
  assert.equal((await request).answer, '첫 문장');
});

test('restores persisted messages, diagrams and failed status', async (t) => {
  t.mock.method(globalThis, 'fetch', async url => {
    assert.equal(url, '/api/conversations/session');
    return Response.json({ messages: [
      { role: 'user', content: '질문', status: 'complete' },
      { role: 'assistant', content: '답변', status: 'complete',
        artifacts: [{ artifact_id: 'diagram', url: '/api/artifacts/diagram', diagram: { title: '도식' } }] },
      { role: 'user', content: '중단된 질문', status: 'pending' },
    ] });
  });
  const messages = await loadConversation('session');
  assert.equal(messages[1].artifact.artifact_id, 'diagram');
  assert.equal(messages[0].failed, false);
  assert.equal(messages[2].failed, true);
});

test('sends the selected artifact and session ID, without client history', async (t) => {
  t.mock.method(globalThis, 'fetch', async (_, request) => {
    assert.deepEqual(JSON.parse(request.body), {
      question: '바꿔줘', conversation_id: 'session', artifact_id: 'diagram',
    });
    return stream('{"type":"answer","text":"완료"}\n{"type":"done"}\n');
  });
  await requestChat('바꿔줘', 'session', () => {}, 'diagram');
});
