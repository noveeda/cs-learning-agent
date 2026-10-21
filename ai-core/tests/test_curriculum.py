# 파일 역할: 가짜 모델 응답으로 단계 전환·압축·분할·재개·검토 반복 중단을 검증
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
from pydantic import ValidationError
from serving.api import app, BUSY
from serving.curriculum import (DraftRequest, JobState, Unit, UnitPlan, Lesson,
    OutlinePage, UnitContent, Findings, FinalReview, Issue, Objectives, ModelGateway, CapacityError, ReviewGuard, ReviewInterrupted, step)

INPUT = {'topic': '페이징', 'goal': '취업 준비', 'level': '용어만 알아요'}
PLAN = UnitPlan(title='주소 공간', scope='주소 공간과 변환', topics=['페이지', '주소 변환'])
LESSONS = [Lesson(title=t, objectives=[t + '을 설명한다']) for t in PLAN.topics]
DRAFT = UnitContent(lessons=[Objectives(objectives=l.objectives) for l in LESSONS])


# 역할: 테스트 간 상태 공유를 피하도록 기본 단원 계획을 깊은 복사
def unit(id='u1'):
    return Unit(id=id, plan=PLAN.model_copy(deep=True))


# 역할: 모델 호출 순서를 기록하고 준비된 응답 또는 예외를 차례대로 반환
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
    # 검증 목적: 입력 길이 제한과 교육적 개수 제한 부재 확인
    def test_input_bounds_and_variable_counts(self):
        for value in ['', ' ', 'x' * 301]:
            with self.assertRaises(ValidationError): DraftRequest(**{**INPUT, 'topic': value})
        for count in (1, 7):
            d = UnitPlan(title='학습', scope='학습 범위', topics=[str(i) for i in range(count)])
            self.assertEqual(len(d.topics), count)
            self.assertEqual(len(Objectives(objectives=['목표'] * 4).objectives), 4)
        with self.assertRaises(ValidationError): UnitPlan(title='학습', scope='범위', topics=[])
        with self.assertRaises(ValidationError): UnitPlan(title='학습', scope='범위', topics=['중복'] * 2)

    # 검증 목적: 개요·생성·검토를 거쳐야 완료로 전환되는지 확인
    def test_outline_generation_review_and_final_completion(self):
        g = FakeGateway(OutlinePage(unit=PLAN, done=False), OutlinePage(unit=None, done=True),
                        DRAFT, Findings(issues=[]), FinalReview(issues=[]))
        s = JobState(request=DraftRequest(**INPUT))
        for _ in range(5): s = step(s, g)
        self.assertEqual(s.stage, 'complete')
        self.assertTrue(s.units[0].reviewed)
        self.assertEqual(s.units[0].lessons, LESSONS)
        self.assertEqual([c[0] for c in g.calls], [OutlinePage, OutlinePage, UnitContent, Findings, FinalReview])

    # 검증 목적: 여러 단원의 보완이 다른 단원과 ID를 보존하는지 확인
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

    # 검증 목적: 출력 잘림 후 분할 생성과 직렬화된 상태 재개 확인
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

    # 검증 목적: 개요가 한도를 넘으면 더 작은 개요로 재시도하는지 확인
    def test_large_outline_retries_as_smaller_unit(self):
        g = FakeGateway(CapacityError('length'), OutlinePage(unit=PLAN, done=False))
        s = step(JobState(request=DraftRequest(**INPUT)), g)
        self.assertTrue(s.outline_split)
        s = step(s, g)
        self.assertEqual(len(s.units), 1)

    # 검증 목적: 검토를 분할해도 피드백이 누락되지 않는지 확인
    def test_review_can_split_and_never_skip_feedback(self):
        u = unit(); u.lessons = LESSONS
        s = JobState(request=DraftRequest(**INPUT), stage='review', units=[u])
        g = FakeGateway(CapacityError('too large'), Findings(issues=['용어 수정']), Findings(issues=[]), FinalReview(issues=[]))
        for _ in range(4): s = step(s, g)
        self.assertEqual(s.stage, 'repair')
        self.assertEqual(s.units[0].feedback, ['용어 수정'])

    # 검증 목적: 전체 검토의 지적을 보완하고 다시 검토하는지 확인
    def test_final_review_repairs_then_rechecks(self):
        u = unit(); u.lessons = LESSONS; u.reviewed = True
        s = JobState(request=DraftRequest(**INPUT), stage='final', units=[u])
        g = FakeGateway(FinalReview(issues=[Issue(unit_id='u1', reason='선수 개념 보완')]), PLAN,
                        DRAFT, Findings(issues=[]), FinalReview(issues=[]))
        for _ in range(5): s = step(s, g)
        self.assertEqual(s.stage, 'complete')
        self.assertEqual(s.repairs, 1)
        self.assertEqual(s.units[0].feedback, [])

    # 검증 목적: 반복 실패 시 체크포인트 보존과 재시도 한도 확인
    def test_failure_preserves_checkpoint_and_bounded_retry(self):
        s = JobState(request=DraftRequest(**INPUT), stage='generate', units=[unit()])
        original = s.model_dump()
        g = FakeGateway(ValueError('invalid JSON'), ValueError('invalid JSON'), ValueError('invalid JSON'))
        for _ in range(3): s = step(s, g)
        self.assertEqual(s.stage, 'blocked')
        self.assertEqual(s.units[0].model_dump(), original['units'][0])
        self.assertEqual(s.steps, 3)

    # 검증 목적: 필수 문맥이 넘치는 작업을 완료로 오인하지 않는지 확인
    def test_no_silent_completion_when_minimal_context_cannot_fit(self):
        u = unit(); u.split = True
        s = step(JobState(request=DraftRequest(**INPUT), stage='generate', units=[u]), FakeGateway(CapacityError('required too large')))
        self.assertEqual(s.stage, 'blocked')
        self.assertEqual(s.units[0].lessons, [])

    # 검증 목적: 소단원 누락과 알 수 없는 단원 ID를 거부하는지 확인
    def test_missing_topic_or_unknown_review_id_is_not_accepted(self):
        s = JobState(request=DraftRequest(**INPUT), stage='generate', units=[unit()])
        s = step(s, FakeGateway(UnitContent(lessons=[Objectives(objectives=LESSONS[0].objectives)])))
        self.assertEqual(s.retries, 1)
        self.assertEqual(s.units[0].lessons, [])
        s.stage = 'final'
        s = step(s, FakeGateway(FinalReview(issues=[Issue(unit_id='unknown', reason='오류')])) )
        self.assertNotEqual(s.stage, 'complete')

    # 검증 목적: 작업·보완 한도 도달 시 완료가 아닌 중단으로 처리하는지 확인
    def test_work_and_repair_limits_pause_not_complete(self):
        with patch('serving.curriculum.MAX_STEPS', 0):
            s = step(JobState(request=DraftRequest(**INPUT)), FakeGateway())
            self.assertEqual(s.stage, 'blocked')
        u = unit(); u.lessons = LESSONS; u.reviewed = True; u.feedback = ['문제']
        with patch('serving.curriculum.MAX_REPAIRS', 0):
            s = step(JobState(request=DraftRequest(**INPUT), stage='final', units=[u]), FakeGateway(FinalReview(issues=[])))
            self.assertEqual(s.stage, 'blocked')

    # 검증 목적: 압축 시 원본 보존과 토큰 계산·생성 메시지 일치 확인
    def test_dynamic_budget_compacts_view_not_original_and_counts_same_messages(self):
        state = JobState(request=DraftRequest(**INPUT))
        other = unit(); other.lessons = LESSONS
        before = other.model_dump()
        http = Mock()
        http.post.side_effect = [Mock(json=lambda: {'count': 900}), Mock(json=lambda: {'count': 200})]
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=200, completion_tokens=20),
            choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='{"objectives":["목표"]}'))])
        gateway = ModelGateway(client, http, 'model', 1000)
        result = gateway.ask(state, '생성', Objectives, {'required': 'keep'}, [other])
        payload = client.chat.completions.create.call_args.kwargs
        self.assertEqual(payload['max_tokens'], 544)
        self.assertEqual(payload['messages'], http.post.call_args.kwargs['json']['messages'])
        content = json.loads(payload['messages'][1]['content'])
        self.assertEqual(content['requirements'], INPUT)
        self.assertEqual(content['current'], {'required': 'keep'})
        self.assertNotIn('lessons', content['other_units'][0])
        self.assertEqual(other.model_dump(), before)
        self.assertEqual(state.compactions, 1)
        self.assertEqual(result.objectives, ['목표'])
        client.chat.completions.create.return_value.choices[0].finish_reason = 'length'
        http.post.side_effect = None; http.post.return_value = Mock(json=lambda: {'count': 200})
        with self.assertRaises(CapacityError): gateway.ask(state, '생성', Objectives, {}, [])


    # 검증 목적: 청크 경계와 무관한 반복 탐지 및 다른 단원의 같은 이유 허용 확인
    def test_review_guard_repetition_across_chunk_boundaries(self):
        for text in (' ' * 256, '반복되는 긴 문장입니다. ' * 30,
                     '{"issues":["같은 문제", "같은 문제"]}'):
            guard = ReviewGuard()
            with self.assertRaises(ReviewInterrupted):
                for char in text:
                    guard.feed(char)
        guard = ReviewGuard()
        valid = '{"issues":[{"unit_id":"u1","reason":"용어 수정"},{"unit_id":"u2","reason":"용어 수정"}]}'
        guard.feed(valid)
        self.assertEqual(guard.text, valid)

    # 역할: 요청과 스트림 종료 호출을 관찰할 수 있는 가짜 게이트웨이 구성
    def review_gateway(self, chunks):
        client, http = Mock(), Mock()
        stream = Mock()
        stream.__enter__ = Mock(return_value=iter(chunks))
        stream.__exit__ = Mock(return_value=False)
        client.chat.completions.create.return_value = stream
        http.post.return_value = Mock(json=lambda: {'count': 100})
        return ModelGateway(client, http, 'model', 32768), stream

    # 역할: 검토 스트림의 텍스트·종료 사유·사용량을 가진 이벤트 생성
    def chunk(self, text='', finish=None, usage=None):
        return SimpleNamespace(usage=usage, choices=[SimpleNamespace(
            delta=SimpleNamespace(content=text), finish_reason=finish)])

    # 검증 목적: 스트림 사용량 수집과 이전 피드백의 새 검토 입력 제외 확인
    def test_review_stream_usage_and_no_feedback_in_review_input(self):
        chunks = [self.chunk('{"issues":'), self.chunk('[]}', 'stop'),
                  self.chunk(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=8))]
        gateway, stream = self.review_gateway(chunks)
        other = unit(); other.feedback = ['심화 설명 추가 요구']
        state = JobState(request=DraftRequest(**INPUT))
        result = gateway.ask(state, '검토', Findings, {}, [other])
        self.assertEqual(result.issues, [])
        self.assertEqual(state.usage['output_tokens'], 8)
        self.assertEqual(state.usage['streamed_chars'], 13)
        args = gateway.client.chat.completions.create.call_args.kwargs
        self.assertTrue(args['stream'])
        self.assertEqual(args['stream_options'], {'include_usage': True})
        self.assertNotIn('feedback', json.loads(args['messages'][1]['content'])['other_units'][0])
        self.assertEqual(other.feedback, ['심화 설명 추가 요구'])
        stream.__exit__.assert_called_once()

    # 검증 목적: 반복 중단 시 연결 종료와 완료 결과 보존 확인
    def test_review_repetition_closes_stream_and_preserves_checkpoint(self):
        gateway, stream = self.review_gateway([self.chunk('{"issues":["동일 문제",'), self.chunk('"동일 문제"]}')])
        u = unit(); u.lessons = LESSONS; u.reviewed = True
        state = JobState(request=DraftRequest(**INPUT), stage='final', units=[u])
        result = step(state, gateway)
        self.assertEqual(result.stage, 'final')
        self.assertEqual(result.units, state.units)
        self.assertEqual(result.retries, 1)
        self.assertIn('반복', result.message)
        self.assertGreater(result.usage['streamed_chars'], 0)
        stream.__exit__.assert_called_once()

    # 검증 목적: 잘림·불완전 응답·시간 초과를 정상 완료로 처리하지 않는지 확인
    def test_review_stream_length_disconnect_and_deadline_do_not_complete(self):
        for chunks, error in [([self.chunk('{"issues":', 'length')], CapacityError),
                              ([self.chunk('{"issues":')], ValueError)]:
            gateway, stream = self.review_gateway(chunks)
            with self.assertRaises(error):
                gateway.ask(JobState(request=DraftRequest(**INPUT)), '검토', Findings, {}, [])
            stream.__exit__.assert_called_once()
        gateway, stream = self.review_gateway([self.chunk('{"issues":[]}','stop')])
        with patch('serving.curriculum.time.monotonic', side_effect=[0, 301, 301]):
            with self.assertRaises(ReviewInterrupted):
                gateway.ask(JobState(request=DraftRequest(**INPUT)), '검토', Findings, {}, [])
        stream.__exit__.assert_called_once()

    # 검증 목적: 반복 없는 긴 검토 결과를 임의로 잘라내지 않는지 확인
    def test_long_nonrepeating_review_is_not_truncated(self):
        report = json.dumps({'issues': [f'고유한 검토 항목 {i}' for i in range(500)]}, ensure_ascii=False)
        gateway, stream = self.review_gateway([self.chunk(report, 'stop')])
        result = gateway.ask(JobState(request=DraftRequest(**INPUT)), '검토', Findings, {}, [])
        self.assertEqual(len(result.issues), 500)

    # 검증 목적: API 실패 시 모델 잠금을 반환하고 내부 오류를 숨기는지 확인
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

    # 검증 목적: 유효하지 않은 요청과 동시 실행을 거부하는지 확인
    def test_api_invalid_and_busy(self):
        with TestClient(app) as http:
            self.assertEqual(http.post('/curriculum/step', json={'request': {**INPUT, 'topic': ' '}}).status_code, 422)
            BUSY.acquire()
            try:
                self.assertEqual(http.post('/curriculum/step', json={'request': INPUT}).status_code, 503)
            finally: BUSY.release()
