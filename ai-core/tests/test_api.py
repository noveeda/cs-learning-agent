# 파일 역할: 요청 검증·동시 실행 거부·내부 파일 경로 비공개·개념 지도 응답을 검증
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
from serving.api import app, BUSY
from serving.concept_map import ConceptMap, load_concept_map


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

    # 검증 목적: 저장소의 검증된 개념 지도를 모델 서버 없이 그대로 반환하는지 확인
    def test_returns_validated_concept_map(self):
        response = self.client.get('/concept-map')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), load_concept_map().model_dump())

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
