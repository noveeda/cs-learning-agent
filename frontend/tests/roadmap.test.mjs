import { test } from 'node:test';
import assert from 'node:assert/strict';
import { concepts, loadRoadmap, saveRoadmap } from '../src/roadmap.ts';

const emptyState = { selectedId: concepts[0].id, statuses: {}, conversationIds: {} };
const stored = value => ({ getItem: () => JSON.stringify(value) });

test('restores the selected concept, all statuses and separate conversation IDs', () => {
  const state = {
    selectedId: 'http',
    statuses: { process: 'completed', scheduling: 'in-progress', http: 'not-started' },
    conversationIds: { process: '0123456789abcdef0123456789abcdef', http: 'abcdef0123456789abcdef0123456789' },
  };
  let serialized;
  saveRoadmap(state, { setItem(key, value) {
    assert.equal(key, 'cs-roadmap-v1');
    serialized = value;
  } });
  assert.deepEqual(loadRoadmap({ getItem(key) {
    assert.equal(key, 'cs-roadmap-v1');
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

test('sanitizes unknown concepts, invalid statuses and malformed conversation IDs', () => {
  assert.deepEqual(loadRoadmap(stored({
    version: 1,
    selectedId: 'removed',
    statuses: { process: 'completed', scheduling: 'unexpected', removed: 'completed' },
    conversationIds: { process: '123456789abcdef0123456789abcdef0', scheduling: '../../other', http: '', removed: 'old-id' },
  })), {
    selectedId: concepts[0].id,
    statuses: { process: 'completed' },
    conversationIds: { process: '123456789abcdef0123456789abcdef0' },
  });
  for (const malformed of [null, [], 'completed', 1]) {
    assert.deepEqual(loadRoadmap(stored({ version: 1, statuses: malformed, conversationIds: malformed })), emptyState);
  }
});

test('reports save failures so the screen can explain unsaved progress', () => {
  assert.throws(() => saveRoadmap(emptyState, { setItem() { throw new Error('quota exceeded'); } }), /quota exceeded/);
});

test('all prerequisite links point to existing concepts and graph positions fit the canvas', () => {
  const ids = new Set(concepts.map(concept => concept.id));
  assert.equal(ids.size, concepts.length);
  for (const concept of concepts) {
    assert.ok(concept.prerequisites.every(id => ids.has(id) && id !== concept.id));
    assert.ok(concept.position.x >= 0 && concept.position.x <= 100);
    assert.ok(concept.position.y >= 0 && concept.position.y <= 100);
  }
});
