# 파일 역할: 외부 모델 없이 비교 측정의 지식 상태·종료·지표·기록 보호를 검증
# - 실패 방지 대상: target만 설명하고 조기 해결, 중복 보충 이중 집계, 10턴 초과·구독 한도 재시도
import json
import random
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from eval.sim_learner import chat_tutor as chat
from eval.sim_learner.learner import Learner
from eval.sim_learner.profiles import Profile, true_gaps
from serving.concept_map import load_concept_map
from serving.item_bank import load_item_bank

MAP = load_concept_map()
ITEMS = load_item_bank()
ALL = frozenset(c.id for c in MAP.concepts)


# 역할: 비용·토큰을 포함하는 가짜 Claude. 요청한 대화만 순서대로 반환
class FakeTutor:
    def __init__(self, texts):
        self.texts = iter(texts)
        self.inputs = []

    def reply(self, message):
        self.inputs.append(message)
        return {'text': next(self.texts), 'usage': {'input_tokens': 10, 'output_tokens': 5}, 'total_cost_usd': 0.01}


# 역할: 튜터 원문에 연결된 가짜 판정기로, 실제 측정 루프의 학습·집계를 검증
class FakeJudge:
    def __init__(self, judgments):
        self.judgments = iter(judgments)

    def __call__(self, text, previous):
        return next(self.judgments)


def profile():
    return Profile('rr-test', 'round-robin', ALL - {'round-robin', 'fcfs', 'queue'}, 0.0, 0.0, True)


def responder(text, states):
    return '짧은 학습자 답'


