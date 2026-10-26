# 파일 역할: 시뮬레이션 학습자(프로필·응답자·API 클라이언트·실행·M1·M2 집계)를 가짜 API와 가짜 모델로 검증
# - 가짜 API는 실제 막힘 엔진(serving.stuck.step)을 그대로 쓰고 LLM 역할만 가짜로 바꿈: 진단·보충 순서는 실제 엔진 규칙대로 확인됨
# - Review Focus 1(엔진 가정과 어긋나는 프로필)·2(진단 문항 상한 효과)를 프로필·집계 테스트로 고정
import random
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import httpx

from eval.sim_learner import client as client_module
from eval.sim_learner.client import StuckApiError, StuckClient
from eval.sim_learner.learner import Learner
from eval.sim_learner.profiles import (Profile, generate_profiles, prerequisite_depths, recall_ceiling, select_targets,
                                       true_gaps)
from eval.sim_learner.run import EVAL_ROOT, _session_metrics, main, oracle_gaps, run_profile, summarize
from serving.concept_map import load_concept_map
from serving.grading import grade
from serving.item_bank import load_item_bank
from serving.stuck import MAX_DIAGNOSIS_ITEMS, StepInput, StuckState, step

MAP = load_concept_map()
ITEMS = load_item_bank()
ALL = frozenset(c.id for c in MAP.concepts)
PREREQUISITES = {c.id: [p.id for p in c.prerequisites] for c in MAP.concepts}


# 역할: 막힘 엔진의 LLM 역할 가짜 구현; 서술 채점은 학습자 답에 '정확' 표시가 있을 때만 통과
class FakeGateway:
    def explain(self, concept, attempt, entry, scene=None):
        return f'{concept.title} 보충'

    def grade_explanation(self, item, answer):
        return [answer.startswith('정확')] * len(item.rubric)

    def summarize(self, state, concept_map):
        return '요약'

    def choose_animation(self, concept, entry, kinds):
        return None

    def identify(self, excerpt, concept_map):
        return None


# 역할: Spring 공개 API 가짜 구현; 실제 엔진으로 한 단계씩 진행하고 브라우저 공개 형태({id, version, stage, view, entry})만 돌려줌
class FakeApi:
    def __init__(self):
        self.states, self.calls = {}, 0

    def _public(self, sid):
        state, version = self.states[sid]
        return {'id': sid, 'version': version, 'stage': state.stage, 'view': state.view, 'entry': state.entry.model_dump()}

    def start(self, entry):
        self.calls += 1
        sid = f's{len(self.states)}'
        self.states[sid] = (step(None, StepInput(type='start', entry=entry), FakeGateway(), MAP, ITEMS, 0.0), 1)
        return self._public(sid)

    def step(self, session, data):
        self.calls += 1
        state, version = self.states[session['id']]
        self.states[session['id']] = (step(state, StepInput.model_validate(data), FakeGateway(), MAP, ITEMS, 0.0), version + 1)
        return self._public(session['id'])


# 역할: 학습자 모델 가짜 구현; 아는 개념(참고 답을 받음)이면 '정확'으로 시작하는 답, 모르면 불완전한 답
def fake_explainer(question, reference):
    return f'정확: {reference["rationale"]}' if reference else '잘 모르겠지만 대충 이런 것 같다'


