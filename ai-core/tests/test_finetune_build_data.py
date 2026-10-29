## @file
# @brief 미세조정 학습 데이터 생성기의 규칙을 검증: 판정 교란 비율, 한자·측정 질문 거르기, 제외 개념 몫 분리, 운영 parse 재검사, 역할별 검증용 분할
# - 26B는 부르지 않음: 교란·녹화 응답은 가짜로 만들고, 생성기가 그 결과를 어떻게 거르고 나누는지만 봄
import json
import unittest
from collections import Counter

from finetune.build_data import (holdout_terms, passes_gateway, refilter_rows, scope_rows, split_rows,
                                 training_judge_rows, verified_pool)
from finetune.capture import recorded_rows
from finetune.split import normalize_question
from serving.concept_map import load_concept_map
from serving.stuck_llm import PROMPTS

MAP = load_concept_map()
HOLDOUT = {'linked-list', 'bfs'}
TERMS = holdout_terms(MAP, HOLDOUT)


## @brief 녹화 한 건(RecordingClient 형식)을 만듦; 운영 게이트웨이와 같은 user 직렬화
def call(role, payload, content, finish='stop'):
    return {'role': role, 'messages': [{'role': 'system', 'content': PROMPTS[role]},
                                       {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
            'content': content, 'finish_reason': finish}


## @brief 하네스 실행 녹화 한 건(records.jsonl 행): 문장마다 상태·판정을 두고, 근거 문단은 학습 가능한 절로 둠
def harvest(qid, question, sentences, concepts=('stack',), section='s1'):
    return {'id': qid, 'kind': 'direct', 'question': question, 'expected': list(concepts),
            'record': {'outcome': 'answered', 'concepts': list(concepts),
                       'sentences': [{'draft': text, 'repairs': 0, 'text': text, 'sources': ['p1'], 'status': status,
                                      'judgments': [{'verdict': 'supported', 'evidence': '나중에 넣은', 'evidence_found': True}]}
                                     for text, status in sentences]},
            'passages': {'p1': {'id': f'{section}#0', 'book': 'ods', 'section_id': section,
                                'text': '스택은 나중에 넣은 원소를 먼저 꺼내는 구조이다.'}},
            'calls': [], 'audits': None}


class JudgeRowsTests(unittest.TestCase):
    # 검증 목적: 판정 학습 행이 맞음·틀림 반반이고 틀림 중 덧붙이기가 40%(±2%p)인지, 정답 규칙(덧붙이기는 partial, 나머지는 unsupported)과 근거 구절이 맞는지 확인
    # - 덧붙이기 비중을 측정 세트(약 20%)보다 높이는 이유: 12B가 가장 많이 놓치는 교란이 덧붙인 문장이기 때문(설계 1장)
    def test_addition_share(self):
        verified = [{'id': f'v{i}', 'sentence': f'스택은 {i}번째 원소를 나중에 꺼낸다.' if i % 3 == 0 else f'스택 연산 {chr(0xAC00 + i)}는 나중에 넣은 것을 먼저 꺼낸다',
                     'passage': '스택은 나중에 넣은 원소를 먼저 꺼낸다.', 'evidence': f'근거{i}'} for i in range(1000)]
        rows = training_judge_rows(verified, lambda kind, row: f"{row['sentence']} 그리고 {kind} 교란이다", seed=0)
        labels = Counter(json.loads(r['messages'][2]['content'])['verdict'] for r in rows)
        wrong = [r for r in rows if r['meta']['perturbation']]
        self.assertEqual(labels['supported'], len(wrong))
        share = sum(r['meta']['perturbation'] == 'addition' for r in wrong) / len(wrong)
        self.assertTrue(0.38 <= share <= 0.42, share)
        self.assertGreater(len(rows), 900)
        evidence = {v['id']: v['evidence'] for v in verified}
        for row in rows:
            out = json.loads(row['messages'][2]['content'])
            expected = {None: 'supported', 'addition': 'partial'}.get(row['meta']['perturbation'], 'unsupported')
            self.assertEqual(out['verdict'], expected)
            self.assertEqual(out['evidence'], evidence[row['meta']['source_id']])
        self.assertEqual(rows, training_judge_rows(verified, lambda kind, row: f"{row['sentence']} 그리고 {kind} 교란이다", seed=0))


class FilterTests(unittest.TestCase):
    # 검증 목적: 한자가 섞인 행은 버리고, 비교 질문의 한쪽·직전 개념·질문 문구·근거 절에 제외 개념이 든 행은 1차 행에 없이 제외 개념 몫으로만 가는지 확인
    # - 제외 개념이 1차 학습에 새면 "처음 보는 개념" 기준의 합격 판정(설계 5장)이 무의미해짐
    # - 제외 개념 몫은 버리지 않고 배포용 2차 학습(설계 7-1)에 씀
    def test_drops_han_and_holdout_compare(self):
        questions = [{'id': 'q1', 'kind': 'compare', 'question': '스택과 큐의 차이가 뭐야?', 'previous': [], 'expected': ['stack', 'queue']},
                     {'id': 'q2', 'kind': 'compare', 'question': '배열과 그 리스트의 차이는?', 'previous': [], 'expected': ['array', 'linked-list']},
                     {'id': 'q3', 'kind': 'direct', 'question': '스택을 利用하는 예는?', 'previous': [], 'expected': ['stack']},
                     {'id': 'q4', 'kind': 'direct', 'question': '스택이랑 연결리스트로 만든 스택 차이?', 'previous': [], 'expected': ['stack']},
                     {'id': 'q5', 'kind': 'follow_up', 'question': '더 쉽게 설명해 줘', 'previous': ['bfs'], 'expected': ['bfs']},
                     {'id': 'q6', 'kind': 'outside', 'question': '리눅스 설치 방법 알려 줘', 'previous': [], 'expected': []}]
        rows, held, reasons = scope_rows(questions, MAP, HOLDOUT, TERMS, measured=set())
        self.assertEqual([r['meta']['question_id'] for r in rows], ['q1', 'q6'])
        self.assertEqual([r['meta']['question_id'] for r in held], ['q2', 'q4', 'q5'])
        self.assertEqual(reasons, Counter({'han': 1}))

        records = [harvest('r1', '스택이 뭐야?', [('스택은 나중에 넣은 원소를 먼저 꺼낸다.', 'verified'),
                                                  ('스택은 后入先出 구조다.', 'verified'),
                                                  ('스택은 연결 리스트로도 만든다.', 'verified')]),
                   harvest('r2', '스택 구현은?', [('스택은 배열로 만든다.', 'verified')], section='holdout-section')]
        pool, held, reasons = verified_pool(records, HOLDOUT, TERMS, measured=set(), trainable={('ods', 's1')}, judged=set())
        self.assertEqual([v['sentence'] for v in pool], ['스택은 나중에 넣은 원소를 먼저 꺼낸다.'])
        self.assertEqual([v['sentence'] for v in held], ['스택은 연결 리스트로도 만든다.', '스택은 배열로 만든다.'])
        self.assertEqual(reasons, Counter({'han': 1}))

    # 검증 목적: 측정 세트 질문과 띄어쓰기·문장부호만 다른 질문, 측정 판정 세트와 같은 문장이 학습 행이 되지 않는지 확인
    # - 같은 질문을 학습하면 측정 점수가 외운 효과로 부풀어 미세조정 효과를 잴 수 없음
    def test_measured_question_dropped(self):
        measured = {normalize_question('스택이 뭐야?')}
        questions = [{'id': 'q1', 'kind': 'direct', 'question': ' 스택이  뭐야 ', 'previous': [], 'expected': ['stack']},
                     {'id': 'q2', 'kind': 'direct', 'question': '스택은 어디에 써?', 'previous': [], 'expected': ['stack']}]
        rows, _, reasons = scope_rows(questions, MAP, HOLDOUT, TERMS, measured=measured)
        self.assertEqual([r['meta']['question_id'] for r in rows], ['q2'])
        self.assertEqual(reasons, Counter({'measured': 1}))

        records = [harvest('r1', '스택이 뭐야?', [('스택은 나중에 넣은 원소를 먼저 꺼낸다.', 'verified')]),
                   harvest('r2', '스택은 어디에 써?', [('스택은 함수 호출에 쓴다.', 'verified'), ('스택은 되돌리기에 쓴다.', 'verified')])]
        pool, _, reasons = verified_pool(records, HOLDOUT, TERMS, measured=measured, trainable={('ods', 's1')},
                                         judged={normalize_question('스택은 되돌리기에 쓴다')})
        self.assertEqual([v['sentence'] for v in pool], ['스택은 함수 호출에 쓴다.'])
        self.assertEqual(reasons, Counter({'measured': 2}))

        # 제외 개념 측정 세트가 나중에 생기면 제외 개념 몫 행을 그 질문으로 다시 거름(--stage refilter)
        _, held, _ = scope_rows([{'id': 'h1', 'kind': 'direct', 'question': 'BFS가 뭐야?', 'previous': [], 'expected': ['bfs']},
                                 {'id': 'h2', 'kind': 'direct', 'question': 'BFS는 어디에 써?', 'previous': [], 'expected': ['bfs']}],
                                MAP, HOLDOUT, TERMS, measured=set())
        self.assertEqual([r['meta']['question_id'] for r in refilter_rows(held, {normalize_question('bfs가 뭐야')})], ['h2'])


class GatewayKeepTests(unittest.TestCase):
    # 검증 목적: JSON으로는 해석되지만 운영 게이트웨이 parse에서 걸렸을 응답(없는 근거 키, 한자, 지도에 없는 개념, none 섞임, 틀린 판정 값)이 학습 행이 되지 않는지 확인
    # - recorded_rows는 JSON 해석까지만 거르므로, 운영 parse를 그대로 다시 돌려 같은 검사를 함
    def test_keep_rechecks_gateway_parse(self):
        source = '스택은 나중에 넣은 원소를 먼저 꺼내는 구조이다.'
        ground = {'question': '스택이 뭐야?', 'concepts': ['스택'], 'sources': [{'key': 'p1', 'text': source}], 'prior': []}
        repair = {'sentence': '스택은 먼저 넣은 것을 먼저 꺼낸다.', 'source': source, 'source_key': 'p1'}
        scope = {'concepts': [], 'previous': [], 'question': '스택이 뭐야?'}
        good = [call('ground', ground, '{"sentences":[{"text":"스택은 나중에 넣은 원소를 먼저 꺼낸다.","sources":["p1"]}]}'),
                call('repair', repair, '{"text":"스택은 나중에 넣은 것을 먼저 꺼낸다.","sources":["p1"]}'),
                call('scope', scope, '{"concepts":["stack"],"follow_up":false}'),
                call('judge', {'sentence': '스택은 LIFO다.', 'source': source}, '{"verdict":"partial","evidence":"나중에 넣은"}')]
        bad = [call('ground', {**ground, 'question': '스택?'}, '{"sentences":[{"text":"스택은 나중에 넣은 원소를 먼저 꺼낸다.","sources":["p9"]}]}'),
               call('ground', {**ground, 'question': '스택은?'}, '{"sentences":[{"text":"스택은 后入先出 구조다.","sources":["p1"]}]}'),
               call('repair', {**repair, 'sentence': '다른 문장이다.'}, '{"text":"스택은 나중에 넣은 것을 먼저 꺼낸다.","sources":["p2"]}'),
               call('scope', {**scope, 'question': '큐?'}, '{"concepts":["no-such-concept"],"follow_up":false}'),
               call('scope', {**scope, 'question': '덱?'}, '{"concepts":["none","stack"],"follow_up":false}'),
               call('judge', {'sentence': '큐는 FIFO다.', 'source': source}, '{"verdict":"maybe","evidence":"나중에 넣은"}')]
        self.assertEqual([passes_gateway(r, MAP) for r in good], [True] * 4)
        self.assertEqual([passes_gateway(r, MAP) for r in bad], [False] * 6)
        rows = recorded_rows(good + bad, keep=lambda record: passes_gateway(record, MAP))
        self.assertEqual(sorted(r['role'] for r in rows), ['ground', 'judge', 'repair', 'scope'])


class SplitTests(unittest.TestCase):
    # 검증 목적: 역할마다 5%(±1행)를 검증용으로 떼고, 같은 seed면 같은 분할이 나오는지 확인
    # - 역할별로 나누지 않으면 적은 역할(repair)이 검증용에 거의 안 들어가 그 역할의 과적합을 볼 수 없음
    def test_split_is_stratified_and_seeded(self):
        rows = [{'role': role, 'messages': [{'role': 'user', 'content': f'{role}{i}'}]}
                for role, n in (('judge', 600), ('ground', 300), ('repair', 101), ('scope', 299)) for i in range(n)]
        train, valid = split_rows(rows, seed=0)
        self.assertEqual(len(train) + len(valid), len(rows))
        for role, n in Counter(r['role'] for r in rows).items():
            with self.subTest(role=role):
                self.assertLessEqual(abs(sum(r['role'] == role for r in valid) - n * 0.05), 1)
        self.assertEqual((train, valid), split_rows(rows, seed=0))
        self.assertNotEqual(valid, split_rows(rows, seed=1)[1])
        self.assertFalse({r['messages'][0]['content'] for r in train} & {r['messages'][0]['content'] for r in valid})


if __name__ == '__main__':
    unittest.main()
