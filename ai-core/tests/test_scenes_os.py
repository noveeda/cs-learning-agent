## @file
# @brief 운영체제 개념별 예시 장면(content/os/scenes.json)이 모두 실행되고, 개념마다 서로 다른 장면 3개를 짧은 글로 담는지 검증
# - 막힘 보충이 시도 n번째에 n번째 장면을 보여 주므로, 한 개념의 세 장면이 같은 그림이면 다시 설명해도 새로 볼 것이 없음
# - 6단계 이상 개념은 화면에서 숨기므로(프런트 MAX_VISIBLE_LEVEL=5) 과목 안 5단계 이하 개념만 장면을 가짐
import json
import unittest
from itertools import combinations
from pathlib import Path

from serving.animation import simulate
from serving.animation.model import AnimationRequestModel
from serving.animation.scheduling import schedule

CONTENT = Path(__file__).resolve().parents[1] / 'content' / 'os'
# 장면 하나의 권장 분량(이전·다음 버튼으로 따라가는 단계 수)과 상자 라벨 길이: 도형 위주로 글을 최소화하기 위한 상한
MAX_SCENE_FRAMES = 12
MAX_LABEL = 8
# 스케줄링 알고리즘 개념은 같은 알고리즘의 장면만 보여 줌(다른 알고리즘 그림이 섞이면 개념을 잘못 가르침)
ALGORITHM = {'fcfs': 'fcfs', 'sjf': 'sjf', 'round-robin': 'rr', 'priority-scheduling': 'priority'}


## @brief 과목 안 선수 관계 단계(선수 없으면 1)가 5 이하인 개념 ID를 지도 순서대로 반환
def visible_concepts() -> list[str]:
    concepts = json.loads((CONTENT / 'concept-map.json').read_text())['concepts']
    by_id = {c['id']: c for c in concepts}
    levels = {}

    def level(concept_id):
        if concept_id not in levels:
            inside = [p['id'] for p in by_id[concept_id]['prerequisites'] if p['id'] in by_id]
            levels[concept_id] = max(map(level, inside), default=0) + 1
        return levels[concept_id]

    return [c['id'] for c in concepts if level(c['id']) <= 5]


class OsScenesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenes = json.loads((CONTENT / 'scenes.json').read_text())

    # 검증 목적: 보이는 개념 전부에 장면이 있고, 숨긴 개념·지도에 없는 개념에는 없음
    def test_covers_visible_concepts(self):
        self.assertEqual(list(self.scenes), visible_concepts())

    # 검증 목적: 개념마다 정확히 3개이고 모두 공개 검증 경로(strict 스키마 → simulate)로 실행되며 분량·글 길이 상한을 지킴
    def test_every_scene_runs_within_limits(self):
        for concept, requests in self.scenes.items():
            self.assertEqual(len(requests), 3, concept)
            for index, request in enumerate(requests):
                with self.subTest(concept=concept, index=index):
                    animation = simulate(AnimationRequestModel.model_validate(request).root)
                    self.assertLessEqual(len(animation.frames), MAX_SCENE_FRAMES)
                    labels = [box.label for frame in animation.frames for box in frame.boxes]
                    self.assertLessEqual(max(map(len, labels)), MAX_LABEL)
                    if concept in ALGORITHM and 'algorithm' in request:
                        self.assertEqual(request['algorithm'], ALGORITHM[concept])

    # 검증 목적: 한 개념의 세 장면은 입력도 그림(프레임 수열)도, 단계별 설명(캡션 흐름)도 서로 달라야 함
    # - 같은 장면 종류에 숫자만 바꾼 장면은 프레임은 달라도 같은 설명 흐름이 되기 쉬워 캡션 수열까지 비교
    def test_three_scenes_differ(self):
        for concept, requests in self.scenes.items():
            animations = [simulate(AnimationRequestModel.model_validate(r).root) for r in requests]
            for (i, a), (j, b) in combinations(enumerate(animations), 2):
                with self.subTest(concept=concept, pair=(i, j)):
                    self.assertNotEqual(requests[i], requests[j])
                    self.assertNotEqual([f.model_dump() for f in a.frames], [f.model_dump() for f in b.frames])
                    self.assertNotEqual([f.caption for f in a.frames], [f.caption for f in b.frames])

    # 검증 목적: RR 장면에 "할당량이 끝난 시각 = 새 프로세스 도착 시각"인 동점이 없어야 함
    # - 이 동점에서 큐 순서는 교재마다 달라(새 도착 먼저 / 선점된 프로세스 먼저) 장면이 한쪽 규칙만 가르치게 됨
    def test_rr_scenes_avoid_quantum_arrival_ties(self):
        for concept, requests in self.scenes.items():
            for index, request in enumerate(requests):
                if request.get('algorithm') != 'rr' or request['kind'] == 'process_state':
                    continue
                validated = AnimationRequestModel.model_validate(request).root
                arrivals = {p.arrival for p in validated.processes}
                for proc, _, end, _, done in schedule(validated):
                    with self.subTest(concept=concept, index=index, time=end):
                        self.assertFalse(proc and not done and end in arrivals)

    # 검증 목적: 상태판 장면을 스케줄링 개념에 쓸 때는 그 방식의 관점(algorithm)으로 캡션을 붙여 규칙이 보이게 함
    def test_state_boards_for_algorithms_name_the_policy(self):
        for concept in ('fcfs', 'round-robin'):
            for request in self.scenes[concept]:
                if request['kind'] == 'process_state':
                    self.assertEqual(request.get('algorithm'), ALGORITHM[concept])


if __name__ == '__main__':
    unittest.main()