def profile_with(target, unknown, slip=0.0, guess=0.0, consistent=True):
    return Profile(id='t', target=target, known=ALL - frozenset(unknown) - {target}, slip=slip, guess=guess, consistent=consistent)


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.targets = select_targets(MAP)
        self.profiles = generate_profiles(MAP, self.targets, per_target=5, seed=7)

    # 검증 목적: 대상 고르기가 결정적이고, 계획대로 깊이 3 이상 개념 10개를 고르는지 확인
    def test_select_targets(self):
        self.assertEqual(len(self.targets), 10)
        self.assertEqual(self.targets, select_targets(MAP))
        for target in self.targets:
            self.assertTrue(any(depth == 3 for depth in prerequisite_depths(MAP, target).values()), target)

    # 검증 목적: (Review Focus 1) 기본 프로필의 known은 선수 관계에 대해 닫혀 있음(X를 알면 X의 선수 개념도 모두 앎)
    # - 엔진은 "선수 개념을 알면 그 아래도 안다"고 보고 가지치기하므로, 이 가정을 지켜야 M1이 엔진 성능을 잼
    def test_profiles_are_prerequisite_closed(self):
        consistent = [p for p in self.profiles if p.consistent]
        self.assertEqual(len(consistent), 50)
        for profile in consistent:
            self.assertNotIn(profile.target, profile.known)
            for concept in profile.known:
                self.assertLessEqual(set(PREREQUISITES[concept]), profile.known, (profile.id, concept))
        # 대상마다 빈틈 0개 조합 하나와 서로 다른 조합들
        for target in self.targets:
            mine = [p for p in consistent if p.target == target]
            self.assertEqual(len({p.known for p in mine}), len(mine))
            self.assertEqual(sum(1 for p in mine if true_gaps(p, MAP) == {target}), 1)

    # 검증 목적: 어긋난 프로필(위는 알고 아래는 모름)은 대상마다 하나씩 따로 표시되고, 실제로 닫힘 성질을 어김
    def test_inconsistent_profiles_are_marked(self):
        inconsistent = [p for p in self.profiles if not p.consistent]
        self.assertEqual(sorted(p.target for p in inconsistent), sorted(self.targets))
        for profile in inconsistent:
            violated = [c for c in profile.known if not set(PREREQUISITES[c]) <= profile.known]
            self.assertTrue(violated, profile.id)

    # 검증 목적: 같은 seed는 같은 프로필, 다른 seed는 다른 조합(고정 프로필 파일 없이 seed로 재현)
    def test_generation_is_reproducible(self):
        again = generate_profiles(MAP, self.targets, per_target=5, seed=7)
        self.assertEqual(self.profiles, again)
        self.assertNotEqual([p.known for p in self.profiles], [p.known for p in generate_profiles(MAP, self.targets, 5, seed=8)])

    # 검증 목적: 정답 빈틈은 대상 개념과 깊이 3 안의 모르는 선수 개념만. 더 깊은 빈틈은 엔진이 찾을 수 없어 세지 않음
    def test_true_gaps_respects_depth(self):
        depths = prerequisite_depths(MAP, 'mst')
        deep = next(c for c, d in prerequisite_depths(MAP, 'mst', max_depth=None).items() if d == 4)
        near = next(c for c, d in depths.items() if d == 1)
        profile = profile_with('mst', {deep, near}, consistent=False)
        self.assertEqual(true_gaps(profile, MAP), {'mst', near})
        self.assertTrue(all(d <= 3 for d in depths.values()))

    # 검증 목적: (Review Focus 2) 빈틈이 진단 문항 수(5)를 넘으면 recall 상한이 1보다 낮음. 빈틈이 없으면 상한 1
    def test_recall_ceiling_when_gaps_exceed_items(self):
        prereqs = list(prerequisite_depths(MAP, 'mst'))
        seven = profile_with('mst', set(prereqs[:7]), consistent=False)
        self.assertEqual(len(true_gaps(seven, MAP) - {'mst'}), 7)
        self.assertAlmostEqual(recall_ceiling(seven, MAP), MAX_DIAGNOSIS_ITEMS / 7)
        self.assertEqual(recall_ceiling(profile_with('mst', set()), MAP), 1.0)
        three = profile_with('mst', set(prereqs[:3]), consistent=False)
        self.assertEqual(recall_ceiling(three, MAP), 1.0)


