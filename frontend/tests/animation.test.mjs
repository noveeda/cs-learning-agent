// 파일 역할: 애니메이션 재생기의 순수 계산(고정 화면 크기·단계 범위)과 보충 화면 분기를 UI 없이 검증
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CELL_HEIGHT, CELL_WIDTH, clampStep, fitViewBox, remediationParts } from '../src/animation.ts';

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
