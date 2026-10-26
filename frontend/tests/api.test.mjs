// 파일 역할: 네트워크 조각 경계와 중단·도식 이벤트 및 대화 복구를 UI 없이 검증
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { artifactNoun, artifactTitle, isAnimationArtifact, requestChat, loadConversation, toolProgress } from '../src/api.ts';

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

// AI core create_animation 결과와 같은 모양의 애니메이션 artifact; 도식과 달리 SVG 경로(path·url)가 없음
const animationArtifact = {
  ok: true, artifact_id: 'b'.repeat(32), kind: 'animation',
  animation: { kind: 'stack', title: '스택 push·pop', frames: [
    { boxes: [], arrows: [], caption: '빈 스택에서 시작합니다' },
    { boxes: [{ id: 's0', label: '1', x: 0, y: 0, shape: 'cell', tone: 'new' }], arrows: [], caption: 'push 1: 1을 top 위에 올립니다' },
  ] },
  request: { kind: 'stack', ops: [{ op: 'push', value: 1 }] },
};

// 검증 목적: 스트림 파서가 kind:'animation' artifact를 프레임까지 그대로 보존하고, SVG 경로가 없어도 오류로 처리하지 않는지 확인
test('keeps animation artifacts without an SVG path', async (t) => {
  const events = [{ type: 'artifact', ...animationArtifact }, { type: 'answer', text: '스택 과정' }, { type: 'done' }];
  t.mock.method(globalThis, 'fetch', async () => stream(events.map(e => JSON.stringify(e)).join('\n')));
  const observed = [];
  const result = await requestChat('스택 보여줘', 'session', e => observed.push(e));
  assert.equal(result.artifact.kind, 'animation');
  assert.deepEqual(result.artifact.animation, animationArtifact.animation);
  assert.equal(result.artifact.url, undefined);
  assert.deepEqual(observed, events);
});

// 검증 목적: 저장된 대화의 애니메이션 artifact도 복구되고, 화면이 종류에 맞는 제목·패널을 고를 수 있는지 확인
test('restores animation artifacts and tells them apart from diagrams', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ messages: [
    { role: 'user', content: '스택 보여줘', status: 'complete' },
    { role: 'assistant', content: '스택 과정', status: 'complete', artifacts: [{ type: 'artifact', ...animationArtifact }] },
  ] }));
  const [, answer] = await loadConversation('session');
  assert.equal(isAnimationArtifact(answer.artifact), true);
  assert.equal(artifactTitle(answer.artifact), '스택 push·pop');
  const diagram = { artifact_id: 'diagram', url: '/api/artifacts/diagram', diagram: { title: '도식' } };
  assert.equal(isAnimationArtifact(diagram), false);
  assert.equal(artifactTitle(diagram), '도식');
});

// 검증 목적: 패널·버튼 문구가 artifact 종류에 맞는 이름(도식·애니메이션)을 쓰는지 확인
test('names artifacts by kind', () => {
  assert.equal(artifactNoun(animationArtifact), '애니메이션');
  assert.equal(artifactNoun({ artifact_id: 'd', url: '/api/artifacts/d', diagram: { title: '도식' } }), '도식');
});

// 검증 목적: 도구 진행 안내가 이벤트의 도구 이름으로 갈려, 애니메이션을 만드는 동안 "도식"이라고 안내하지 않는지 확인
test('describes tool progress by tool name', () => {
  assert.match(toolProgress('show_animation', false), /애니메이션/);
  assert.match(toolProgress('show_animation', true), /애니메이션/);
  assert.match(toolProgress('create_diagram', false), /도식/);
  assert.match(toolProgress('create_diagram', true), /도식/);
  assert.doesNotMatch(toolProgress('show_animation', false), /도식/);
});
