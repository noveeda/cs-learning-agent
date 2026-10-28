## @file
# @brief 하네스 검증 규칙(문장 형식·한자·근거 구절 대조)과 범위·생성·판정·수정 역할의 요청 형태·출력 검증·재시도를 검증
# - 모델은 가짜 클라이언트로 대신하고, 실제 26B 호출 예시는 PR 본문에 따로 남김
import json
import threading
import unittest
from types import SimpleNamespace

from serving.concept_map import load_concept_map
from serving.harness import Sentence, evidence_in, grading_evidence_in, has_han, validate_sentences
from serving.harness_llm import HarnessGateway
from serving.stuck import LLMUnavailable
from serving.stuck_llm import PROMPTS, ROLE_SETTINGS

MAP = load_concept_map()
KEYS = {'p1', 'p2', 'p3'}


def reply(content):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=content))])


## @brief OpenAI 클라이언트 가짜 구현; 요청을 기록하고 시스템 지시로 역할을 알아내 역할별 응답 함수에 넘김
class FakeClient:
    def __init__(self, **responders):
        self.requests, self.responders = [], responders
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.models = SimpleNamespace(list=lambda timeout: SimpleNamespace(data=[SimpleNamespace(id='served-model')]))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def create(self, **kwargs):
        role = next(r for r, prompt in PROMPTS.items() if kwargs['messages'][0]['content'] == prompt)
        self.requests.append((role, kwargs))
        return reply(self.responders[role](json.loads(kwargs['messages'][1]['content'])))


def gateway(client):
    return HarnessGateway(MAP, threading.Lock(), client_factory=lambda: client)


def sentences(*items):
    return {'sentences': [{'text': text, 'sources': list(sources)} for text, sources in items]}


