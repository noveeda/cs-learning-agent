/** @file
 * @brief 반 요약 API의 응답 검증·네트워크 오류 안내·캐시 금지 검증
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as api from '../src/api.ts';

const empty = { totalSessions: 0, diagnosedSessions: 0, earliestAt: null, latestAt: null,
  targets: [], gaps: [], pairs: [], crossSubjectPairs: 0, outcomes: [] };

/** @brief 유효한 0건 응답을 유지하고 읽기 전용 URL과 캐시 정책을 적용 */
test('loads class aggregates without changing zero values', async (t) => {
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/api/stuck/summary');
    assert.equal(options.cache, 'no-store');
    return Response.json(empty);
  });
  assert.deepEqual(await api.loadClassSummary(), empty);
});

/** @brief 손상 응답이나 분모 위반을 화면 통계로 사용하지 않음 */
test('rejects malformed class summary responses', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch');
  for (const body of [{}, { ...empty, totalSessions: -1 }, { ...empty, diagnosedSessions: 1 }, { ...empty, gaps: null }]) {
    fetch.mock.mockImplementation(async () => Response.json(body));
    await assert.rejects(api.loadClassSummary(), /집계 응답 형식/);
  }
});

/** @brief HTTP 실패와 JSON이 아닌 프록시 오류 및 연결 오류를 사용자 안내로 바꿈 */
test('turns server and network failures into useful summary errors', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => new Response('<html>bad gateway</html>', { status: 502 }));
  await assert.rejects(api.loadClassSummary(), /반 요약을 불러오지 못했습니다/);
  fetch.mock.mockImplementation(async () => { throw new TypeError('network'); });
  await assert.rejects(api.loadClassSummary(), /서버에 연결할 수 없습니다/);
});
