// 파일 역할: 개념 지도 응답 해석·선수 관계 단계 배치·손상된 브라우저 저장값 복구를 검증
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { loadConceptMap } from '../src/api.ts';
import { filterBySubject, layoutConcepts, listSubjects, loadRoadmap, saveRoadmap } from '../src/roadmap.ts';

// 실제 화면이 받는 것과 같은 검수 완료 지도로 배치를 검증; 데이터가 바뀌어도 규칙 위반을 잡기 위함
const conceptMap = JSON.parse(readFileSync(new URL('../../ai-core/content/dsa/concept-map.json', import.meta.url)));
const storageKey = 'cs-dsa-roadmap-v1';
const emptyState = { selectedId: '', statuses: {}, conversationIds: {} };
const stored = value => ({ getItem: () => JSON.stringify(value) });

// 역할: 테스트마다 /api/concept-map 응답을 바꿔 끼우고 요청 주소 확인
function mockMap(t, response) {
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/api/concept-map');
    assert.equal(options.cache, 'no-store');
    return response();
  });
}

test('converts the API concept map into roadmap concepts with objectives and reasons', async (t) => {
  mockMap(t, () => Response.json(conceptMap));
  const concepts = await loadConceptMap();
  assert.equal(concepts.length, 36);
  const linked = concepts.find(concept => concept.id === 'linked-list');
  assert.equal(linked.domain, '자료구조·알고리즘');
  assert.equal(linked.objectives.length, 2);
  assert.deepEqual(linked.prerequisites.map(p => p.id), ['reference', 'array']);
  assert.ok(linked.prerequisites.every(p => p.reason.trim()));
});

// 역할: 여러 과목을 합친 지도 응답(AI core MVP 4 형식) 생성; 자료구조 지도 뒤에 운영체제 개념 2개를 붙임
// - process-state → queue는 과목 사이 선수 관계, fcfs → process-state는 같은 과목 안 관계
function mixedMap() {
  const os = (id, title, prerequisite) => ({
    id, title, description: title, objectives: [`${title}을 설명할 수 있다.`],
    prerequisites: [{ id: prerequisite, reason: '근거' }], subject: 'os',
  });
  return {
    ...conceptMap, id: 'cs', title: 'CS 전공',
    subjects: [{ id: 'dsa', title: '자료구조·알고리즘' }, { id: 'os', title: '운영체제' }],
    concepts: [
      ...conceptMap.concepts.map(concept => ({ ...concept, subject: 'dsa' })),
      os('process-state', '프로세스와 상태', 'queue'),
      os('fcfs', 'FCFS 스케줄링', 'process-state'),
    ],
  };
}

test('uses each concept subject title as its domain in a multi-subject map', async (t) => {
  mockMap(t, () => Response.json(mixedMap()));
  const concepts = await loadConceptMap();
  const byId = new Map(concepts.map(concept => [concept.id, concept]));
  assert.equal(byId.get('queue').subject, 'dsa');
  assert.equal(byId.get('queue').domain, '자료구조·알고리즘');
  assert.equal(byId.get('fcfs').subject, 'os');
  assert.equal(byId.get('fcfs').domain, '운영체제');
  assert.deepEqual(listSubjects(concepts), [
    { id: 'dsa', title: '자료구조·알고리즘' }, { id: 'os', title: '운영체제' },
  ]);
});

// 검증 목적: subject·subjects가 없는 옛 응답(MVP 4 이전 AI core)도 자료구조 과목으로 읽어 화면이 깨지지 않게 함
test('treats a map without subjects as the data structures subject', async (t) => {
  mockMap(t, () => Response.json(conceptMap));
  const concepts = await loadConceptMap();
  assert.ok(concepts.every(concept => concept.subject === 'dsa' && concept.domain === '자료구조·알고리즘'));
  assert.deepEqual(listSubjects(concepts), [{ id: 'dsa', title: '자료구조·알고리즘' }]);
});

// 검증 목적: 과목 필터가 다른 과목 개념을 빼면서 그 개념을 가리키던 선수 관계도 빼서, 화면에 끝이 없는 선이 남지 않게 함
test('filters concepts by subject without leaving prerequisites to hidden concepts', async (t) => {
  mockMap(t, () => Response.json(mixedMap()));
  const concepts = await loadConceptMap();
  const os = filterBySubject(concepts, 'os');
  assert.deepEqual(os.map(concept => concept.id), ['process-state', 'fcfs']);
  assert.deepEqual(os.find(concept => concept.id === 'process-state').prerequisites, []);
  assert.deepEqual(os.find(concept => concept.id === 'fcfs').prerequisites.map(p => p.id), ['process-state']);
  assert.deepEqual(layoutConcepts(os).layers, [['process-state'], ['fcfs']]);
  // 원본 개념은 바꾸지 않음: 상세 패널은 과목 사이 선수 관계(queue)를 그대로 보여 줘야 함
  assert.deepEqual(concepts.find(concept => concept.id === 'process-state').prerequisites.map(p => p.id), ['queue']);
  assert.equal(filterBySubject(concepts, 'all'), concepts);
});

test('shows the server message for failed responses and rejects malformed maps', async (t) => {
  mockMap(t, () => Response.json({ message: '개념 지도를 불러오지 못했습니다. AI core 상태를 확인해 주세요.' }, { status: 502 }));
  await assert.rejects(loadConceptMap(), /AI core 상태를 확인/);
  mockMap(t, () => new Response('Bad Gateway', { status: 502 }));
  await assert.rejects(loadConceptMap(), /개념 지도를 불러오지 못했습니다/);
  mockMap(t, () => Response.json({ concepts: 'broken' }));
  await assert.rejects(loadConceptMap(), /형식이 올바르지 않습니다/);
  t.mock.method(globalThis, 'fetch', async () => { throw new TypeError('fetch failed'); });
  await assert.rejects(loadConceptMap(), /서버에 연결할 수 없습니다/);
});