class ChatLoopTest(unittest.TestCase):
    # 검증: target이 설명돼도 truth의 fcfs·queue가 남으면 종료하지 않고, 전부 설명된 순간 종료
    def test_resolution_requires_every_truth_and_target(self):
        tutor = FakeTutor(['대상 설명', '큐 설명', '선수 설명', '불필요한 네 번째'])
        judge = FakeJudge([chat.Judgment([], ['round-robin']), chat.Judgment([], ['queue']),
                           chat.Judgment([], ['fcfs'])])
        r = chat.run_chat(profile(), 'C1', tutor, judge, responder, MAP, ITEMS)
        self.assertTrue(r['resolved'])
        self.assertEqual(r['learner_turns'], 3)
        self.assertEqual(len(tutor.inputs), 3)
        self.assertEqual(r['closure'], '이제 이해했어요')
        self.assertEqual(r['explained'], ['fcfs', 'queue', 'round-robin'])
        self.assertEqual(r['usage']['input_tokens'], 30)
        self.assertAlmostEqual(r['total_cost_usd'], 0.03)

    # 검증: 빈틈이 남으면 열 번째 응답에서 미해결로 종료, 열한 번째 Claude 호출 방지
    def test_unresolved_stops_at_ten_learner_turns(self):
        tutor = FakeTutor(['설명 없음'] * 11)
        r = chat.run_chat(profile(), 'C2', tutor, FakeJudge([chat.Judgment([], [])] * 11), responder, MAP, ITEMS)
        self.assertFalse(r['resolved'])
        self.assertEqual(r['learner_turns'], 10)
        self.assertEqual(len(tutor.inputs), 10)
        self.assertIsNone(r['error'])

    # 검증: 열 번째 응답에서 마지막 빈틈을 설명하면 상한 때문에 해결을 놓치지 않음
    def test_resolution_on_last_allowed_turn(self):
        tutor = FakeTutor(['대기'] * 9 + ['전부 설명'])
        judge = FakeJudge([chat.Judgment([], [])] * 9 + [chat.Judgment([], ['queue', 'fcfs', 'round-robin'])])
        r = chat.run_chat(profile(), 'C2', tutor, judge, responder, MAP, ITEMS)
        self.assertTrue(r['resolved'])
        self.assertEqual(r['learner_turns'], 10)

    # 검증: 이전 설명은 learn()에 반영하고, 같은 응답에서 답을 알려 준 질문은 진단으로 세지 않음
    def test_learning_precedes_answer_and_diagnostic_question_count(self):
        states_seen = []
        def respond(text, states):
            states_seen.append(states)
            return '답'
        tutor = FakeTutor(['큐를 알려 주고 큐 질문', 'fcfs 질문', '나머지 설명'])
        judge = FakeJudge([chat.Judgment(['queue'], ['queue']), chat.Judgment(['fcfs'], []),
                           chat.Judgment([], ['fcfs', 'round-robin'])])
        r = chat.run_chat(profile(), 'C1', tutor, judge, respond, MAP, ITEMS)
        self.assertEqual(states_seen[0]['queue'], 'known')
        self.assertEqual(states_seen[1]['fcfs'], 'unknown')
        self.assertEqual(r['diagnostic_questions'], 1)
        self.assertEqual(r['explained_after_probe'], ['fcfs'])

    # 검증: Claude usage의 중첩 서버 도구 집계·서비스 문자열은 원본에 남기고 토큰 합계와 구분
    def test_nested_usage_keeps_raw_metadata_and_sums_only_tokens(self):
        class NestedTutor:
            def reply(self, message):
                return {'text': '전부 설명', 'total_cost_usd': 0.1,
                        'usage': {'input_tokens': 4, 'output_tokens': 2, 'cache_read_input_tokens': 6,
                                  'server_tool_use': {'web_search_requests': 0}, 'service_tier': 'standard'}}
        r = chat.run_chat(profile(), 'C1', NestedTutor(),
                          FakeJudge([chat.Judgment([], ['queue', 'fcfs', 'round-robin'])]), responder, MAP, ITEMS)
        self.assertIsNone(r['error'])
        self.assertEqual(r['usage'], {'input_tokens': 4, 'output_tokens': 2, 'cache_read_input_tokens': 6})
        self.assertEqual(r['turns'][0]['usage']['service_tier'], 'standard')

    # 검증: 질문 없는 턴에 정답 요약을 생성하지 않고, 처음 나온 미학습 용어를 되물음
    def test_no_question_clarifies_first_unknown_term_without_model_answer(self):
        def forbidden(text, states):
            raise AssertionError('질문 없는 턴에 Qwen이 정답을 지어내면 안 됨')
        tutor = FakeTutor(['큐와 FCFS 스케줄링을 다음에 설명할게요.'] + ['설명 없음'] * 9)
        judge = FakeJudge([chat.Judgment([], [])] * 10)
        r = chat.run_chat(profile(), 'C1', tutor, judge, forbidden, MAP, ITEMS)
        self.assertEqual(tutor.inputs[1], '큐가 뭔지 잘 모르겠어요.')
        self.assertEqual(r['turns'][0]['clarification_concept'], 'queue')
        self.assertEqual(r['clarification_rules']['unknown_term'], 1)
        self.assertEqual(r['clarification_rules']['repeat_confusion'], 8)
        self.assertIsNone(r['error'])

    # 검증: 이름에 부분 문자열이 겹치면 먼저 나온 긴 이름 우선, 이미 설명받은 개념은 되묻지 않음
    def test_clarification_uses_current_knowledge_and_literal_names(self):
        message, cid = chat.clarification('우선순위 큐 다음에 큐를 보자.', ALL - {'priority-queue', 'queue'}, MAP)
        self.assertEqual(cid, 'priority-queue')
        self.assertEqual(message, '우선순위 큐가 뭔지 잘 모르겠어요.')
        self.assertEqual(chat.clarification('Queue와 enqueue', ALL - {'queue'}, MAP)[1], 'queue')
        self.assertEqual(chat.clarification('큐를 배웠어요.', ALL, MAP), ('아직 잘 모르겠어요.', None))

    # 검증: 구독 한도 오류는 실패 기록을 반환하고, 같은 세션을 재호출하지 않음
    def test_quota_error_is_recorded_without_retry(self):
        class LimitedTutor:
            def reply(self, message):
                raise chat.ClaudeLimitError('usage limit reached')
        r = chat.run_chat(profile(), 'C1', LimitedTutor(), FakeJudge([]), responder, MAP, ITEMS)
        self.assertTrue(r['rate_limited'])
        self.assertFalse(r['resolved'])
        self.assertIn('ClaudeLimitError', r['error'])
        self.assertEqual(r['learner_turns'], 0)


