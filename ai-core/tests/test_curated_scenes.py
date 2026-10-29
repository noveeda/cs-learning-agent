## @file
# @brief 미리 짠 개념별 장면(scenes.json) 로더와, 막힘 보충·하네스 채팅이 모델 장면 선택 대신 그 장면을 쓰는 연결을 검증
# - 로더: 깨진 장면·파일은 버리고 경고, 파일이 없으면 빈 표, 실제 데이터는 개념마다 3개 모두 실행됨
# - 막힘 보충: 시도 차례대로 세션 seed 순서의 장면을 쓰고 순환, 직전 장면 회피, 장면이 없으면 모델 선택, 옛 세션 복원
# - 채팅: 질문 문자열로 결정적으로 고르고 모델 장면 선택을 부르지 않음
# - 픽스처는 임시 scenes.json(stack 개념)이라 실제 데이터 내용이 바뀌어도 흐름 검증은 그대로 유지됨
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from serving.animation import simulate
from serving.animation.curated import load_curated_scenes
from serving.concept_map import load_concept_map
from serving.harness_chat import _animation_event
from serving.stuck import Entry, StuckState, _curated_scene, _ordered
from test_stuck_flow import FakeGateway, STACK_SCENE, fail_and_retry, remediation_session

# 서로 다른 스택 장면 3개(픽스처); 첫 장면은 가짜 모델이 고르는 STACK_SCENE과 같게 둬 직전 장면 회피를 확인
SCENES = [STACK_SCENE,
          {'kind': 'stack', 'ops': [{'op': 'push', 'value': 5}, {'op': 'pop'}]},
          {'kind': 'stack', 'ops': [{'op': 'push', 'value': 7}, {'op': 'push', 'value': 8}, {'op': 'push', 'value': 9}]}]
# 스키마는 맞지만 실행할 수 없는 장면(빈 스택 pop)과 스키마 위반 장면; 로더가 버려야 함
BROKEN = [{'kind': 'stack', 'ops': [{'op': 'pop'}]}, {'kind': 'stack', 'ops': 'push'}]


## @brief 임시 content 폴더에 과목별 scenes.json을 써서 load_curated_scenes 결과를 반환
# - files: {과목 폴더: 파일 내용(dict면 JSON으로, str이면 그대로)}
def load_fixture(test, files):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    for subject, content in files.items():
        folder = Path(tmp.name) / subject
        folder.mkdir()
        (folder / 'scenes.json').write_text(content if isinstance(content, str) else json.dumps(content))
    return load_curated_scenes(Path(tmp.name))


## @brief 픽스처의 stack 장면 3개를 막힘 엔진이 쓰는 미리 짠 장면 표로 끼움
def curated_stack(test):
    table = load_fixture(test, {'dsa': {'stack': SCENES}})
    patcher = patch('serving.stuck.CURATED_SCENES', table)
    patcher.start()
    test.addCleanup(patcher.stop)
    return table['stack']


def state(seed, scenes=None):
    return StuckState(entry=Entry(kind='roadmap', concept='stack', return_to='/learn'), stage='remediate', seed=seed,
                      scenes=scenes or {})


class LoaderTests(unittest.TestCase):
    # 검증 목적: 실행할 수 없는 장면·스키마 위반 장면은 버리고 경고를 남기되, 같은 개념의 나머지 장면은 순서대로 남는지 확인
    def test_drops_broken_scenes_with_warning(self):
        with self.assertLogs(level='WARNING') as logs:
            table = load_fixture(self, {'dsa': {'stack': [BROKEN[0], *SCENES, BROKEN[1]]}})
        self.assertEqual([r.model_dump() for r in table['stack']], SCENES)
        self.assertEqual(len([line for line in logs.output if 'dropped' in line]), 2)

    # 검증 목적: 장면이 모두 깨진 개념·읽을 수 없는 파일·scenes.json이 없는 과목은 표에 넣지 않아 모델 선택으로 돌아가는지 확인
    def test_missing_or_unusable_scenes_give_no_entry(self):
        with self.assertLogs(level='WARNING'):
            table = load_fixture(self, {'dsa': {'stack': BROKEN, 'queue': []}, 'os': '{깨진 JSON'})
        self.assertEqual(table, {})
        self.assertEqual(load_fixture(self, {}), {})

    # 검증 목적: 실제 과목 데이터 전체(43개념)가 개념마다 3개씩 모두 검증·실행되는지 확인(하나라도 버려지면 실패)
    def test_repository_scenes_all_load(self):
        table = load_curated_scenes()
        self.assertEqual(len(table), 43)
        self.assertEqual({len(requests) for requests in table.values()}, {3})
        self.assertLessEqual(set(table), {c.id for c in load_concept_map().concepts})
        for requests in table.values():
            for request in requests:
                simulate(request)


