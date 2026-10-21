# 파일 역할: 모델 서버 없이 LangGraph 라우팅·요청 격리·체크포인트 복구 경계 검증
"""Graph routing and unchanged request/checkpoint boundaries (no model server)."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from serving.api import app
from serving.curriculum import (
    _GRAPH, _StepContext, DraftRequest, JobState, OutlinePage, UnitPlan,
    Findings, FinalReview, step,
)
from test_curriculum import INPUT, PLAN, LESSONS, DRAFT, FakeGateway, unit


# 역할: 단계 라우팅과 기존 API·저장 계약의 유지 여부 검증
class CurriculumGraphTests(unittest.TestCase):
    # 역할: 각 단계가 노드 하나와 모델 호출 하나만 실행하는지 검증
    def test_each_stage_routes_to_one_node_and_one_model_call(self):
        for stage, node, answer, next_stage in [
            ('outline', 'outline', OutlinePage(unit=PLAN, done=True), 'generate'),
            ('generate', 'generate', DRAFT, 'review'),
            ('review', 'review', Findings(issues=[]), 'final'),
            ('final', 'final_review', FinalReview(issues=[]), 'complete'),
            ('repair', 'repair', PLAN, 'generate'),
        ]:
            with self.subTest(stage=stage):
                u = unit()
                if stage in ('review', 'final', 'repair'):
                    u.lessons = LESSONS.copy()
                if stage in ('final', 'repair'):
                    u.reviewed = True
                if stage == 'repair':
                    u.feedback = ['용어 보완']
                job = JobState(request=DraftRequest(**INPUT), stage=stage,
                               units=[] if stage == 'outline' else [u])
                gateway = FakeGateway(answer)
                updates = list(_GRAPH.stream(
                    {'job': job}, context=_StepContext(gateway), stream_mode='updates'))
                self.assertEqual([list(update) for update in updates], [[node]])
                self.assertEqual(len(gateway.calls), 1)
                self.assertEqual(updates[0][node]['job'].stage, next_stage)

    # 역할: 종료·한도 상태에서 모델 호출과 추가 단계 실행 방지 확인
    def test_terminal_and_exhausted_jobs_do_not_call_model_or_add_steps(self):
        for stage in ('complete', 'blocked'):
            with self.subTest(stage=stage):
                job = JobState(request=DraftRequest(**INPUT), stage=stage, steps=5)
                gateway = FakeGateway()
                updates = list(_GRAPH.stream(
                    {'job': job}, context=_StepContext(gateway), stream_mode='updates'))
                self.assertEqual(updates, [])
                self.assertEqual(step(job, gateway).model_dump(), job.model_dump())
                self.assertEqual(gateway.calls, [])
        job = JobState(request=DraftRequest(**INPUT), steps=100)
        gateway = FakeGateway()
        with patch('serving.curriculum.MAX_STEPS', 100):
            result = step(job, gateway)
        self.assertEqual(result.stage, 'blocked')
        self.assertEqual(result.steps, 100)
        self.assertEqual(gateway.calls, [])

    # 역할: 노드 실패 시 자동 중복 실행 없이 부분 변경을 되돌리는지 확인
    def test_failed_node_runs_once_and_rolls_back_partial_mutations(self):
        job = JobState(request=DraftRequest(**INPUT), stage='generate', units=[unit()])
        original = job.model_dump()
        # 역할: 실행 도중 상태를 변경한 뒤 오류를 발생시켜 복구 동작 재현
        class FailingGateway:
            calls = 0
            # 역할: 피드백·사용량 변경 후 예외 발생; 완료 결과와 관측 정보의 보존 범위 검증용
            def ask(self, state, *args):
                self.calls += 1
                state.units[0].feedback.append('저장되면 안 되는 변경')
                state.usage = {'input_tokens': 123}
                raise ValueError('invalid response')
        gateway = FailingGateway()
        with self.assertLogs(level='ERROR'):
            result = step(job, gateway)
        self.assertEqual(gateway.calls, 1)
        self.assertEqual(result.units[0].model_dump(), original['units'][0])
        self.assertEqual(result.usage, {'input_tokens': 123})
        self.assertEqual((result.steps, result.retries), (1, 1))
        self.assertEqual(job.model_dump(), original)

    # 역할: 검토 지적이 없어도 미완성 단원은 전체 완료로 처리하지 않는지 확인
    def test_incomplete_unit_cannot_complete_after_clean_final_review(self):
        u = unit()
        u.lessons = LESSONS[:1]
        u.reviewed = True
        job = JobState(request=DraftRequest(**INPUT), stage='final', units=[u])
        with self.assertLogs(level='ERROR'):
            result = step(job, FakeGateway(FinalReview(issues=[])))
        self.assertEqual(result.stage, 'final')
        self.assertEqual(result.retries, 1)
        self.assertEqual(result.units, job.units)

    # 역할: 공유 그래프에서도 요청별 작업과 모델 게이트웨이가 분리되는지 검증
    def test_shared_graph_does_not_share_job_or_gateway_between_requests(self):
        # 역할: 서로 다른 주제와 게이트웨이로 생성 요청을 구성해 동시 실행 격리 확인
        def run(index):
            request = DraftRequest(**{**INPUT, 'topic': f'주제 {index}'})
            plan = UnitPlan(title=f'단원 {index}', scope='범위', topics=['개념'])
            job = JobState(request=request)
            gateway = FakeGateway(OutlinePage(unit=plan, done=True))
            result = step(job, gateway)
            self.assertEqual(job.units, [])
            self.assertEqual(len(gateway.calls), 1)
            return result
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = list(pool.map(run, range(2)))
        for index, job in enumerate(jobs):
            self.assertEqual(job.request.topic, f'주제 {index}')
            self.assertEqual(job.units[0].plan.title, f'단원 {index}')
            self.assertEqual(job.steps, 1)

    # 역할: 기존 JSON 상태로 API를 재개하고 그래프 내부 필드가 저장 계약에 섞이지 않는지 검증
    @patch('serving.api.OpenAI')
    @patch('serving.api.ModelGateway')
    def test_existing_api_resumes_json_checkpoint_without_graph_fields(self, gateway_type, client):
        model = SimpleNamespace(id='test', model_extra={'max_model_len': 32768})
        configured = client.return_value.__enter__.return_value
        configured.models.list.return_value.data = [model]
        configured.base_url = 'http://127.0.0.1:8000/v1/'
        configured.api_key = 'test'
        gateway = FakeGateway(OutlinePage(unit=PLAN, done=True), DRAFT,
                              Findings(issues=[]), FinalReview(issues=[]))
        gateway_type.return_value = gateway
        job = JobState(request=DraftRequest(**INPUT))
        fields = set(job.model_dump())
        with TestClient(app) as http:
            for index in range(4):
                response = http.post('/curriculum/step', json=job.model_dump())
                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual(set(body), fields)
                self.assertEqual(body['request'], INPUT)
                self.assertEqual(body['steps'], index + 1)
                self.assertEqual(len(gateway.calls), index + 1)
                job = JobState.model_validate_json(response.text)
                self.assertEqual(job.model_dump(), body)
        self.assertEqual(job.stage, 'complete')
        self.assertEqual(job.units[0].lessons, LESSONS)