class ValidateSentencesTests(unittest.TestCase):
    # 검증 목적: 근거 번호가 없거나 검색되지 않은 문단을 가리키는 문장이 근거 있는 문장처럼 통과하지 않는지 확인
    def test_validate_rejects_missing_or_unknown_source(self):
        self.assertEqual(validate_sentences(sentences(('스택은 나중에 넣은 값을 먼저 꺼낸다.', ['p1', 'p2'])), KEYS),
                         [('스택은 나중에 넣은 값을 먼저 꺼낸다.', ('p1', 'p2'))])
        for bad in (sentences(('근거 없음', [])), sentences(('없는 문단', ['p4'])), {'sentences': [{'text': '키 없음'}]},
                    {'sentences': []}, {}, [], sentences(('', ['p1']))):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_sentences(bad, KEYS)

    # 검증 목적: 작은 모델이 한국어 문장에 중국어 한자를 섞는 오류(예: "利用了")를 형식 오류로 걸러 재시도하게 하는지 확인
    def test_validate_rejects_han_characters(self):
        with self.assertRaises(ValueError):
            validate_sentences(sentences(('스택을 利用了 값을 저장한다.', ['p1'])), KEYS)
        self.assertTrue(has_han('利用了'))
        self.assertFalse(has_han('스택은 LIFO 구조다.'))
        # 호환 한자(U+F900)는 걸리고, 한글 음절 끝 글자(U+D7A3)는 걸리지 않아야 함: 범위 경계가 정규화로 바뀌는 회귀 방지
        self.assertTrue(has_han('\uf900'))
        self.assertFalse(has_han('\ud7a3'))

    # 검증 목적: 한 답의 분량 상한(문장 8개, 문장당 200자)을 넘는 출력이 그대로 화면에 나가지 않는지 확인
    def test_validate_rejects_over_8_sentences_and_over_200_chars(self):
        self.assertEqual(len(validate_sentences(sentences(*[(f'{i}번 문장은 통과한다.', ['p1']) for i in range(8)]), KEYS)), 8)
        with self.assertRaises(ValueError):
            validate_sentences(sentences(*[(f'{i}번 문장은 통과한다.', ['p1']) for i in range(9)]), KEYS)
        validate_sentences(sentences(('가' * 200, ['p1'])), KEYS)
        with self.assertRaises(ValueError):
            validate_sentences(sentences(('가' * 201, ['p1'])), KEYS)

    # 검증 목적: 모델이 본문에 직접 쓴 근거 키 표기('(p3)', '[p1]', 단독 'p2')가 화면 각주([^p3])와 겹치지 않게 지워지는지 확인
    # - 실측(Task 7)에서 26B가 문장 끝에 '(p3)'를 써 각주와 중복됨. 지운 결과가 이후 판정·표시에 쓰이는 문장임
    # - 'p3에 따르면'처럼 낱말에 붙은 것과 근거 키가 아닌 표기('(p9)')는 건드리지 않음
    def test_validate_strips_source_key_markers(self):
        cases = [('스택은 마지막 원소를 먼저 꺼낸다(p3).', '스택은 마지막 원소를 먼저 꺼낸다.'),
                 ('스택은 마지막 원소를 먼저 꺼낸다 [p1].', '스택은 마지막 원소를 먼저 꺼낸다.'),
                 ('스택은 p2 마지막 원소를 먼저 꺼낸다.', '스택은 마지막 원소를 먼저 꺼낸다.'),
                 ('스택은 마지막 원소를 먼저 꺼낸다 (p1, p2).', '스택은 마지막 원소를 먼저 꺼낸다.'),
                 ('스택은 마지막 원소를 먼저 꺼낸다[^p1][p2].', '스택은 마지막 원소를 먼저 꺼낸다.'),
                 ('p3에 따르면 스택은 LIFO다 (p9).', 'p3에 따르면 스택은 LIFO다 (p9).')]
        for raw, cleaned in cases:
            with self.subTest(raw=raw):
                self.assertEqual(validate_sentences(sentences((raw, ['p1'])), KEYS), [(cleaned, ('p1',))])

    # 검증 목적: 근거 키 표기를 지우고 나면 내용이 없는 문장, 문장부호뿐인 문장(실측의 ':'), 너무 짧은 문장을 거부해 재시도하게 함
    # - 기준: 한글·영문 글자가 하나 이상이고, 한글·영문·숫자가 모두 5자 이상
    def test_validate_rejects_empty_or_punctuation_only_sentences(self):
        for bad in (':', '(p3).', '...', '- ', '1 2 3', '큐다.', 'O(1)'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_sentences(sentences(('스택은 마지막 원소를 먼저 꺼낸다.', ['p1']), (bad, ['p1'])), KEYS)
        self.assertEqual(validate_sentences(sentences(('삽입은 O(1)이다.', ['p1'])), KEYS), [('삽입은 O(1)이다.', ('p1',))])

    # 검증 목적: 검증을 통과한 문장의 자료형이 다음 단계(판정)에서 쓸 상태 값을 담는지 확인
    def test_sentence_holds_status(self):
        sentence = Sentence('스택은 LIFO다.', ('p1',), 'verified')
        self.assertEqual((sentence.text, sentence.sources, sentence.status), ('스택은 LIFO다.', ('p1',), 'verified'))


class EvidenceTests(unittest.TestCase):
    PASSAGE = 'A stack is a collection\nof elements that\n  supports push and pop operations.'

    # 검증 목적: 판정 근거 구절이 문단과 줄바꿈·연속 공백만 다를 때 거짓 실패하지 않는지 확인
    def test_evidence_whitespace_normalized(self):
        self.assertTrue(evidence_in('collection of elements that supports push', self.PASSAGE))
        self.assertTrue(evidence_in('  supports push\nand   pop ', self.PASSAGE))

    # 검증 목적: 한 단어라도 바뀐 구절은 문단에 없는 근거로 보고 거짓을 돌려주는지 확인
    def test_evidence_altered_word_fails(self):
        self.assertFalse(evidence_in('supports push and peek operations', self.PASSAGE))
        self.assertFalse(evidence_in('', self.PASSAGE))
        self.assertFalse(evidence_in(' \n ', self.PASSAGE))

    # 검증 목적: 서술 채점 근거는 공백·문장부호 차이를 무시하고 학생 답에 있는지 대조하는지 확인
    def test_grading_evidence_ignores_spaces_and_punctuation(self):
        self.assertTrue(grading_evidence_in('스택은 LIFO다.', '스택은LIFO 다'))
        self.assertTrue(grading_evidence_in('「먼저」 넣은…', '가장 먼저 넣은 값'))
        self.assertFalse(grading_evidence_in('스택은 FIFO다', '스택은LIFO 다'))

    # 검증 목적: 빈 근거(공백·문장부호만 있는 경우 포함)를 충족 근거로 인정하지 않는지 확인
    def test_grading_evidence_empty_is_false(self):
        self.assertFalse(grading_evidence_in('', '스택은 LIFO다'))
        self.assertFalse(grading_evidence_in(' .… ', '스택은 LIFO다'))


class HarnessGatewayTests(unittest.TestCase):
    # 검증 목적: 범위 결정이 지도 61개 ID와 "none"만 고르게 schema를 걸고, 역할 설정(temperature 0, 128토큰)을 보내는지 확인
    def test_scope_schema_enum_matches_concept_map(self):
        client = FakeClient(scope=lambda user: json.dumps({'concepts': ['stack', 'queue'], 'follow_up': False}))
        self.assertEqual(gateway(client).scope('스택과 큐 차이가 뭐예요?', [], MAP), (['stack', 'queue'], False))
        role, kw = client.requests[0]
        schema = kw['response_format']['json_schema']['schema']
        self.assertEqual(schema['properties']['concepts']['items']['enum'], [c.id for c in MAP.concepts] + ['none'])
        self.assertEqual((schema['properties']['concepts']['minItems'], schema['properties']['concepts']['maxItems']), (1, 2))
        self.assertEqual((kw['temperature'], kw['max_tokens']), (0.0, 128))

    # 검증 목적: "none"은 빈 목록(차단)으로 바꾸고, 직전 개념을 이어받는 후속 질문 표시는 그대로 전달하는지 확인
    def test_scope_none_and_follow_up(self):
        client = FakeClient(scope=lambda user: json.dumps({'concepts': ['none'], 'follow_up': False}))
        self.assertEqual(gateway(client).scope('이 코드 버그 찾아 줘', [], MAP), ([], False))
        client = FakeClient(scope=lambda user: json.dumps({'concepts': ['stack'], 'follow_up': True}))
        self.assertEqual(gateway(client).scope('더 쉽게 설명해 줘', ['stack'], MAP), (['stack'], True))
        self.assertEqual(client.requests[0][1]['messages'][1]['content'].count('"previous"'), 1)

    # 검증 목적: 생성 출력이 두 번 모두 형식 규칙을 어기면 재시도 횟수(2)만큼 부른 뒤 LLMUnavailable로 끝나는지 확인
    def test_ground_retries_on_invalid_then_raises(self):
        client = FakeClient(ground=lambda user: json.dumps(sentences(('없는 문단을 가리킨다.', ['p9']))))
        with self.assertRaises(LLMUnavailable):
            gateway(client).ground('스택이 뭐예요?', {'p1': 'A stack is LIFO.'}, ['스택'], [])
        self.assertEqual(len(client.requests), ROLE_SETTINGS['ground'].attempts)
        self.assertEqual(ROLE_SETTINGS['ground'].attempts, 2)

    # 검증 목적: 생성 schema의 근거 번호를 실제 검색된 근거 키로 묶고, 검증을 통과한 문장 목록을 돌려주는지 확인
    def test_ground_returns_validated_sentences(self):
        client = FakeClient(ground=lambda user: json.dumps(sentences(('스택은 나중에 넣은 값을 먼저 꺼낸다.', ['p1']))))
        result = gateway(client).ground('스택이 뭐예요?', {'p1': 'A stack is LIFO.', 'a1': 'push 1'}, ['스택'], ['이전 문장'])
        self.assertEqual(result, [('스택은 나중에 넣은 값을 먼저 꺼낸다.', ('p1',))])
        role, kw = client.requests[0]
        items = kw['response_format']['json_schema']['schema']['properties']['sentences']['items']
        self.assertEqual(sorted(items['properties']['sources']['items']['enum']), ['a1', 'p1'])
        self.assertEqual((kw['temperature'], kw['max_tokens']), (0.3, 1024))

    # 검증 목적: 판정은 세 값 중 하나와 근거 구절을 돌려주고, 판정·수정 역할 설정이 계획대로인지 확인
    def test_judge_and_repair(self):
        client = FakeClient(judge=lambda user: json.dumps({'verdict': 'partial', 'evidence': 'A stack is LIFO.'}),
                            repair=lambda user: json.dumps({'text': '스택은 LIFO 구조다.', 'sources': ['p2']}))
        gw = gateway(client)
        self.assertEqual(gw.judge('스택은 FIFO다.', 'A stack is LIFO.'), ('partial', 'A stack is LIFO.'))
        self.assertEqual(gw.repair('스택은 FIFO다.', 'A stack is LIFO.', 'p2'), ('스택은 LIFO 구조다.', ('p2',)))
        (_, judge_kw), (_, repair_kw) = client.requests
        self.assertEqual((judge_kw['temperature'], judge_kw['max_tokens']), (0.0, 256))
        self.assertEqual((repair_kw['temperature'], repair_kw['max_tokens']), (0.3, 256))
        for role in ('scope', 'ground', 'judge', 'repair'):
            self.assertEqual((ROLE_SETTINGS[role].timeout, ROLE_SETTINGS[role].attempts), (30, 2), role)


if __name__ == '__main__':
    unittest.main()
