## @file
# @brief 재귀 호출·반환 프레임의 계산 정확성, 교육적 분량 제한, 채팅·막힘 보충 등록 일관성 검증
import json
import re
import unittest

from pydantic import ValidationError

from serving.agent import SYSTEM, model_view
from serving.animation import ANIMATION_TOOL, CONCEPT_SCENES, SCENE_FORMATS, SIMULATORS, create_animation, simulate
from serving.animation.model import MAX_FRAMES, AnimationRequestModel
from serving.concept_map import load_concept_map


## @brief 공개 요청 검증 경로로 재귀 장면 실행; 내부 함수 대신 실제 도구 입력 계약 검증
def run(function='factorial', n=4):
    return simulate(AnimationRequestModel.model_validate({'kind': 'call_stack', 'function': function, 'n': n}).root)


## @brief 반환되어 빠진 상자를 제외한 호출 칸을 스택 바닥부터 읽음
def stack(frame):
    return sorted((b for b in frame.boxes if b.id.startswith('call') and b.shape == 'cell' and b.tone != 'removed'),
                  key=lambda b: -b.y)


## @brief 코드가 계산한 반환값 상자를 읽음; 호출 단계에는 반환값 없음
def returned(frame):
    return next((int(b.label) for b in frame.boxes if b.id == 'result'), None)


## @brief 마지막 호출부터 반환되는 순서와 호출별 상자 정체성이 유지되는지 검증
class CallStackTests(unittest.TestCase):
    ## @brief factorial(4)의 호출 깊이·LIFO 반환·곱셈 결과가 수작업 계산과 같은지 확인
    def test_factorial_calls_then_unwinds_with_computed_values(self):
        animation = run()
        self.assertEqual(animation.kind, 'call_stack')
        self.assertEqual([len(stack(f)) for f in animation.frames], [0, 1, 2, 3, 4, 3, 2, 1, 0])
        calls = [f for f in animation.frames if '호출 →' in f.caption]
        self.assertEqual([int(re.search(r'factorial\((\d)\)', f.caption)[1]) for f in calls], [4, 3, 2, 1])
        returns = [f for f in animation.frames if returned(f) is not None]
        self.assertEqual([returned(f) for f in returns], [1, 2, 6, 24])
        self.assertIn('factorial(1)=1 반환', returns[0].caption)
        self.assertIn('4 × 6 = 24', returns[-1].caption)
        initial_ids = [b.id for b in stack(calls[-1])]
        self.assertEqual([b.id for b in stack(returns[0])], initial_ids[:-1])
        self.assertEqual([b.id for b in returns[0].boxes if b.tone == 'removed'], [initial_ids[-1]])
        self.assertEqual(stack(returns[-1]), [])
        self.assertIn('스택 0칸', returns[-1].caption)

    ## @brief fib(3)의 왼쪽·오른쪽 재귀 분기를 생략하지 않고 반복 호출에 고유 id를 주는지 확인
    def test_fibonacci_traces_both_branches_without_reusing_call_ids(self):
        animation = run('fib', 3)
        calls = [f for f in animation.frames if '호출 →' in f.caption]
        self.assertEqual([int(re.search(r'fib\((\d)\)', f.caption)[1]) for f in calls], [3, 2, 1, 0, 1])
        self.assertEqual([returned(f) for f in animation.frames if returned(f) is not None], [1, 0, 1, 1, 2])
        ids = [stack(f)[-1].id for f in calls]
        self.assertEqual(len(set(ids)), 5)
        self.assertEqual(max(len(stack(f)) for f in animation.frames), 3)
        self.assertIn('1 + 1 = 2', animation.frames[-1].caption)
        self.assertEqual(stack(animation.frames[-1]), [])

    ## @brief 0·1 기저 사례에서 더 호출하지 않고 올바른 값을 반환하는지 확인
    def test_base_cases_return_without_recursive_calls(self):
        for function, n, value in [('factorial', 0, 1), ('factorial', 1, 1), ('fib', 0, 0), ('fib', 1, 1)]:
            with self.subTest(function=function, n=n):
                animation = run(function, n)
                self.assertEqual([len(stack(f)) for f in animation.frames], [0, 1, 0])
                self.assertEqual(returned(animation.frames[-1]), value)

    ## @brief 허용 입력 전체가 16단계·12×8 격자·Box 라벨 계약을 지키며 반환하는지 확인
    def test_supported_inputs_fit_existing_player_limits(self):
        for function, expected in [('factorial', [1, 1, 2, 6, 24, 120, 720]), ('fib', [0, 1, 1, 2])]:
            for n, value in enumerate(expected):
                with self.subTest(function=function, n=n):
                    animation = run(function, n)
                    self.assertLessEqual(len(animation.frames), MAX_FRAMES)
                    self.assertEqual(returned(animation.frames[-1]), value)
                    for frame in animation.frames:
                        self.assertEqual(len({(b.x, b.y) for b in frame.boxes}), len(frame.boxes))
                        self.assertTrue(all(0 <= b.x < 12 and 0 <= b.y < 8 and len(b.label) <= 16 for b in frame.boxes))
                        live = stack(frame)
                        arrows = {a.source: a.target for a in frame.arrows}
                        self.assertEqual(arrows.get('top'), live[-1].id if live else None)

    ## @brief 과도한 재귀·잘못된 타입·임의 함수 이름을 계산 전에 거부하는지 확인
    def test_invalid_or_too_large_requests_are_rejected(self):
        for change in [{'n': -1}, {'n': 7}, {'n': True}, {'n': 2.5}, {'n': '3'},
                       {'function': 'fib', 'n': 4}, {'function': 'eval'}, {'ops': []}]:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                AnimationRequestModel.model_validate({'kind': 'call_stack', 'function': 'factorial', 'n': 4, **change})

    ## @brief 실제 도구가 반환한 장면과 모델용 단계 요약에 같은 계산 결과가 들어가는지 확인
    def test_artifact_and_model_steps_share_computed_trace(self):
        result = create_animation(json.dumps({'kind': 'call_stack', 'function': 'factorial', 'n': 4}))
        self.assertTrue(result['ok'])
        self.assertEqual(result['request'], {'kind': 'call_stack', 'function': 'factorial', 'n': 4})
        self.assertEqual(result['animation']['kind'], 'call_stack')
        view = model_view(result)
        self.assertEqual(view['steps'], [f['caption'] for f in result['animation']['frames']])
        self.assertIn('4 × 6 = 24', view['steps'][-1])
        self.assertNotIn('animation', view)


