## @file
# @brief 배열·연결 리스트·스택·큐 애니메이션이 파이썬 참조 동작과 같은 상태를 그리는지, 실행할 수 없는 연산과 분량 초과를 거부하는지 검증
import json
import unittest

from pydantic import ValidationError
from serving.animation import create_animation, simulate
from serving.animation.model import AnimationRequestModel


## @brief dict 요청을 스키마로 검증한 뒤 시뮬레이터를 실행
def run(data: dict):
    return simulate(AnimationRequestModel.model_validate(data).root)


## @brief 프레임에서 지정 모양 상자의 라벨을 x(왼→오), y(위→아래) 순서로 꺼냄
def labels(frame, shape='cell'):
    return [b.label for b in sorted((b for b in frame.boxes if b.shape == shape), key=lambda b: (b.x, b.y))]


class StackTests(unittest.TestCase):
    # 검증 목적: push·pop 결과가 파이썬 리스트와 같고, pop한 상자가 그 단계에서 removed로 표시되는지 확인
    def test_stack_frames_match_list_reference(self):
        ops = [{'op': 'push', 'value': 1}, {'op': 'push', 'value': 2}, {'op': 'pop'}, {'op': 'push', 'value': 3}]
        animation = run({'kind': 'stack', 'ops': ops})
        self.assertEqual(len(animation.frames), len(ops) + 1)
        cells = sorted((b for b in animation.frames[-1].boxes if b.shape == 'cell'), key=lambda b: -b.y)
        self.assertEqual([b.label for b in cells], ['1', '3'])
        popped = [b for b in animation.frames[3].boxes if b.tone == 'removed']
        self.assertEqual([b.label for b in popped], ['2'])
        self.assertIn('push 1', animation.frames[1].caption)

    # 검증 목적: 빈 스택 pop처럼 실행할 수 없는 연산은 그림을 지어내지 않고 한국어 오류로 거부되는지 확인
    def test_stack_pop_empty_rejected(self):
        with self.assertRaises(ValueError) as caught:
            run({'kind': 'stack', 'ops': [{'op': 'pop'}]})
        self.assertIn('비어', str(caught.exception))


class ArrayTests(unittest.TestCase):
    # 검증 목적: 삽입 시 뒤 원소가 밀리고, 같은 원소는 프레임이 바뀌어도 같은 Box.id를 유지해 이동 애니메이션이 가능한지 확인
    def test_array_insert_shifts_and_keeps_ids(self):
        animation = run({'kind': 'array', 'values': [4, 7, 9], 'ops': [{'op': 'insert', 'index': 1, 'value': 5}]})
        first, last = animation.frames[0], animation.frames[-1]
        self.assertEqual(labels(last), ['4', '5', '7', '9'])
        ids = lambda frame: {b.label: b.id for b in frame.boxes if b.shape == 'cell'}
        self.assertEqual(ids(first)['7'], ids(last)['7'])
        self.assertEqual(ids(first)['9'], ids(last)['9'])

    # 검증 목적: 길이를 벗어난 인덱스 삭제를 거부하는지 확인
    def test_array_index_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            run({'kind': 'array', 'values': [4, 7, 9], 'ops': [{'op': 'delete', 'index': 3}]})


class LinkedListTests(unittest.TestCase):
    # 검증 목적: 중간 노드 삭제 후 앞 노드가 다음 노드를 직접 가리키고, 삭제된 노드로 가는 화살표가 남지 않는지 확인
    def test_linked_list_delete_rewires_arrow(self):
        animation = run({'kind': 'linked_list', 'values': [1, 2, 3], 'ops': [{'op': 'delete', 'value': 2}]})
        last = animation.frames[-1]
        by_id = {b.id: b.label for b in last.boxes}
        edges = {(by_id[a.source], by_id[a.target]) for a in last.arrows}
        self.assertIn(('1', '3'), edges)
        self.assertFalse([e for e in edges if e[1] == '2'])


class QueueTests(unittest.TestCase):
    # 검증 목적: 원형 큐에서 rear가 배열 끝을 지나 0번 칸으로 돌아가고, front·rear 표시가 올바른 칸을 가리키는지 확인
    def test_circular_queue_wraps(self):
        ops = [{'op': 'enqueue', 'value': v} for v in (1, 2, 3)] + [{'op': 'dequeue'}, {'op': 'enqueue', 'value': 4}]
        animation = run({'kind': 'queue', 'capacity': 3, 'circular': True, 'ops': ops})
        last = animation.frames[-1]
        slots = {b.id: b for b in last.boxes if b.shape == 'slot'}
        self.assertEqual(slots['slot0'].label, '4')
        pointer = {a.source: a.target for a in last.arrows}
        self.assertEqual(pointer['front'], 'slot1')
        self.assertEqual(pointer['rear'], 'slot0')

    # 검증 목적: "끝을 지나 돌아온다"는 캡션이 rear가 실제로 0번 칸으로 넘어가는 enqueue에만 붙고,
    # 이미 한 바퀴 돈 뒤의 enqueue에는 다시 붙지 않는지 확인(검수 재현 사례)
    def test_circular_queue_caption_wraps_once(self):
        ops = ([{'op': 'enqueue', 'value': v} for v in (1, 2, 3)] + [{'op': 'dequeue'}] * 2
               + [{'op': 'enqueue', 'value': v} for v in (4, 5)])
        captions = [f.caption for f in run({'kind': 'queue', 'capacity': 3, 'circular': True, 'ops': ops}).frames]
        self.assertIn('0번 칸으로 돌아와', captions[-2])
        self.assertNotIn('돌아와', captions[-1])
        self.assertIn('[1]', captions[-1])

    # 검증 목적: 용량을 넘는 enqueue를 원형·선형 큐 모두 거부하는지 확인
    def test_queue_full_rejected(self):
        for circular in (True, False):
            with self.subTest(circular=circular), self.assertRaises(ValueError):
                run({'kind': 'queue', 'capacity': 2, 'circular': circular,
                     'ops': [{'op': 'enqueue', 'value': v} for v in (1, 2, 3)]})


class LimitTests(unittest.TestCase):
    # 검증 목적: 교육적 분량 상한을 넘는 연산 목록은 시뮬레이션 전에 스키마 검증에서 거부되는지 확인
    def test_frame_limit(self):
        with self.assertRaises(ValidationError):
            AnimationRequestModel.model_validate({'kind': 'stack', 'ops': [{'op': 'push', 'value': 1}] * 11})

    # 검증 목적: 도구 인자(JSON 문자열)로 artifact 결과를 만들고, 기존 도식과 같은 artifact_id 형식을 쓰는지 확인
    def test_create_animation_returns_artifact(self):
        result = create_animation(json.dumps({'kind': 'stack', 'ops': [{'op': 'push', 'value': 5}]}))
        self.assertTrue(result['ok'])
        self.assertEqual(result['kind'], 'animation')
        self.assertRegex(result['artifact_id'], r'^[0-9a-f]{32}$')
        self.assertEqual(result['animation']['kind'], 'stack')
        self.assertEqual(result['request']['kind'], 'stack')
        self.assertEqual(len(result['animation']['frames']), 2)
        json.dumps(result)


if __name__ == '__main__':
    unittest.main()