class MetricsTest(unittest.TestCase):
    # 검증: 중복 설명은 한 개념으로 계산, 초기 known만 과잉 보충으로 계산, 과목 경계 따로 합산
    def test_pooled_metrics_and_wilson_intervals(self):
        records = [
            {'condition': 'C1', 'truth': ['queue', 'fcfs'], 'target': 'round-robin',
             'initial_known': ['memory', 'adt'], 'cross_subject': ['queue'],
             'explained': ['queue', 'queue', 'memory', 'round-robin'], 'resolved': False,
             'learner_turns': 10, 'reading_chars': 100, 'tutor_chars': 80, 'diagnostic_questions': 0,
             'usage': {'input_tokens': 20, 'output_tokens': 7}, 'total_cost_usd': 0.1, 'error': None},
            {'condition': 'C1', 'truth': ['queue'], 'target': 'round-robin', 'initial_known': ['adt'],
             'cross_subject': ['queue'], 'explained': ['queue', 'round-robin'], 'resolved': True,
             'learner_turns': 2, 'reading_chars': 50, 'tutor_chars': 30, 'diagnostic_questions': 2,
             'usage': {'input_tokens': 10, 'output_tokens': 3}, 'total_cost_usd': 0.2, 'error': None},
        ]
        s = chat.summarize_condition(records)
        self.assertEqual(s['recall']['numerator'], 2)
        self.assertEqual(s['recall']['denominator'], 3)
        self.assertAlmostEqual(s['recall']['rate'], 2 / 3)
        self.assertAlmostEqual(s['over_remediation']['rate'], 1 / 5)
        self.assertEqual(s['cross_subject_recall']['rate'], 1.0)
        self.assertEqual(s['resolution']['rate'], 0.5)
        self.assertEqual(s['resolved_turns_mean'], 2)
        self.assertEqual(s['reading_chars_total'], 150)
        self.assertEqual(s['usage'], {'input_tokens': 30, 'output_tokens': 10})
        self.assertAlmostEqual(s['total_cost_usd'], 0.3)
        lo, hi = s['recall']['ci95']
        self.assertTrue(lo < 2 / 3 < hi)

    # 검증: 원래 학습 화면의 제목·설명만 사용하고, 개념 지도 선수 관계를 Claude 입력에 누설하지 않음
    def test_initial_message_matches_learning_screen(self):
        concept = next(c for c in MAP.concepts if c.id == 'round-robin')
        self.assertEqual(chat.learning_screen(concept), f'{concept.title}\n{concept.description}')
        self.assertIn('여기서 막혔어요. 이해가 안 돼요.', chat.initial_message(concept))

    # 검증: seed 7로 재현되는 20명, 과목별 10명, consistent·선수 빈틈 존재, os 경계 빈틈 최대 포함
    def test_profile_selection_is_reproducible_and_balanced(self):
        selected = chat.select_profiles(MAP)
        self.assertEqual(selected, chat.select_profiles(MAP))
        self.assertEqual(len({p.id for p in selected}), 20)
        by_id = {c.id: c for c in MAP.concepts}
        self.assertEqual(sum(by_id[p.target].subject == 'dsa' for p in selected), 10)
        self.assertEqual(sum(by_id[p.target].subject == 'os' for p in selected), 10)
        self.assertTrue(all(p.consistent and true_gaps(p, MAP) - {p.target} for p in selected))
        crossing = [p for p in selected if by_id[p.target].subject == 'os' and
                    any(by_id[c].subject != 'os' for c in true_gaps(p, MAP) - {p.target})]
        self.assertEqual(len(crossing), 10)

    # 검증: open 질문의 guess 금지, 선택형 guess는 답만 맞고 지식 상태는 학습 전까지 바뀌지 않음
    def test_reused_answer_state_preserves_slip_guess_meanings(self):
        p = Profile('p', 'fcfs', frozenset({'memory'}), 1.0, 1.0, True)
        learner = Learner(p, ITEMS, random.Random(0), lambda q, r: '')
        self.assertEqual(learner.answer_state('memory'), 'slip')
        self.assertEqual(learner.answer_state('queue'), 'unknown')
        self.assertEqual(learner.answer_state('queue', multiple_choice=True), 'guess')
        self.assertNotIn('queue', learner.known)
        learner.learn('queue')
        self.assertEqual(learner.answer_state('queue'), 'slip')


