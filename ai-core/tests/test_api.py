# 파일 역할: 요청 검증·동시 실행 거부·내부 파일 경로 비공개·애니메이션 artifact 왕복·개념 지도 응답을 검증
import json
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

    # 역할: 실제 모델 서버 없이 /chat이 run_agent까지 가도록 OpenAI 클라이언트의 모델 목록·주소를 채움
    def fake_model(self, client):
        client.return_value.__enter__.return_value.models.list.return_value.data = [
            SimpleNamespace(id='test', model_extra={'max_model_len': 32768})]
        client.return_value.__enter__.return_value.base_url = 'http://127.0.0.1:8000/v1/'
        client.return_value.__enter__.return_value.api_key = 'test'

    # 검증 목적: Spring이 저장한 애니메이션 artifact 이벤트가 이력·활성 artifact로 돌아와도 422가 나지 않고,
    # 에이전트에는 프레임 없이 장면 요청만 전달되는지 확인(애니메이션 뒤 대화가 끊기는 회귀 방지)
    @patch('serving.api.OpenAI')
    @patch('serving.api.run_agent')
    def test_chat_accepts_animation_artifact_from_spring(self, run, client):
        self.fake_model(client)
        run.return_value = iter([{'type': 'done'}])
        request = {'kind': 'stack', 'ops': [{'op': 'push', 'value': 1}]}
        stored = {'type': 'artifact', 'ok': True, 'artifact_id': 'b' * 32, 'kind': 'animation',
                  'animation': {'kind': 'stack', 'title': '스택', 'frames': []}, 'request': request}
        diagram = {'type': 'artifact', 'ok': True, 'artifact_id': 'c' * 32, 'url': '/api/artifacts/' + 'c' * 32,
                   'diagram': {'title': 't', 'nodes': [{'id': 'a', 'label': 'A', 'column': 0, 'row': 0}], 'edges': []}}
        response = self.client.post('/chat', json={
            'question': '바꿔줘', 'active_artifact': stored,
            'history': [{'role': 'user', 'content': '스택'},
                        {'role': 'assistant', 'content': '완료', 'artifacts': [stored, diagram]}]})
        self.assertEqual(response.status_code, 200)
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs['active_artifact'], {'artifact_id': 'b' * 32, 'kind': 'animation', 'request': request})
        self.assertEqual([sorted(a) for a in kwargs['history'][1]['artifacts']],
                         [['artifact_id', 'kind', 'request'], ['artifact_id', 'diagram']])

    # 검증 목적: 애니메이션 artifact 이벤트에는 존재하지 않는 SVG 주소를 붙이지 않는지 확인
    @patch('serving.api.OpenAI')
    @patch('serving.api.run_agent')
    def test_animation_artifact_has_no_svg_url(self, run, client):
        self.fake_model(client)
        run.return_value = iter([{'type': 'artifact', 'ok': True, 'artifact_id': 'b' * 32, 'kind': 'animation',
                                  'animation': {'frames': []}, 'request': {}}, {'type': 'done'}])
        response = self.client.post('/chat', json={'question': 'test'})
        self.assertIn('"kind": "animation"', response.text)
        self.assertNotIn('/api/artifacts/', response.text)

    # 검증 목적: 도구 오류 안내가 실패한 도구에 맞는 문구로 나가고, 내부 오류 원문(검증 메시지)은 웹에 노출되지 않는지 확인
    # - 애니메이션 도구 실패에 "도식 입력"이라고 안내하던 문제 방지
    @patch('serving.api.OpenAI')
    @patch('serving.api.run_agent')
    def test_tool_error_message_matches_tool(self, run, client):
        self.fake_model(client)
        expected = {'create_diagram': '도식 입력을 수정하고 있습니다.', 'show_animation': '애니메이션 입력을 수정하고 있습니다.',
                    'run_shell': '도구 입력을 수정하고 있습니다.'}
        for name, message in expected.items():
            with self.subTest(name=name):
                run.return_value = iter([{'type': 'tool_error', 'name': name, 'error': 'pop: 스택이 비어 internal'},
                                         {'type': 'done'}])
                events = [json.loads(line) for line in self.client.post('/chat', json={'question': 'test'}).text.splitlines()]
                self.assertIn({'type': 'tool_error', 'error': message}, events)
                self.assertNotIn('internal', json.dumps(events, ensure_ascii=False))

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
