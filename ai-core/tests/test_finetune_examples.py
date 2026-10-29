## @file
# @brief 미세조정 학습 예시 형식(공백 없는 assistant JSON, 운영과 같은 user JSON)과 26B 호출 녹화 거르기, scope 요청 분리 전후 동작을 검증
# - 모델은 가짜 클라이언트로 대신함: 녹화 래퍼가 실제 게이트웨이 호출 경로에 끼워졌을 때 무엇을 남기는지만 봄
import json
import threading
import unittest
from types import SimpleNamespace

from finetune.capture import RecordingClient, recorded_rows
from finetune.examples import chat_row, compact, judge_row, scope_row
from serving.concept_map import load_concept_map
from serving.harness_llm import HarnessGateway, scope_payload
from serving.stuck import LLMUnavailable
from serving.stuck_llm import PROMPTS

MAP = load_concept_map()
PASSAGE = '스택은 나중에 넣은 원소를 먼저 꺼내는 LIFO 구조이다. push와 pop 연산을 제공한다.'


def reply(content, finish='stop'):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))])


## @brief OpenAI 클라이언트 가짜 구현; 역할별 응답 목록을 차례로 돌려주고 마지막 응답은 계속 되풀이함
# - 응답이 (내용, finish_reason) 튜플이면 잘린 응답 같은 비정상 종료를 흉내 냄
class FakeClient:
    def __init__(self, **responses):
        self.responses = {role: list(items) for role, items in responses.items()}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.models = SimpleNamespace(list=lambda timeout: SimpleNamespace(data=[SimpleNamespace(id='served-model')]))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def create(self, **kwargs):
        role = next(r for r, prompt in PROMPTS.items() if kwargs['messages'][0]['content'] == prompt)
        items = self.responses[role]
        item = items.pop(0) if len(items) > 1 else items[0]
        return reply(*item) if isinstance(item, tuple) else reply(item)


## @brief 가짜 클라이언트를 녹화 래퍼로 감싼 하네스 게이트웨이와 녹화 목록을 함께 만듦
def recording_gateway(**responses):
    sink = []
    fake = FakeClient(**responses)
    return HarnessGateway(MAP, threading.Lock(), client_factory=lambda: RecordingClient(fake, sink)), sink