## @brief 재귀 장면이 채팅과 막힘 보충 양쪽 경로에 노출되는지 검증
class AnimationGuideTests(unittest.TestCase):
    ## @brief 시뮬레이터·요청·도구 enum·예시가 일치하고 개념 ID가 실제 지도에 존재하는지 확인
    def test_registry_schema_and_concept_map_stay_consistent(self):
        self.assertIn('call_stack', SIMULATORS)
        self.assertEqual(CONCEPT_SCENES.get('recursion'), ('call_stack',))
        self.assertEqual(set(SCENE_FORMATS), set(SIMULATORS))
        properties = ANIMATION_TOOL['function']['parameters']['properties']
        self.assertEqual(set(properties['kind']['enum']), set(SIMULATORS))
        kinds = {d['properties']['kind']['const'] for d in AnimationRequestModel.model_json_schema()['$defs'].values()
                 if 'kind' in d.get('properties', {})}
        self.assertEqual(kinds, set(SIMULATORS))
        self.assertLessEqual(set(CONCEPT_SCENES), {c.id for c in load_concept_map().concepts})
        self.assertLessEqual({kind for kinds in CONCEPT_SCENES.values() for kind in kinds}, set(SIMULATORS))
        self.assertEqual(simulate(AnimationRequestModel.model_validate(SCENE_FORMATS['call_stack']).root).kind, 'call_stack')
        self.assertEqual(properties['function']['enum'], ['factorial', 'fib'])
        self.assertIn('n', properties)

    ## @brief 개념 설명의 자동 호출·장면 없는 사실 질문의 제외·계산 단계 설명을 모델에 안내하는지 확인
    def test_system_guides_animation_without_explicit_request(self):
        self.assertIn('명시적으로 요청하지 않아도', SYSTEM)
        self.assertIn('show_animation', SYSTEM)
        self.assertIn('한 번', SYSTEM)
        self.assertIn('단순 사실 질문', SYSTEM)
        self.assertIn('steps', SYSTEM)
        for concept, kinds in CONCEPT_SCENES.items():
            self.assertIn(concept, SYSTEM)
            for kind in kinds:
                self.assertIn(kind, SYSTEM)
        self.assertIn('recursion', SYSTEM)
        self.assertIn('call_stack', SYSTEM)


if __name__ == '__main__':
    unittest.main()
