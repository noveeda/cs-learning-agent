// 파일 역할: 애니메이션 재생기의 순수 계산(고정 화면 크기·단계 범위)과 보충 화면 분기를 UI 없이 검증
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CELL_HEIGHT, CELL_WIDTH, clampStep, fitViewBox, remediationParts, squeezesLabel } from '../src/animation.ts';

const box = (id, x, y) => ({ id, label: id, x, y, shape: 'cell', tone: 'normal' });

// 검증 목적: 단계마다 크기가 달라져 화면이 출렁이지 않도록, 모든 프레임 좌표의 합집합으로 크기를 고정하는지 확인
test('fits one view box to the largest coordinates across frames', () => {
  const frames = [
    { boxes: [box('a', 0, 0), box('b', 3, 0)], arrows: [], caption: '처음' },
    { boxes: [box('a', 1, 2)], arrows: [], caption: '이동' },
  ];
  assert.deepEqual(fitViewBox(frames), { width: 4 * CELL_WIDTH, height: 3 * CELL_HEIGHT });
  // 원소가 없는 장면(빈 연결 리스트 등)도 칸 하나 크기로 그려 0 크기 SVG를 막음
  assert.deepEqual(fitViewBox([{ boxes: [], arrows: [], caption: '비어 있음' }]), { width: CELL_WIDTH, height: CELL_HEIGHT });
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
