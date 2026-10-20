import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
from pydantic import ValidationError
from serving.api import app, BUSY
from serving.curriculum import DraftRequest, CurriculumDraft, generate_draft, MAX_OUTPUT_TOKENS

INPUT = {'topic': '페이징', 'goal': '취업 준비', 'level': '용어만 알아요'}
DRAFT = {'title': '페이징 학습', 'lessons': [
    {'title': '페이지와 프레임', 'objectives': ['페이지와 프레임을 구분한다.']},
    {'title': '주소 변환', 'objectives': ['주소 변환 과정을 설명한다.']},
]}


class CurriculumTests(unittest.TestCase):
    def test_input_bounds(self):
        for value in ['', '  ', 'x' * 301]:
            with self.assertRaises(ValidationError):
                DraftRequest(**{**INPUT, 'topic': value})

    def test_counts_follow_model_output_not_fixed_ranges(self):
        for count in (1, 7):
            lessons = [{'title': f'소단원 {i}', 'objectives': [f'학습 목표 {j}' for j in range(4)]}
                       for i in range(count)]
            draft = CurriculumDraft.model_validate({'title': '학습', 'lessons': lessons})
            self.assertEqual(len(draft.lessons), count)
            self.assertEqual(len(draft.lessons[0].objectives), 4)
        schema = CurriculumDraft.model_json_schema()
        self.assertNotIn('maxItems', schema['properties']['lessons'])
        self.assertNotIn('maxItems', schema['$defs']['Lesson']['properties']['objectives'])

    def test_bad_output_rejected(self):
        for lessons in [[], [DRAFT['lessons'][0]] * 2,
                        [{'title': 'A', 'objectives': []}, DRAFT['lessons'][1]]]:
            with self.assertRaises(ValidationError):
                CurriculumDraft.model_validate({**DRAFT, 'lessons': lessons})

    def test_generator_validates_model_output_and_truncation(self):
        client = Mock()
        choice = SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=json.dumps(DRAFT)))
        client.chat.completions.create.return_value = SimpleNamespace(choices=[choice])
        self.assertEqual(generate_draft(client, 'model', DraftRequest(**INPUT)).title, DRAFT['title'])
        payload = client.chat.completions.create.call_args.kwargs
        self.assertEqual(json.loads(payload['messages'][1]['content']), INPUT)
        self.assertEqual(payload['max_tokens'], MAX_OUTPUT_TOKENS)
        choice.finish_reason = 'length'
        with self.assertRaises(ValueError):
            generate_draft(client, 'model', DraftRequest(**INPUT))
        choice.finish_reason = 'stop'
        choice.message.content = 'not JSON'
        with self.assertRaises(ValueError):
            generate_draft(client, 'model', DraftRequest(**INPUT))

    @patch('serving.api.OpenAI')
    @patch('serving.api.generate_draft')
    def test_api_failure_releases_lock_and_retry_succeeds(self, generate, client):
        generate.side_effect = [ValueError('private model detail'), CurriculumDraft(**DRAFT)]
        with TestClient(app) as http:
            response = http.post('/curriculum/draft', json=INPUT)
            self.assertEqual(response.status_code, 502)
            self.assertNotIn('private model detail', response.text)
            self.assertFalse(BUSY.locked())
            self.assertEqual(http.post('/curriculum/draft', json=INPUT).json(), DRAFT)
            self.assertFalse(BUSY.locked())

    def test_api_invalid_and_busy(self):
        with TestClient(app) as http:
            self.assertEqual(http.post('/curriculum/draft', json={**INPUT, 'topic': ' '}).status_code, 422)
            BUSY.acquire()
            try:
                self.assertEqual(http.post('/curriculum/draft', json=INPUT).status_code, 503)
            finally:
                BUSY.release()