class LearnerTest(unittest.TestCase):
    # 검증 목적: slip=0, guess=0이면 아는 개념의 자동 채점 문항은 모두 정답, 모르는 개념은 모두 오답
    def test_answers_follow_knowledge_exactly(self):
        automatic = [i for i in ITEMS if i.format != 'explanation']
        concepts = sorted({i.concept for i in automatic})
        known = frozenset(concepts[::2])
        learner = Learner(Profile('p', 'mst', known, 0.0, 0.0, True), ITEMS, random.Random(1), fake_explainer)
        for item in automatic:
            answer, note = learner.answer({'id': item.id, 'format': item.format, 'question': item.question})
            self.assertEqual(grade(item, answer), item.concept in known, item.id)
            self.assertEqual(note, 'known' if item.concept in known else 'unknown')

    # 검증 목적: slip·guess 확률이 실제로 반영되고(아는 개념 오답·모르는 선택형 정답), 기록에 이유가 남는지 확인
    def test_slip_and_guess(self):
        mc = next(i for i in ITEMS if i.format == 'multiple_choice')
        view = {'id': mc.id, 'format': mc.format, 'question': mc.question}
        knows = Learner(Profile('p', 'mst', frozenset({mc.concept}), 1.0, 0.0, True), ITEMS, random.Random(1), fake_explainer)
        answer, note = knows.answer(view)
        self.assertEqual((grade(mc, answer), note), (False, 'slip'))
        guesses = Learner(Profile('p', 'mst', frozenset(), 0.0, 1.0, True), ITEMS, random.Random(1), fake_explainer)
        answer, note = guesses.answer(view)
        self.assertEqual((grade(mc, answer), note), (True, 'guess'))

    # 검증 목적: 서술 문항은 아는 개념이면 참고 답(근거·루브릭)을 주고 학습자 모델에 맡기며, 모르면 참고 답 없이 부름
    # - 보충을 받은 개념은 "알게 됨"으로 바뀌어 그 뒤 확인 문항부터 아는 개념처럼 답함
    def test_explanation_and_learning(self):
        item = next(i for i in ITEMS if i.format == 'explanation')
        seen = []

        def explainer(question, reference):
            seen.append(reference)
            return 'x'
        learner = Learner(Profile('p', item.concept, frozenset(), 0.0, 0.0, True), ITEMS, random.Random(1), explainer)
        view = {'id': item.id, 'format': 'explanation', 'question': item.question}
        learner.answer(view)
        learner.learn(item.concept)
        learner.answer(view)
        self.assertIsNone(seen[0])
        self.assertEqual(seen[1], {'rationale': item.rationale, 'rubric': item.rubric})


class ClientTest(unittest.TestCase):
    # 검증 목적: 409·503은 2초 간격으로 최대 3번 다시 보내고, 그래도 실패하면 상태 코드를 담은 오류로 끝남
    def test_retries_409_and_503(self):
        replies = iter([409, 503, 200])
        seen, waits = [], []

        def handler(request):
            seen.append(request.url.path)
            status = next(replies)
            body = {'id': 'abc', 'version': 2, 'stage': 'diagnose', 'view': {}, 'entry': {}} if status == 200 else {'message': 'x'}
            return httpx.Response(status, json=body)
        api = StuckClient('http://test', transport=httpx.MockTransport(handler), sleep=waits.append)
        session = api.step({'id': 'abc', 'version': 1}, {'type': 'continue'})
        self.assertEqual((session['version'], seen, waits), (2, ['/api/stuck/sessions/abc/step'] * 3, [2.0, 2.0]))
        always = StuckClient('http://test', transport=httpx.MockTransport(lambda r: httpx.Response(503, json={})), sleep=waits.append)
        with self.assertRaises(StuckApiError) as caught:
            always.start({'kind': 'roadmap', 'concept': 'stack', 'return_to': '/x'})
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(client_module.RETRIES, 3)


