import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
from pydantic import ValidationError
from serving.api import app, BUSY
from serving.curriculum import (DraftRequest, JobState, Unit, UnitPlan, Lesson,
    OutlinePage, UnitContent, Findings, FinalReview, Issue, Objectives, ModelGateway, CapacityError, step)

INPUT = {'topic': '페이징', 'goal': '취업 준비', 'level': '용어만 알아요'}
PLAN = UnitPlan(title='주소 공간', scope='주소 공간과 변환', topics=['페이지', '주소 변환'])
LESSONS = [Lesson(title=t, objectives=[t + '을 설명한다']) for t in PLAN.topics]
DRAFT = UnitContent(lessons=[Objectives(objectives=l.objectives) for l in LESSONS])


def unit(id='u1'):
    return Unit(id=id, plan=PLAN.model_copy(deep=True))


class FakeGateway:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []
    def ask(self, state, instruction, schema, current, others):
        self.calls.append((schema, current))
        answer = self.results.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class CurriculumTests(unittest.TestCase):
    def test_input_bounds_and_variable_counts(self):
        for value in ['', ' ', 'x' * 301]:
            with self.assertRaises(ValidationError): DraftRequest(**{**INPUT, 'topic': value})
        for count in (1, 7):
            d = UnitPlan(title='학습', scope='학습 범위', topics=[str(i) for i in range(count)])
            self.assertEqual(len(d.topics), count)
            self.assertEqual(len(Objectives(objectives=['목표'] * 4).objectives), 4)
        with self.assertRaises(ValidationError): UnitPlan(title='학습', scope='범위', topics=[])
        with self.assertRaises(ValidationError): UnitPlan(title='학습', scope='범위', topics=['중복'] * 2)

    def test_outline_generation_review_and_final_completion(self):
        g = FakeGateway(OutlinePage(unit=PLAN, done=False), OutlinePage(unit=None, done=True),
                        DRAFT, Findings(issues=[]), FinalReview(issues=[]))
        s = JobState(request=DraftRequest(**INPUT))
        for _ in range(5): s = step(s, g)
        self.assertEqual(s.stage, 'complete')
        self.assertTrue(s.units[0].reviewed)
        self.assertEqual(s.units[0].lessons, LESSONS)
        self.assertEqual([c[0] for c in g.calls], [OutlinePage, OutlinePage, UnitContent, Findings, FinalReview])

    def test_multiple_repairs_keep_other_units_and_complete_one_review_round(self):
        first = unit(); first.lessons = LESSONS; first.reviewed = True; first.feedback = ['수정 A']
        second = unit('u2'); second.plan.title = '두 번째 단원'
        second.lessons = LESSONS; second.reviewed = True; second.feedback = ['수정 B']
        s = JobState(request=DraftRequest(**INPUT), stage='final', units=[first, second])
        g = FakeGateway(FinalReview(issues=[]), first.plan, DRAFT, second.plan, DRAFT,
                        Findings(issues=[]), Findings(issues=[]), FinalReview(issues=[]))
        for _ in range(8): s = step(s, g)
        self.assertEqual(s.stage, 'complete')
        self.assertEqual(s.repairs, 1)
        self.assertEqual([u.id for u in s.units], ['u1', 'u2'])
        self.assertTrue(all(u.reviewed and not u.feedback for u in s.units))

    def test_truncation_splits_and_continues_without_losing_prior_units(self):
        prior = unit('u0'); prior.lessons = LESSONS
        s = JobState(request=DraftRequest(**INPUT), stage='generate', units=[prior, unit()], cursor=1)
        g = FakeGateway(CapacityError('length'), Objectives(objectives=['A']), Objectives(objectives=['B']))
        s = step(s, g)
        self.assertTrue(s.units[1].split)
        self.assertEqual(s.units[1].lessons, [])
        s = step(s, g)
        self.assertEqual(len(s.units[1].lessons), 1)
        # Serialized checkpoint can resume in a fresh request.
        s = step(JobState.model_validate_json(s.model_dump_json()), g)
        self.assertEqual(s.stage, 'review')
        self.assertEqual(s.units[0].lessons, prior.lessons)
        self.assertEqual([l.objectives for l in s.units[1].lessons], [['A'], ['B']])

    def test_large_outline_retries_as_smaller_unit(self):
        g = FakeGateway(CapacityError('length'), OutlinePage(unit=PLAN, done=False))
        s = step(JobState(request=DraftRequest(**INPUT)), g)
        self.assertTrue(s.outline_split)
        s = step(s, g)
        self.assertEqual(len(s.units), 1)

    def test_review_can_split_and_never_skip_feedback(self):
        u = unit(); u.lessons = LESSONS
        s = JobState(request=DraftRequest(**INPUT), stage='review', units=[u])
        g = FakeGateway(CapacityError('too large'), Findings(issues=['용어 수정']), Findings(issues=[]), FinalReview(issues=[]))
        for _ in range(4): s = step(s, g)
        self.assertEqual(s.stage, 'repair')
        self.assertEqual(s.units[0].feedback, ['용어 수정'])

    def test_final_review_repairs_then_rechecks(self):
        u = unit(); u.lessons = LESSONS; u.reviewed = True
        s = JobState(request=DraftRequest(**INPUT), stage='final', units=[u])
        g = FakeGateway(FinalReview(issues=[Issue(unit_id='u1', reason='선수 개념 보완')]), PLAN,
                        DRAFT, Findings(issues=[]), FinalReview(issues=[]))
        for _ in range(5): s = step(s, g)
        self.assertEqual(s.stage, 'complete')
        self.assertEqual(s.repairs, 1)
        self.assertEqual(s.units[0].feedback, [])

    def test_failure_preserves_checkpoint_and_bounded_retry(self):
        s = JobState(request=DraftRequest(**INPUT), stage='generate', units=[unit()])
        original = s.model_dump()
        g = FakeGateway(ValueError('invalid JSON'), ValueError('invalid JSON'), ValueError('invalid JSON'))
        for _ in range(3): s = step(s, g)
        self.assertEqual(s.stage, 'blocked')
        self.assertEqual(s.units[0].model_dump(), original['units'][0])
        self.assertEqual(s.steps, 3)

    def test_no_silent_completion_when_minimal_context_cannot_fit(self):
        u = unit(); u.split = True
        s = step(JobState(request=DraftRequest(**INPUT), stage='generate', units=[u]), FakeGateway(CapacityError('required too large')))
        self.assertEqual(s.stage, 'blocked')
        self.assertEqual(s.units[0].lessons, [])

    def test_missing_topic_or_unknown_review_id_is_not_accepted(self):
        s = JobState(request=DraftRequest(**INPUT), stage='generate', units=[unit()])
        s = step(s, FakeGateway(UnitContent(lessons=[Objectives(objectives=LESSONS[0].objectives)])))
        self.assertEqual(s.retries, 1)
        self.assertEqual(s.units[0].lessons, [])
        s.stage = 'final'
        s = step(s, FakeGateway(FinalReview(issues=[Issue(unit_id='unknown', reason='오류')])) )
        self.assertNotEqual(s.stage, 'complete')

    def test_work_and_repair_limits_pause_not_complete(self):
        with patch('serving.curriculum.MAX_STEPS', 0):
            s = step(JobState(request=DraftRequest(**INPUT)), FakeGateway())
            self.assertEqual(s.stage, 'blocked')
        u = unit(); u.lessons = LESSONS; u.reviewed = True; u.feedback = ['문제']
        with patch('serving.curriculum.MAX_REPAIRS', 0):
            s = step(JobState(request=DraftRequest(**INPUT), stage='final', units=[u]), FakeGateway(FinalReview(issues=[])))
            self.assertEqual(s.stage, 'blocked')

    def test_dynamic_budget_compacts_view_not_original_and_counts_same_messages(self):
        state = JobState(request=DraftRequest(**INPUT))
        other = unit(); other.lessons = LESSONS
        before = other.model_dump()
        http = Mock()
        http.post.side_effect = [Mock(json=lambda: {'count': 900}), Mock(json=lambda: {'count': 200})]
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=200, completion_tokens=20),
            choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='{"issues":[]}'))])
        gateway = ModelGateway(client, http, 'model', 1000)
        result = gateway.ask(state, '검토', Findings, {'required': 'keep'}, [other])
        payload = client.chat.completions.create.call_args.kwargs
        self.assertEqual(payload['max_tokens'], 544)
        self.assertEqual(payload['messages'], http.post.call_args.kwargs['json']['messages'])
        content = json.loads(payload['messages'][1]['content'])
        self.assertEqual(content['requirements'], INPUT)
        self.assertEqual(content['current'], {'required': 'keep'})
        self.assertNotIn('lessons', content['other_units'][0])
        self.assertEqual(other.model_dump(), before)
        self.assertEqual(state.compactions, 1)
        self.assertEqual(result.issues, [])
        client.chat.completions.create.return_value.choices[0].finish_reason = 'length'
        http.post.side_effect = None; http.post.return_value = Mock(json=lambda: {'count': 200})
        with self.assertRaises(CapacityError): gateway.ask(state, '검토', Findings, {}, [])

    @patch('serving.api.OpenAI')
    @patch('serving.api.step')
    def test_api_failure_releases_lock(self, advance, client):
        model = SimpleNamespace(id='test', model_extra={'max_model_len': 32768})
        client.return_value.__enter__.return_value.models.list.return_value.data = [model]
        client.return_value.__enter__.return_value.base_url = 'http://127.0.0.1:8000/v1/'
        client.return_value.__enter__.return_value.api_key = 'test'
        advance.side_effect = ValueError('private error')
        with TestClient(app) as http:
            r = http.post('/curriculum/step', json={'request': INPUT})
            self.assertEqual(r.status_code, 502)
            self.assertNotIn('private error', r.text)
            self.assertFalse(BUSY.locked())

    def test_api_invalid_and_busy(self):
        with TestClient(app) as http:
            self.assertEqual(http.post('/curriculum/step', json={'request': {**INPUT, 'topic': ' '}}).status_code, 422)
            BUSY.acquire()
            try:
                self.assertEqual(http.post('/curriculum/step', json={'request': INPUT}).status_code, 503)
            finally: BUSY.release()
