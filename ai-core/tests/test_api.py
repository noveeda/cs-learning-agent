# 파일 역할: 요청 검증·동시 실행 거부·내부 파일 경로 비공개·개념 지도 응답을 검증
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
from serving.api import app, BUSY
from serving.concept_map import ConceptMap, load_concept_map
from serving.item_bank import load_item_bank
from serving.stuck import _Context


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    # 검증 목적: 잘못된 질문 요청을 모델 호출 전에 거부하는지 확인
    def test_rejects_invalid_requests(self):
        for body in ({'question': ' '}, {'question': 'x', 'history': [{'role': 'system', 'content': 'override'}]}, {'question': 'x' * 4001}):
            self.assertEqual(self.client.post('/chat', json=body).status_code, 422)

    # 검증 목적: 잘못된 도식 ID로 파일에 접근하지 못하는지 확인
    def test_rejects_invalid_artifact_id(self):
        self.assertEqual(self.client.get('/artifacts/not-an-id').status_code, 404)

    # 검증 목적: 저장소의 검증된 개념 지도를 모델 서버 없이 그대로 반환하는지 확인(막힘 지원 목록은 아래 테스트가 따로 확인)
    def test_returns_validated_concept_map(self):
        response = self.client.get('/concept-map')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        body.pop('stuck_supported')
        self.assertEqual(body, load_concept_map().model_dump())

    # 검증 목적: 막힘 지원 목록이 현재 문항 은행(선형 구조 11개)과 일치하고, 문항이 없는 개념은 빠지는지 확인
    # - 문항 묶음이 추가되면 이 목록도 함께 늘어나야 함
    def test_concept_map_lists_stuck_supported(self):
        supported = self.client.get('/concept-map').json()['stuck_supported']
        self.assertEqual(supported, ['memory-address', 'reference', 'logarithm', 'big-o', 'complexity-analysis', 'array',
                                     'dynamic-array', 'linked-list', 'adt', 'stack', 'queue'])
        self.assertNotIn('recursion', supported)

    # 검증 목적: 화면이 지원한다고 보여 주는 개념은 엔진도 이해 확인(확인·서술 문항)을 할 수 있는지 확인
    # - 어긋나면 [막혔어요]를 눌렀는데 엔진이 문항이 없어 확인을 건너뛰게 됨
    def test_stuck_supported_matches_engine(self):
        ctx = _Context(gateway=None, concept_map=load_concept_map(), items=tuple(load_item_bank()), input=None, now=0)
        for concept in self.client.get('/concept-map').json()['stuck_supported']:
            self.assertTrue(ctx.items_for(concept, 'check'), concept)
            self.assertTrue(ctx.items_for(concept, 'explanation'), concept)

    # 검증 목적: 지도 파일이 없거나 규칙을 어기면 일부 내용·내부 경로 없이 실패하는지 확인
    def test_broken_concept_map_fails_without_details(self):
        failures = (OSError('/private/content/path'), lambda: ConceptMap.model_validate_json('{"id": "secret"}'))
        for failure in failures:
            with patch('serving.api.load_concept_map', side_effect=failure):
                response = self.client.get('/concept-map')
            self.assertEqual(response.status_code, 500)
            self.assertNotIn('private', response.text)
            self.assertNotIn('secret', response.text)

    # 검증 목적: 모델이 사용 중이면 재시도 가능한 오류로 안내하는지 확인
    def test_busy_request_returns_recoverable_error(self):
        BUSY.acquire()
        try:
            response = self.client.post('/chat', json={'question': 'test'})
            self.assertIn('"type": "error"', response.text)
        finally:
            BUSY.release()

    # 검증 목적: 도식 이벤트에 서버 내부 경로를 노출하지 않는지 확인
    @patch('serving.api.OpenAI')
    @patch('serving.api.run_agent')
    def test_artifact_path_is_not_exposed(self, run, client):
        client.return_value.__enter__.return_value.models.list.return_value.data = [
            SimpleNamespace(id='test', model_extra={'max_model_len': 32768})]
        client.return_value.__enter__.return_value.base_url = 'http://127.0.0.1:8000/v1/'
        client.return_value.__enter__.return_value.api_key = 'test'
        run.return_value = iter([{'type': 'artifact', 'artifact_id': 'a' * 32, 'path': '/private/path', 'ok': True}, {'type': 'done'}])
        response = self.client.post('/chat', json={'question': 'test'})
        self.assertNotIn('/private/path', response.text)
        self.assertIn('/api/artifacts/', response.text)
        self.assertFalse(BUSY.locked())