test('places every prerequisite in an earlier layer so the study direction reads top to bottom', async (t) => {
  mockMap(t, () => Response.json(conceptMap));
  const concepts = await loadConceptMap();
  const { layers, positions } = layoutConcepts(concepts);
  assert.equal(layers.flat().length, concepts.length);
  assert.equal(new Set(layers.flat()).size, concepts.length);
  for (const concept of concepts) {
    for (const { id } of concept.prerequisites)
      assert.ok(positions[id].layer < positions[concept.id].layer, `${id} → ${concept.id}`);
    // 최장 경로 단계: 선수 개념이 있으면 바로 앞 단계에 적어도 하나가 있어 불필요하게 아래로 밀리지 않음
    if (concept.prerequisites.length)
      assert.equal(Math.max(...concept.prerequisites.map(p => positions[p.id].layer)) + 1, positions[concept.id].layer);
    else assert.equal(positions[concept.id].layer, 0);
  }
  // 한 줄 개념 수가 화면 너비 계산의 기준이므로 현재 지도에서 7개 이하인지 확인
  assert.ok(Math.max(...layers.map(row => row.length)) <= 7);
});

test('keeps each row centered with one slot per concept and returns the same layout every time', async (t) => {
  mockMap(t, () => Response.json(conceptMap));
  const concepts = await loadConceptMap();
  const layout = layoutConcepts(concepts);
  for (const row of layout.layers) {
    const xs = row.map(id => layout.positions[id].x);
    assert.deepEqual(xs, row.map((_, index) => index - (row.length - 1) / 2));
  }
  assert.deepEqual(layoutConcepts(concepts), layout);
});

test('restores the selected concept, all statuses and separate conversation IDs', () => {
  const state = {
    selectedId: 'stack',
    statuses: { array: 'completed', stack: 'in-progress', queue: 'not-started' },
    conversationIds: { array: '0123456789abcdef0123456789abcdef', stack: 'abcdef0123456789abcdef0123456789' },
  };
  let serialized;
  saveRoadmap(state, { setItem(key, value) {
    assert.equal(key, storageKey);
    serialized = value;
  } });
  assert.deepEqual(loadRoadmap({ getItem(key) {
    assert.equal(key, storageKey);
    return serialized;
  } }), state);
});

test('opens a fresh roadmap for absent, damaged or unsupported browser data', () => {
  for (const raw of [null, '{broken', 'null', '[]', '123', '{"version":2}', '{"statuses":{}}']) {
    assert.deepEqual(loadRoadmap({ getItem: () => raw }), emptyState);
  }
  assert.deepEqual(loadRoadmap({ getItem() { throw new Error('denied'); } }), emptyState);
});

test('opens a fresh roadmap when access to localStorage itself is blocked', () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  try {
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      get() { throw new Error('SecurityError'); },
    });
    assert.deepEqual(loadRoadmap(), emptyState);
  } finally {
    if (original) Object.defineProperty(globalThis, 'localStorage', original);
    else delete globalThis.localStorage;
  }
});

test('drops malformed keys, invalid statuses and conversation IDs but keeps well-formed unknown IDs', () => {
  // 지도에 없는 ID도 형식이 맞으면 보존: 지도가 다시 바뀌어도 사용자의 기록을 지우지 않기 위함
  assert.deepEqual(loadRoadmap(stored({
    version: 1,
    selectedId: '../../other',
    statuses: { array: 'completed', stack: 'unexpected', removed: 'completed', 'Bad Key': 'completed' },
    conversationIds: { array: '123456789abcdef0123456789abcdef0', stack: '../../other', queue: '', removed: 'old-id' },
  })), {
    selectedId: '',
    statuses: { array: 'completed', removed: 'completed' },
    conversationIds: { array: '123456789abcdef0123456789abcdef0' },
  });
  for (const malformed of [null, [], 'completed', 1]) {
    assert.deepEqual(loadRoadmap(stored({ version: 1, statuses: malformed, conversationIds: malformed })), emptyState);
  }
});

// 검증 목적: 과목이 생기기 전에 저장된 기록(cs-dsa-roadmap-v1 원문 그대로)을 바꾸지 않고 읽는지 확인
test('reads a roadmap record saved before subjects existed unchanged', () => {
  const raw = '{"version":1,"selectedId":"heap","statuses":{"array":"completed","heap":"in-progress"},'
    + '"conversationIds":{"heap":"0123456789abcdef0123456789abcdef"}}';
  assert.deepEqual(loadRoadmap({ getItem: key => (key === storageKey ? raw : null) }), {
    selectedId: 'heap',
    statuses: { array: 'completed', heap: 'in-progress' },
    conversationIds: { heap: '0123456789abcdef0123456789abcdef' },
  });
});

test('ignores the old 9-concept roadmap key without touching it', () => {
  const reads = [];
  const state = loadRoadmap({ getItem(key) {
    reads.push(key);
    return key === 'cs-roadmap-v1'
      ? JSON.stringify({ version: 1, selectedId: 'process', statuses: { process: 'completed' }, conversationIds: {} })
      : null;
  } });
  assert.deepEqual(state, emptyState);
  assert.deepEqual(reads, [storageKey]);
});

test('reports save failures so the screen can explain unsaved progress', () => {
  assert.throws(() => saveRoadmap(emptyState, { setItem() { throw new Error('quota exceeded'); } }), /quota exceeded/);
});
