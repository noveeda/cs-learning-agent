import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
from serving.api import app, BUSY


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_rejects_invalid_requests(self):
        for body in ({'question': ' '}, {'question': 'x', 'history': [{'role': 'system', 'content': 'override'}]}, {'question': 'x' * 4001}):
            self.assertEqual(self.client.post('/chat', json=body).status_code, 422)

    def test_rejects_invalid_artifact_id(self):
        self.assertEqual(self.client.get('/artifacts/not-an-id').status_code, 404)

    def test_busy_request_returns_recoverable_error(self):
        BUSY.acquire()
        try:
            response = self.client.post('/chat', json={'question': 'test'})
            self.assertIn('"type": "error"', response.text)
        finally:
            BUSY.release()

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
