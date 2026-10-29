/**
 * @file
 * @brief 애니메이션 재생기의 순수 계산(고정 화면 크기·단계 범위)과 보충 화면 분기를 UI 없이 검증
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CELL_HEIGHT, CELL_WIDTH, VIEW_PADDING, clampStep, compactRows, fitViewBox, remediationParts, squeezesLabel } from '../src/animation.ts';

const box = (id, x, y) => ({ id, label: id, x, y, shape: 'cell', tone: 'normal' });

// 검증 목적: 단계마다 크기가 달라져 화면이 출렁이지 않도록, 모든 프레임 좌표의 합집합으로 크기를 고정하는지 확인
test('fits one view box to the largest coordinates across frames', () => {
  const frames = [
    { boxes: [box('a', 0, 0), box('b', 3, 0)], arrows: [], caption: '처음' },
    { boxes: [box('a', 1, 2)], arrows: [], caption: '이동' },
  ];
  // 위아래에 여백(VIEW_PADDING)을 조금 두어 맨 위·아래 상자가 테두리에 붙지 않게 함
  assert.deepEqual(fitViewBox(frames), { y: -VIEW_PADDING, width: 4 * CELL_WIDTH, height: 3 * CELL_HEIGHT + 2 * VIEW_PADDING });
  // 원소가 없는 장면(빈 연결 리스트 등)도 칸 하나 크기로 그려 0 크기 SVG를 막음
  assert.deepEqual(fitViewBox([{ boxes: [], arrows: [], caption: '비어 있음' }]),
    { y: -VIEW_PADDING, width: CELL_WIDTH, height: CELL_HEIGHT + 2 * VIEW_PADDING });
});

// 검증 목적: 12×8 격자에서 어느 프레임도 쓰지 않는 행 때문에 생기는 큰 빈 공간을 없애는지 확인
// - 호출 스택은 스택을 아래(y 5~7)에, 반환값을 위(y 0~1)에 두어 최소~최대 y로 자르면 줄지 않음 → 빈 행 묶음을 한 행으로 접음
// - 행 순서와 같은 행의 상자 배치는 그대로 두고, 전체 프레임 기준이라 단계마다 위치가 출렁이지 않음
test('collapses rows no frame uses into one spacer row', () => {
  const frames = [
    { boxes: [box('top', 0, 7), box('f3', 1, 7)], arrows: [], caption: '호출' },
    { boxes: [box('f3', 1, 7), box('f2', 1, 6), box('f1', 1, 5)], arrows: [], caption: '쌓임' },
    { boxes: [box('ret', 6, 0), box('label', 6, 1), box('f2', 1, 6)], arrows: [], caption: '반환' },
  ];
  const shown = compactRows(frames);
  const rows = shown.map((frame) => frame.boxes.map((b) => [b.id, b.y]));
  // 쓰는 행 0·1·5·6·7 → 빈 행 2~4는 한 행으로 접혀 0·1·(빈 행)·3·4·5
  assert.deepEqual(rows, [[['top', 5], ['f3', 5]], [['f3', 5], ['f2', 4], ['f1', 3]], [['ret', 0], ['label', 1], ['f2', 4]]]);
  assert.equal(fitViewBox(shown).height, 6 * CELL_HEIGHT + 2 * VIEW_PADDING);
  // 원래 프레임은 바꾸지 않음(트래킹·캡션은 원본 좌표와 무관하지만 엔진 데이터는 그대로 둠)
  assert.equal(frames[0].boxes[0].y, 7);
  // 위쪽만 빈 장면은 빈 행을 모두 없애 첫 행부터 그림; 한 행짜리 빈 틈(트리 층 사이 간격 등)은 그대로 둠
  const lower = compactRows([{ boxes: [box('a', 0, 4), box('b', 0, 6)], arrows: [], caption: '' }]);
  assert.deepEqual(lower[0].boxes.map((b) => b.y), [0, 2]);
  assert.deepEqual(compactRows([{ boxes: [], arrows: [], caption: '' }])[0].boxes, []);
});

// 검증 목적: 이전·다음·키보드 이동이 첫 단계 앞이나 마지막 단계 뒤로 넘어가지 않는지 확인
test('clamps the step into the frame range', () => {
  assert.equal(clampStep(-1, 5), 0);
  assert.equal(clampStep(9, 5), 4);
  assert.equal(clampStep(2, 5), 2);
});

// 검증 목적: animation 필드가 없던 시절에 저장된 보충 화면도 텍스트만으로 그대로 보이는지 확인(Review Focus 2)
test('splits a remediation view into text and an optional animation', () => {
  const view = { kind: 'remediation', concept: { id: 'stack', title: '스택' }, attempt: 1, text: '설명', is_target: true };
  assert.deepEqual(remediationParts(view), { text: '설명', animation: null });
  const animation = { kind: 'stack', title: '스택 push', frames: [{ boxes: [], arrows: [], caption: '빈 스택' }] };
  assert.deepEqual(remediationParts({ ...view, animation }), { text: '설명', animation });
});

// 검증 목적: 상자 폭을 넘는 라벨만 글자 폭을 줄이고, 테두리 없는 이름표(head·front·rear)는 줄이지 않는지 확인
// - 이름표까지 줄이면 칸 폭에 충분히 들어가는데도 작게 보임
test('squeezes only labels wider than their box', () => {
  const at = (shape, label) => ({ id: label, label, x: 0, y: 0, shape, tone: 'normal' });
  assert.equal(squeezesLabel(at('label', 'front')), false);
  assert.equal(squeezesLabel(at('cell', '999')), false);
  assert.equal(squeezesLabel(at('cell', '1000')), true);
  // node는 cell보다 좁아 세 자리부터 줄임
  assert.equal(squeezesLabel(at('node', '99')), false);
  assert.equal(squeezesLabel(at('node', '123')), true);
});

const motion = await import('../src/animation.ts');
/** @brief 좌표·tone 변화만 있는 프레임 구성 */
const frameOf = (boxes) => ({ boxes, arrows: [], caption: '계산된 상태' });