class ExampleFormatTests(unittest.TestCase):
    # 검증 목적: 학습 정답이 운영 xgrammar 문법(공백 없는 한 줄 JSON)과 같은 모양인지 확인
    # - ': '·', ' 공백이나 한글 \u 이스케이프가 섞이면 미세조정 모델이 운영 문법과 다른 토큰열을 배우게 됨
    def test_assistant_content_is_compact_json(self):
        row = chat_row('judge', {'sentence': '스택은 LIFO 구조이다.', 'source': PASSAGE},
                       {'verdict': 'supported', 'evidence': '나중에 넣은 원소를 먼저 꺼내는 LIFO 구조'})
        content = row['messages'][2]['content']
        self.assertEqual([m['role'] for m in row['messages']], ['system', 'user', 'assistant'])
        self.assertEqual(row['messages'][0]['content'], PROMPTS['judge'])
        for bad in (': ', ', ', '\\u'):
            self.assertNotIn(bad, content)
        self.assertEqual(content, compact({'verdict': 'supported', 'evidence': '나중에 넣은 원소를 먼저 꺼내는 LIFO 구조'}))
        self.assertEqual(json.loads(content)['verdict'], 'supported')

    # 검증 목적: 구성으로 만든 judge·scope 학습 행의 지시문과 user JSON이 운영 게이트웨이가 실제로 보내는 것과 글자 그대로 같은지 확인
    # - 다르면 학습한 입력 모양과 운영 입력 모양이 어긋나 미세조정 효과가 운영에서 나오지 않음
    def test_user_content_matches_gateway(self):
        gw, sink = recording_gateway(scope=['{"concepts":["stack"],"follow_up":true}'],
                                     judge=['{"verdict":"supported","evidence":"LIFO 구조"}'])
        gw.scope('스택이 뭐야?', ['queue'], MAP)
        gw.judge('스택은 LIFO 구조이다.', PASSAGE)
        built = {'scope': scope_row('스택이 뭐야?', ['queue'], ['stack'], True, MAP),
                 'judge': judge_row('스택은 LIFO 구조이다.', PASSAGE, 'supported', 'LIFO 구조')}
        self.assertEqual([r['role'] for r in sink], ['scope', 'judge'])
        for record in sink:
            with self.subTest(role=record['role']):
                row = built[record['role']]
                self.assertEqual(record['messages'][0]['content'], row['messages'][0]['content'])
                self.assertEqual(record['messages'][1]['content'], row['messages'][1]['content'])
        self.assertEqual(scope_row('리눅스 설치법', [], [], False, MAP)['messages'][2]['content'],
                         '{"concepts":["none"],"follow_up":false}')

    # 검증 목적: 형식 오류·잘린 응답·검사 탈락 응답이 학습 행이 되지 않고, 재시도 끝에 성공한 마지막 응답만 남는지 확인
    # - 실패 응답을 배우면 미세조정 모델이 운영에서 걸러야 할 출력(':' 한 글자, 중간에 끊긴 JSON)을 따라 하게 됨
    def test_failed_or_truncated_calls_are_not_recorded(self):
        gw, sink = recording_gateway(judge=[':', '{"verdict": "partial", "evidence": "push와 pop"}'])
        self.assertEqual(gw.judge('스택은 push와 peek만 제공한다.', PASSAGE), ('partial', 'push와 pop'))
        rows = recorded_rows(sink, keep=lambda record: True)
        self.assertEqual(len(sink), 2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['messages'][2]['content'], '{"verdict":"partial","evidence":"push와 pop"}')
        self.assertEqual(rows[0]['messages'][1]['content'], sink[1]['messages'][1]['content'])

        gw, sink = recording_gateway(judge=[('{"verdict":"supported","evid', 'length')])
        with self.assertRaises(LLMUnavailable):
            gw.judge('스택은 LIFO 구조이다.', PASSAGE)
        self.assertTrue(sink)
        self.assertEqual(recorded_rows(sink, keep=lambda record: True), [])

        gw, sink = recording_gateway(judge=['{"verdict":"supported","evidence":"LIFO"}'])
        gw.judge('스택은 LIFO 구조이다.', PASSAGE)
        self.assertEqual(recorded_rows(sink, keep=lambda record: False), [])


class ScopePayloadTests(unittest.TestCase):
    # 검증 목적: scope user 구성을 scope_payload로 뽑아낸 뒤에도 운영 HarnessGateway.scope가 보내는 user JSON이 분리 전과 같은지 확인
    # - expected는 분리 전 scope 메서드 안의 구성식을 그대로 옮긴 것: 운영 범위 결정 입력이 바뀌지 않았음을 보임
    def test_scope_payload_refactor_keeps_behavior(self):
        title = {c.id: c.title for c in MAP.concepts}
        expected = {'concepts': [{'id': c.id, 'title': c.title, 'description': c.description} for c in MAP.concepts],
                    'previous': [{'id': p, 'title': title.get(p, p)} for p in ['stack', 'no-such-concept']],
                    'question': '더 쉽게 설명해 줘'}
        gw, sink = recording_gateway(scope=['{"concepts":["stack"],"follow_up":true}'])
        self.assertEqual(gw.scope('더 쉽게 설명해 줘', ['stack', 'no-such-concept'], MAP), (['stack'], True))
        self.assertEqual(sink[0]['messages'][1]['content'], json.dumps(expected, ensure_ascii=False))
        self.assertEqual(scope_payload('더 쉽게 설명해 줘', ['stack', 'no-such-concept'], MAP, gw._title), expected)


if __name__ == '__main__':
    unittest.main()