class ChoiceTests(unittest.TestCase):
    # 검증 목적: 시도 1·2·3은 세션 seed로 섞은 순서의 장면을 차례로 쓰고(세 장면 모두 한 번씩), 시도 4는 처음으로 순환하는지 확인
    def test_attempts_follow_seeded_order_and_cycle(self):
        scenes = curated_stack(self)
        for seed in range(5):
            with self.subTest(seed=seed):
                s = state(seed)
                order = _ordered(s, list(scenes), 'stack', 'scene')
                picks = [_curated_scene(s, 'stack', scenes, attempt) for attempt in (1, 2, 3, 4)]
                self.assertEqual(picks, [*order, order[0]])
                self.assertEqual(len({json.dumps(p.model_dump()) for p in picks[:3]}), 3)

    # 검증 목적: 세션 seed가 다르면 첫 장면이 달라질 수 있는지 확인(세션마다 같은 장면부터 나오지 않게)
    def test_seed_changes_first_scene(self):
        scenes = curated_stack(self)
        firsts = {json.dumps(_curated_scene(state(seed), 'stack', scenes, 1).model_dump()) for seed in range(20)}
        self.assertGreater(len(firsts), 1)

    # 검증 목적: 차례 장면이 직전에 보여 준 장면과 같으면 다음 장면으로 넘기는지 확인
    def test_skips_previous_scene(self):
        scenes = curated_stack(self)
        s = state(None, scenes={'stack': STACK_SCENE})
        self.assertEqual(_curated_scene(s, 'stack', scenes, 1).model_dump(), SCENES[1])

    # 검증 목적: 장면이 하나만 남은 개념은 직전 장면과 같아도 그대로 보여 주는지 확인(장면 없음보다 반복이 나음)
    def test_single_scene_repeats(self):
        only = curated_stack(self)[:1]
        s = state(1, scenes={'stack': STACK_SCENE})
        self.assertEqual(_curated_scene(s, 'stack', only, 2).model_dump(), STACK_SCENE)


class StuckFlowTests(unittest.TestCase):
    # 검증 목적: 미리 짠 장면이 있는 개념은 모델 장면 선택을 부르지 않고, 첫 차례 장면과 코드 캡션을 설명에 넘기는지 확인
    def test_curated_scene_skips_model_choice(self):
        scenes = curated_stack(self)
        gateway = FakeGateway()
        session = remediation_session(self, 'stack', gateway)
        self.assertNotIn('animate', gateway.calls)
        first = _curated_scene(state(session.state.seed), 'stack', scenes, 1).model_dump()
        steps = [frame['caption'] for frame in session.state.view['animation']['frames']]
        self.assertEqual(gateway.explained_scenes[-1], {**first, 'steps': steps})
        self.assertEqual(session.state.scenes['stack'], first)

    # 검증 목적: 재설명(시도 2·3)마다 직전과 다른 장면이 나오고 반복 로그가 없는지 확인
    def test_retries_show_different_scenes(self):
        curated_stack(self)
        gateway = FakeGateway()
        session = remediation_session(self, 'stack', gateway)
        shown = [session.state.scenes['stack']]
        for _ in range(2):
            fail_and_retry(session)
            self.assertEqual(session.state.view['kind'], 'remediation')
            shown.append(session.state.scenes['stack'])
        self.assertEqual(len({json.dumps(scene) for scene in shown}), 3)
        self.assertNotIn('animate', gateway.calls)
        self.assertNotIn('animation_repeated', [e.kind for e in session.state.log])

    # 검증 목적: 미리 짠 장면이 없으면(파일 없음) 지금처럼 모델 장면 선택 경로를 쓰는지 확인
    def test_without_curated_scenes_uses_model_choice(self):
        with patch('serving.stuck.CURATED_SCENES', load_fixture(self, {})):
            gateway = FakeGateway()
            remediation_session(self, 'stack', gateway)
        self.assertEqual(gateway.calls.count('animate'), 1)

    # 검증 목적: seed·scenes 필드가 없는 옛 저장 세션도 복원되고, 파일 순서 첫 장면으로 보충하는지 확인
    def test_old_state_restores_with_file_order(self):
        scenes = curated_stack(self)
        session = remediation_session(self, 'stack', FakeGateway())
        old = session.state.model_dump()
        del old['seed'], old['scenes']
        restored = StuckState.model_validate(old)
        self.assertEqual((restored.seed, restored.scenes), (None, {}))
        self.assertEqual(_curated_scene(restored, 'stack', scenes, 1).model_dump(), SCENES[0])


class ChatTests(unittest.TestCase):
    ## @brief 장면 선택 모델이 불리면 실패하는 가짜 게이트웨이
    class NoChoice:
        def choose_animation(self, *args):
            raise AssertionError('미리 짠 장면이 있으면 모델 장면 선택을 부르지 않음')

    # 검증 목적: 채팅 애니메이션이 미리 짠 장면 중 하나를 쓰고, 같은 질문은 늘 같은 장면, 질문이 다르면 여러 장면이 나오는지 확인
    def test_chat_picks_curated_scene_by_question(self):
        concept_map = load_concept_map()
        with patch('serving.harness_chat.CURATED_SCENES', load_fixture(self, {'dsa': {'stack': SCENES}})):
            def pick(question):
                event = _animation_event(question, ['stack'], self.NoChoice(), concept_map)
                return json.dumps(event['request'])

            self.assertEqual(pick('스택이 뭐예요?'), pick('스택이 뭐예요?'))
            self.assertEqual(len({pick(f'스택 질문 {n}') for n in range(20)}), 3)
            self.assertLessEqual({json.loads(pick(f'q{n}'))['ops'][0]['value'] for n in range(20)}, {1, 5, 7})


if __name__ == '__main__':
    unittest.main()