class RunTest(unittest.TestCase):
    # 검증 목적: 가짜 API로 진단→보충→확인→복귀를 끝까지 진행하고, 지식 상태대로 답하면 엔진이 찾을 수 있는 빈틈을 모두 찾는지 확인
    # - slip·guess 0인 학습자는 완벽한 응답자라 시스템 빈틈 = 엔진 오라클 빈틈, 기본 프로필이면 틀린 빈틈(precision 손실)도 없음
    def test_full_session_and_metrics(self):
        profiles = [replace(p, slip=0.0, guess=0.0) for p in generate_profiles(MAP, ['bst'], 5, seed=1)]
        for profile in [p for p in profiles if p.consistent]:
            with self.subTest(profile=profile.id):
                api = FakeApi()
                record = run_profile(profile, api, Learner(profile, ITEMS, random.Random(0), fake_explainer), MAP, ITEMS)
                self.assertEqual((record['stage'], record['error']), ('complete', None))
                self.assertEqual(set(record['gaps']), oracle_gaps(profile, MAP, ITEMS))
                self.assertEqual(record['recall'], record['oracle_recall'])
                self.assertEqual(record['precision'], 1.0)
                self.assertEqual(record['remediated'][-1], 'bst')
                self.assertEqual(record['over_remediated'], [])
                self.assertEqual(record['steps'], api.calls)
                self.assertTrue(all(outcome == 'passed' for outcome in record['outcomes'].values()))
        # (Review Focus 1) 위는 알고 아래는 모르는 프로필: 엔진이 위를 맞힌 뒤 가지치기해 아래 빈틈을 놓침. 이유가 기록됨
        bad = next(p for p in profiles if not p.consistent)
        record = run_profile(bad, FakeApi(), Learner(bad, ITEMS, random.Random(0), fake_explainer), MAP, ITEMS)
        self.assertLess(record['recall'], 1.0)
        self.assertEqual(record['miss_reasons'], {'not_probed_pruned': 1})

    # 검증 목적: 놓친 빈틈 이유를 "상한 탓"과 "찍기 탓"으로 나누는지 확인(2026-10-26 오프라인 재생 분석 근거)
    # - 예전 분류는 "묻지 않았고 문항 5개를 다 씀"만 보고 상한 탓으로 셌음. 찍기로 가지치기된 빈틈과 진짜 가지치기를 상한 탓과 섞지 않게 함
    # - heap의 선수 관계: heap → binary-tree·array, binary-tree → tree, array → memory-address·complexity-analysis,
    #   tree → reference·recursion, complexity-analysis → big-o
    def test_miss_reasons_split_budget_and_guess(self):
        def reasons(truth, evidence):
            record = {'target': 'heap', 'gaps': [c for c, result, _ in evidence if result == 'gap'], 'remediated': [],
                      'evidence': [{'concept': c, 'result': result} for c, result, _ in evidence],
                      'answers': [{'stage': 'diagnose', 'concept': c, 'item': f'{c}-d', 'note': note}
                                  for c, _, note in evidence]}
            return _session_metrics(record, profile_with('heap', truth), set(truth), MAP, ITEMS)['miss_reasons']

        # 모르는 array를 찍어 맞힘(문항 2개만 씀): array는 guessed, 그 아래 complexity-analysis·big-o는 찍기로 가지치기됨
        self.assertEqual(reasons({'array', 'complexity-analysis', 'big-o'},
                                 [('binary-tree', 'known', 'known'), ('array', 'known', 'guess')]),
                         {'guessed': 1, 'not_probed_guessed': 2})
        # 문항 5개를 다 썼어도 아는 binary-tree를 맞혀 그 아래 tree를 묻지 않은 것은 상한이 아니라 진짜 가지치기
        self.assertEqual(reasons({'array', 'memory-address', 'complexity-analysis', 'big-o', 'tree'},
                                 [('binary-tree', 'known', 'known'), ('array', 'gap', 'unknown'),
                                  ('memory-address', 'gap', 'unknown'), ('complexity-analysis', 'gap', 'unknown'),
                                  ('big-o', 'gap', 'unknown')]),
                         {'not_probed_pruned': 1})
        # 문항 5개를 다 쓴 뒤 대기열에 남은 reference·recursion·big-o는 상한 탓
        self.assertEqual(reasons({'binary-tree', 'array', 'tree', 'complexity-analysis', 'reference', 'recursion',
                                  'big-o'},
                                 [('binary-tree', 'gap', 'unknown'), ('array', 'gap', 'unknown'),
                                  ('tree', 'gap', 'unknown'), ('memory-address', 'known', 'known'),
                                  ('complexity-analysis', 'gap', 'unknown')]),
                         {'not_probed_budget': 3})

    # 검증 목적: 실제 엔진으로 끝까지 진행한 세션에서 상한에 걸린 빈틈은 대기열에 남은 것과 그보다 더 깊은 것 모두 상한 탓으로 셈
    # - mst의 선수 개념을 모두 모르면 깊이 1·2 개념 5개를 묻고 끝남. 남은 7개 중 heap·queue·sorting-basics는 대기열,
    #   reference·array·binary-tree·adt는 대기열의 개념을 빈틈으로 판정해야 닿는 더 깊은 개념
    def test_miss_reasons_budget_with_real_engine(self):
        unknown = set(prerequisite_depths(MAP, 'mst'))
        profile = profile_with('mst', unknown)
        record = run_profile(profile, FakeApi(), Learner(profile, ITEMS, random.Random(0), fake_explainer), MAP, ITEMS)
        self.assertEqual(len(record['evidence']), MAX_DIAGNOSIS_ITEMS)
        self.assertEqual(record['miss_reasons'], {'not_probed_budget': len(unknown) - MAX_DIAGNOSIS_ITEMS})

    # 검증 목적: summarize가 M1(recall·precision·상한 대비)·M2(과잉 보충률)를 계산하고 어긋난 프로필을 따로 묶는지 확인
    def test_summarize(self):
        base = {'stage': 'complete', 'error': None, 'seconds': 1.0, 'steps': 10, 'miss_reasons': {}, 'false_gap_reasons': {}}
        results = [
            {**base, 'consistent': True, 'recall': 1.0, 'precision': 1.0, 'ceiling': 1.0, 'oracle_recall': 1.0,
             'remediated': ['a', 't'], 'over_remediated': [], 'target': 't'},
            {**base, 'consistent': True, 'recall': 0.5, 'precision': 0.5, 'ceiling': 0.5, 'oracle_recall': 0.5,
             'remediated': ['a', 'b', 't'], 'over_remediated': ['b'], 'target': 't', 'miss_reasons': {'not_probed_budget': 1}},
            {**base, 'consistent': False, 'recall': 0.0, 'precision': 1.0, 'ceiling': 1.0, 'oracle_recall': 0.0,
             'remediated': ['t'], 'over_remediated': [], 'target': 't'},
            {**base, 'consistent': True, 'stage': 'diagnose', 'error': '503', 'recall': None, 'precision': None,
             'miss_reasons': {'guessed': 1, 'not_probed_guessed': 2, 'not_probed_pruned': 1}},
        ]
        summary = summarize(results)
        consistent = summary['consistent']
        self.assertEqual((consistent['sessions'], consistent['completed'], consistent['failed']), (3, 2, 1))
        self.assertAlmostEqual(consistent['m1']['recall_mean'], 0.75)
        self.assertAlmostEqual(consistent['m1']['recall_over_ceiling_mean'], 1.0)
        self.assertEqual(consistent['m1']['sessions_below_ceiling'], 0)
        self.assertAlmostEqual(consistent['m2']['over_remediation_rate'], 1 / 5, places=3)
        self.assertAlmostEqual(consistent['m2']['prerequisite_over_remediation_rate'], 1 / 3, places=3)
        self.assertEqual(consistent['miss_reasons'],
                         {'not_probed_budget': 1, 'guessed': 1, 'not_probed_guessed': 2, 'not_probed_pruned': 1})
        # 발표용 묶음: 상한 탓(budget)·찍기 탓(guess: 직접 찍음 + 찍기로 가지치기)·나머지(other)
        self.assertEqual(consistent['miss_causes'], {'budget': 1, 'guess': 3, 'other': 1})
        self.assertEqual(summary['inconsistent']['miss_causes'], {'budget': 0, 'guess': 0, 'other': 0})
        self.assertEqual(summary['inconsistent']['sessions'], 1)
        self.assertAlmostEqual(summary['inconsistent']['m1']['recall_mean'], 0.0)

    # 검증 목적: --out이 ai-core/outputs/eval/ 밖이거나 이미 있으면 API·모델을 부르기 전에 거부
    def test_rejects_bad_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            for out in (Path(tmp) / 'x.jsonl',):
                with self.assertRaises(SystemExit):
                    main(['--base-url', 'http://127.0.0.1:1', '--learner-url', 'http://127.0.0.1:1/v1', '--out', str(out)])
        EVAL_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=EVAL_ROOT, suffix='.jsonl') as existing, self.assertRaises(SystemExit):
            main(['--base-url', 'http://127.0.0.1:1', '--learner-url', 'http://127.0.0.1:1/v1', '--out', existing.name])


if __name__ == '__main__':
    unittest.main()