class BoundaryTest(unittest.TestCase):
    # 검증: 모델의 임의 ID·존재하지 않는 행 번호를 성공 판정으로 받아들이지 않음
    def test_judgment_validation_rejects_invalid_ids_and_evidence(self):
        for row in ({'concept': 'invented', 'line': 1}, {'concept': 'queue', 'line': 2}):
            with self.assertRaises(ValueError):
                chat.parse_judgment({'questions': [], 'explanations': [row]}, '큐', MAP)

    # 검증: 실제 선택지 없는 choice 판정을 open으로 바꾸고 보정 횟수를 기록, 명시적 A/B 보기는 유지
    def test_line_evidence_and_choice_correction(self):
        payload = {'questions': [{'concept': 'queue', 'line': 1, 'format': 'choice'}], 'explanations': []}
        j = chat.parse_judgment(payload, '큐가 뭔지 알고 있나요?', MAP)
        self.assertEqual(j.asked, ['queue'])
        self.assertEqual(j.choices, [])
        self.assertEqual(j.choice_corrections, 1)
        self.assertEqual(j.evidence['questions'][0]['source'], '큐가 뭔지 알고 있나요?')
        j = chat.parse_judgment(payload, '큐의 순서는?\nA) FIFO\nB) LIFO', MAP)
        self.assertEqual(j.choices, ['queue'])
        self.assertEqual(j.choice_corrections, 0)

    # 검증: 실제 (가)/(나) 보기를 형식 오류로 보정해 선택형 guess 기회를 없애지 않음
    def test_parenthesized_korean_choices_are_preserved(self):
        payload = {'questions': [{'concept': 'priority-scheduling', 'line': 1, 'format': 'choice'}],
                   'explanations': []}
        text = 'C가 밀리는 이유는?\n- (가) 다른 우선순위 숫자가 커져서\n- (나) 높은 프로세스가 새로 들어와서'
        judgment = chat.parse_judgment(payload, text, MAP)
        self.assertEqual(judgment.choices, ['priority-scheduling'])
        self.assertEqual(judgment.choice_corrections, 0)

    # 검증: CLI 격리 옵션·resume을 실제 subprocess 경계에서 확인하고, 호출마다 usage·비용 기록
    def test_cli_isolation_resume_and_usage(self):
        responses = [subprocess.CompletedProcess([], 0, json.dumps({
            'result': '응답', 'session_id': 'session-a', 'usage': {'input_tokens': 3, 'output_tokens': 2},
            'total_cost_usd': 0.004, 'is_error': False}), '')] * 2
        with tempfile.TemporaryDirectory() as directory, patch.object(chat.subprocess, 'run', side_effect=responses) as run:
            tutor = chat.ClaudeTutor('C1', Path(directory))
            first = tutor.reply('첫 질문')
            tutor.reply('다음 질문')
            self.assertEqual(first['usage']['input_tokens'], 3)
            self.assertEqual(first['total_cost_usd'], 0.004)
            first_cmd = run.call_args_list[0].args[0]
            second_cmd = run.call_args_list[1].args[0]
            for flag, value in [('--tools', ''), ('--setting-sources', ''), ('--settings', '{"disableAllHooks":true}')]:
                self.assertEqual(first_cmd[first_cmd.index(flag) + 1], value)
            self.assertIn('--strict-mcp-config', first_cmd)
            self.assertEqual(second_cmd[second_cmd.index('--resume') + 1], 'session-a')
            self.assertEqual(run.call_args_list[0].kwargs['cwd'], Path(directory))

    # 검증: 대체 판정기를 선택해도 학습자 문장은 계속 지정된 Qwen 클라이언트로 생성
    def test_judge_model_is_separate_from_learner(self):
        calls = []
        def model_factory(url, model, **kwargs):
            def create(**payload):
                calls.append((url, payload))
                return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='응답'))])
            return SimpleNamespace(model=model, client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
        with patch.object(chat, 'LearnerModel', side_effect=model_factory):
            roles = chat.QwenRoles('learner-url', 'qwen', MAP, ITEMS, judge_url='judge-url', judge_model='gemma')
            roles._complete('system', '판정', roles.schema)
            roles._complete('system', '학습자 답')
        self.assertEqual([url for url, _ in calls], ['judge-url', 'learner-url'])
        self.assertEqual(calls[0][1]['model'], 'gemma')
        self.assertEqual(calls[1][1]['model'], 'qwen')
        self.assertEqual(calls[0][1]['response_format']['type'], 'json_schema')

    # 검증: 파일럿 공용 비용 예산에 도달하면 다음 유료 CLI 호출을 하지 않음
    def test_shared_budget_stops_before_another_paid_call(self):
        payload = subprocess.CompletedProcess([], 0, json.dumps({'result': '설명', 'session_id': 'session-a',
                   'usage': {'output_tokens': 2}, 'total_cost_usd': 0.1, 'is_error': False}), '')
        budget = {'spent': 0.0, 'limit': 0.2}
        with tempfile.TemporaryDirectory() as directory, patch.object(chat.subprocess, 'run', return_value=payload) as run:
            tutor = chat.ClaudeTutor('C1', Path(directory), budget=budget)
            tutor.reply('첫 질문')
            tutor.reply('다음 질문')
            with self.assertRaises(chat.ClaudeBudgetError):
                tutor.reply('예산 초과 질문')
            self.assertEqual(budget['spent'], 0.2)
            self.assertEqual(run.call_count, 2)

    # 검증: 한도 오류를 반환한 유료 CLI 응답도 비용·토큰·원문을 보존하고 다음 호출을 하지 않음
    def test_limit_reply_preserves_charged_usage_and_text(self):
        payload = subprocess.CompletedProcess([], 1, json.dumps({
            'result': "You've hit your limit", 'session_id': '98b2f510-8e2f-4ca3-9ce1-e8fab3b9b3cf',
            'usage': {'input_tokens': 7, 'output_tokens': 2}, 'total_cost_usd': 0.02, 'is_error': True}), '')
        budget = {'spent': 0.0, 'limit': 1.0}
        with tempfile.TemporaryDirectory() as directory, patch.object(chat.subprocess, 'run', return_value=payload) as run:
            tutor = chat.ClaudeTutor('C1', Path(directory), budget=budget)
            record = chat.run_chat(profile(), 'C1', tutor, FakeJudge([]), responder, MAP, ITEMS)
        self.assertTrue(record['rate_limited'])
        self.assertAlmostEqual(record['total_cost_usd'], 0.02)
        self.assertEqual(record['usage'], {'input_tokens': 7, 'output_tokens': 2})
        self.assertEqual(record['turns'][0]['tutor'], "You've hit your limit")
        self.assertEqual(budget['spent'], 0.02)
        self.assertEqual(run.call_count, 1)

    # 검증: 판정기 오류가 있어도 이미 발생한 Claude 비용·원문을 버리지 않음
    def test_judge_error_preserves_paid_reply(self):
        def broken(text, previous):
            raise ValueError('bad JSON')
        r = chat.run_chat(profile(), 'C1', FakeTutor(['유료 응답']), broken, responder, MAP, ITEMS)
        self.assertIn('bad JSON', r['error'])
        self.assertEqual(r['total_cost_usd'], 0.01)
        self.assertEqual(r['turns'][0]['tutor'], '유료 응답')


if __name__ == '__main__':
    unittest.main()