/** @brief 같은 원소의 이동이 DOM 재생성으로 이어지는 회귀 방지 */
test('keeps visual identity when the same value moves and changes tone', () => {
  const first = box('a', 0, 0);
  const frames = [frameOf([first]), frameOf([{ ...first, x: 3, y: 2, tone: 'active' }])];
  const tracks = motion.trackBoxes(frames);
  assert.equal(tracks[1][0].key, tracks[0][0].key);
  assert.deepEqual(tracks[1][0].box, frames[1].boxes[0]);
});

/** @brief BFS 큐 자리 id가 재사용돼도 값 C를 B로 바꾸지 않고 이동으로 연결 */
test('tracks unique queue values across reused slot identifiers', () => {
  const [before, after] = motion.trackBoxes([
    frameOf([{ ...box('q0', 1, 7), label: 'B' }, { ...box('q1', 2, 7), label: 'C' }]),
    frameOf([{ ...box('q0', 1, 7), label: 'C' }, { ...box('q1', 2, 7), label: 'D' }]),
  ]);
  assert.equal(after[0].key, before[1].key);
  assert.notEqual(after[0].key, before[0].key);
  assert.ok(!before.some(item => item.key === after[1].key));
});

/** @brief 중복 값은 고유 id로 구별하되 id까지 바뀌면 이동을 지어내지 않음 */
test('distinguishes duplicates by stable ids and replaces ambiguous duplicates', () => {
  const same = (id, x) => ({ ...box(id, x, 0), label: '2' });
  const [first, swapped, unknown] = motion.trackBoxes([
    frameOf([same('a', 0), same('b', 1)]),
    frameOf([same('b', 0), same('a', 1)]),
    frameOf([same('c', 0), same('d', 1)]),
  ]);
  assert.equal(swapped[0].key, first[1].key);
  assert.equal(swapped[1].key, first[0].key);
  assert.ok(unknown.every(item => !swapped.some(previous => previous.key === item.key)));
});

/** @brief 사본·다른 모양·사라진 뒤 재사용한 값은 기존 원소와 잘못 합치지 않음 */
test('reserves exact matches before fallback and uses fresh keys after absence', () => {
  const first = { ...box('a', 0, 0), label: 'A' };
  const tracks = motion.trackBoxes([
    frameOf([first]), frameOf([{ ...first, id: 'copy', x: 1 }, first]),
    frameOf([{ ...first, shape: 'node' }]), frameOf([]), frameOf([first]),
  ]);
  assert.equal(tracks[1][1].key, tracks[0][0].key);
  assert.notEqual(tracks[1][0].key, tracks[0][0].key);
  assert.notEqual(tracks[2][0].key, tracks[0][0].key);
  assert.notEqual(tracks[4][0].key, tracks[0][0].key);
  assert.equal(new Set(tracks[1].map(item => item.key)).size, 2);
});

/** @brief 퇴장을 유지하다가 빠른 역방향 이동으로 복귀하면 key 중복·잘못된 삭제 방지 */
test('keeps exits for fading and revives them on a rapid reverse transition', () => {
  const [first, second] = motion.trackBoxes([frameOf([box('a', 0, 0)]), frameOf([box('b', 1, 0)])]);
  const forward = motion.transitionBoxes(first, second);
  assert.deepEqual(forward.map(item => [item.box.id, item.phase]), [['b', 'enter'], ['a', 'exit']]);
  const reverse = motion.transitionBoxes(forward, first);
  assert.deepEqual(reverse.map(item => [item.box.id, item.phase]), [['a', 'present'], ['b', 'exit']]);
  assert.equal(new Set(reverse.map(item => item.key)).size, reverse.length);
});

/** @brief 표시용 계산은 AI core의 계산 결과를 바꾸지 않음 */
test('does not mutate frame data while computing visual transitions', () => {
  const original = Object.freeze(box('a', 0, 0));
  const frames = Object.freeze([Object.freeze(frameOf(Object.freeze([original])))]);
  const tracked = motion.trackBoxes(frames);
  const transition = motion.transitionBoxes([], tracked[0]);
  assert.equal(transition[0].box, original);
  assert.equal(transition[0].phase, 'enter');
  assert.deepEqual(motion.trackBoxes([]), []);
});
