## @file
# @brief 근거 검사 하네스 흐름(범위 → 검색 → 생성 → 판정 → 수정 → 표시)과 단계별 기록을 가짜 게이트웨이·검색기로 검증
# - 모델 없이 판정 결과를 정해 두고, 검증되지 않은 문장이 "확인 필요" 표시 없이 화면에 나가는 경로가 없는지 확인함
import itertools
import json
import tempfile
import unittest
from pathlib import Path

from rag.search import Figure, Passage
from rag.sources import BOOKS
from serving.concept_map import load_concept_map
from serving.harness import (BLOCKED_TEXT, FAILED_TEXT, NEEDS_CHECK_MARK, NO_SOURCE_TEXT, Grounding, Sentence, answer,
                             render_answer)
from serving.harness_log import HarnessLog
from serving.stuck import LLMUnavailable

CONCEPT_MAP = load_concept_map()
CONCEPT = CONCEPT_MAP.concepts[0].id
PASSAGES = [Passage('ods:q.tex#2.1:0', 'ods', 'q.tex#2.1', 'Queues', 'A queue removes the oldest element first.', 0.8),
            Passage('hailperin:s.tex#3.3:0', 'hailperin', 's.tex#3.3', 'Round Robin',
                    'Round robin gives each thread\na time slice in turn.', 0.6)]
OK = ('supported', 'removes the oldest element')
OK2 = ('supported', 'a time slice in turn')


## @brief 역할마다 정해 둔 결과를 돌려주는 가짜 하네스 게이트웨이
# - judges: 문장 → 판정 결과 목록(호출 순서대로 꺼냄). 예외 객체면 그 예외를 던짐
# - repairs: 문장 → 수정 결과(예외 객체면 던짐)
class FakeGateway:
    def __init__(self, scope=([CONCEPT], False), sentences=(), judges=None, repairs=None, ground_error=None):
        self.scope_result, self.sentences = scope, list(sentences)
        self.judges, self.repairs, self.ground_error = judges or {}, repairs or {}, ground_error
        self.calls = []

    def scope(self, question, previous, concept_map):
        self.calls.append(('scope', question, tuple(previous)))
        return self.scope_result

    def ground(self, question, sources, concept_titles, prior):
        self.calls.append(('ground', tuple(sources), tuple(prior)))
        if self.ground_error:
            raise self.ground_error
        return self.sentences

    def judge(self, sentence, source_text):
        self.calls.append(('judge', sentence))
        result = self.judges[sentence].pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def repair(self, sentence, source_text, source_key):
        self.calls.append(('repair', sentence, source_key))
        result = self.repairs[sentence]
        if isinstance(result, Exception):
            raise result
        return result


## @brief 정해 둔 문단·그림을 돌려주는 가짜 검색기; 받은 인자를 기록함
class FakeRetriever:
    def __init__(self, passages=PASSAGES, figure=None):
        self.result, self.figure_result, self.calls = list(passages), figure, []

    def passages(self, concepts, query, k=3, exclude=frozenset()):
        self.calls.append(('passages', tuple(concepts), query))
        return self.result

    def by_ids(self, ids):
        self.calls.append(('by_ids', tuple(ids)))
        return [p for p in self.result if p.id in ids]

    def figure(self, concepts, query, compatible_with=()):
        self.calls.append(('figure', tuple(p.id for p in compatible_with)))
        return self.figure_result


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = HarnessLog(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def run_flow(self, gw, retriever=None, previous=None, question='큐는 무엇인가요?'):
        return list(answer(question, previous, gw, retriever or FakeRetriever(), CONCEPT_MAP, self.log))

    def records(self):
        return [json.loads(line) for path in Path(self.tmp.name).glob('*.jsonl') for line in path.read_text().splitlines()]

    # 검증 목적: 지도 밖 질문(none)은 검색·생성 없이 정확한 차단 문구로 끝남
    def test_blocked_question_returns_fixed_text(self):
        gw = FakeGateway(scope=([], False))
        events = self.run_flow(gw)
        self.assertEqual([e['type'] for e in events], ['status', 'text_start', 'text_delta', 'answer', 'done'])
        self.assertEqual(events[2]['text'], BLOCKED_TEXT)
        self.assertEqual(events[3]['text'], BLOCKED_TEXT)
        self.assertEqual([c[0] for c in gw.calls], ['scope'])

    # 검증 목적: 개념에 근거 문단이 없으면(빈칸 개념·색인 없음) 모델 생성 없이 근거 없음 문구로 끝남
    def test_no_passage_returns_no_source_text(self):
        gw = FakeGateway()
        events = self.run_flow(gw, FakeRetriever(passages=[]))
        self.assertEqual(next(e for e in events if e['type'] == 'answer')['text'], NO_SOURCE_TEXT)
        self.assertNotIn('ground', [c[0] for c in gw.calls])
        self.assertEqual(events[-1], {'type': 'done'})

    # 검증 목적: 모두 검증된 문장이 생성 순서대로 하나씩 각주와 함께 나가고, 최종 답에 원문 각주와 출처가 붙음
    def test_all_verified_emits_sentences_in_order_with_footnotes(self):
        gw = FakeGateway(sentences=[('큐는 가장 오래된 원소부터 꺼낸다.', ('p1',)), ('라운드 로빈은 차례로 시간을 준다.', ('p2',))],
                         judges={'큐는 가장 오래된 원소부터 꺼낸다.': [OK], '라운드 로빈은 차례로 시간을 준다.': [OK2]})
        events = self.run_flow(gw)
        self.assertEqual([(e['type'], e.get('stage')) for e in events[:6]],
                         [('status', 'scope'), ('status', 'search'), ('status', 'write'), ('status', 'check'),
                          ('text_start', None), ('text_delta', None)])
        deltas = [e['text'] for e in events if e['type'] == 'text_delta']
        self.assertEqual(deltas, ['큐는 가장 오래된 원소부터 꺼낸다.[^p1]', ' 라운드 로빈은 차례로 시간을 준다.[^p2]'])
        final = next(e for e in events if e['type'] == 'answer')['text']
        self.assertTrue(final.startswith(''.join(deltas)))
        self.assertIn('[^p1]: > A queue removes the oldest element first.', final)
        self.assertIn('**출처**', final)
        self.assertIn(BOOKS['hailperin'].title, final)
        self.assertNotIn(NEEDS_CHECK_MARK, final)

    # 검증 목적: 틀린 문장은 같은 문단으로 1회 수정하고, 수정 문장이 판정을 통과하면 표시 없이 나감
    def test_wrong_sentence_repaired_then_verified(self):
        gw = FakeGateway(sentences=[('큐는 최신 원소부터 꺼낸다.', ('p1',))],
                         judges={'큐는 최신 원소부터 꺼낸다.': [('unsupported', 'removes the oldest element')],
                                 '큐는 가장 오래된 원소부터 꺼낸다.': [OK]},
                         repairs={'큐는 최신 원소부터 꺼낸다.': ('큐는 가장 오래된 원소부터 꺼낸다.', ('p1',))})
        events = self.run_flow(gw)
        self.assertEqual([e['text'] for e in events if e['type'] == 'text_delta'], ['큐는 가장 오래된 원소부터 꺼낸다.[^p1]'])
        self.assertIn(('repair', '큐는 최신 원소부터 꺼낸다.', 'p1'), gw.calls)

    # 검증 목적: 수정 뒤에도 통과하지 못한 문장은 "확인 필요" 표시를 달고 나감(절반 이하라 답은 유지)
    def test_unrepairable_sentence_marked_needs_check(self):
        gw = FakeGateway(sentences=[('맞는 문장.', ('p1',)), ('틀린 문장.', ('p1',)), ('또 맞는 문장.', ('p2',))],
                         judges={'맞는 문장.': [OK], '틀린 문장.': [('partial', 'removes the oldest element')],
                                 '고친 문장.': [('unsupported', 'removes the oldest element')], '또 맞는 문장.': [OK2]},
                         repairs={'틀린 문장.': ('고친 문장.', ('p1',))})
        deltas = [e['text'] for e in self.run_flow(gw) if e['type'] == 'text_delta']
        self.assertEqual(deltas, ['맞는 문장.[^p1]', f' 고친 문장.[^p1]{NEEDS_CHECK_MARK}', ' 또 맞는 문장.[^p2]'])

    # 검증 목적: 판정이 "맞음"이어도 근거 구절이 문단에 없으면 틀림으로 보고 수정 단계로 보냄
    def test_judge_evidence_not_in_passage_counts_as_wrong(self):
        gw = FakeGateway(sentences=[('문장.', ('p1',))],
                         judges={'문장.': [('supported', 'removes the newest element')], '고친 문장.': [OK]},
                         repairs={'문장.': ('고친 문장.', ('p1',))})
        deltas = [e['text'] for e in self.run_flow(gw) if e['type'] == 'text_delta']
        self.assertEqual(deltas, ['고친 문장.[^p1]'])

    # 검증 목적: 판정 호출이 모델 오류로 실패하면 수정 없이 그 문장을 "확인 필요"로 둠
    def test_judge_failure_marks_needs_check(self):
        gw = FakeGateway(sentences=[('하나.', ('p1',)), ('둘.', ('p1',)), ('셋.', ('p2',))],
                         judges={'하나.': [OK], '둘.': [LLMUnavailable('down')], '셋.': [OK2]})
        deltas = [e['text'] for e in self.run_flow(gw) if e['type'] == 'text_delta']
        self.assertEqual(deltas[1], f' 둘.[^p1]{NEEDS_CHECK_MARK}')
        self.assertNotIn('repair', [c[0] for c in gw.calls])

    # 검증 목적: 확인 필요 문장이 절반을 넘으면 이미 보낸 문장을 text_start로 지우고 생성 실패 문구와 원문으로 바꿈
    def test_majority_wrong_replaced_by_fallback(self):
        bad = ('unsupported', 'removes the oldest element')
        gw = FakeGateway(sentences=[('하나.', ('p1',)), ('둘.', ('p1',)), ('셋.', ('p1',))],
                         judges={'하나.': [OK], '둘.': [bad, bad], '셋.': [bad, bad]},
                         repairs={'둘.': ('둘.', ('p1',)), '셋.': ('셋.', ('p1',))})
        events = self.run_flow(gw)
        types = [e['type'] for e in events]
        self.assertEqual(types.count('text_start'), 2)
        final = next(e for e in events if e['type'] == 'answer')['text']
        self.assertTrue(final.startswith(FAILED_TEXT))
        self.assertIn('> A queue removes the oldest element first.', final)
        self.assertNotIn('하나.', final)
        last_reset = len(types) - 1 - types[::-1].index('text_start')
        self.assertEqual(''.join(e['text'] for e in events[last_reset:] if e['type'] == 'text_delta'), final)

    # 검증 목적: 생성 호출이 끝내 실패하면 문장 없이 생성 실패 문구와 교재 원문만 보여 줌
    def test_ground_failure_returns_fallback(self):
        events = self.run_flow(FakeGateway(ground_error=LLMUnavailable('down')))
        final = next(e for e in events if e['type'] == 'answer')['text']
        self.assertTrue(final.startswith(FAILED_TEXT))
        self.assertIn('Round robin gives each thread', final)

    # 검증 목적(Review Focus 1): 첫 판정 × 수정 × 재판정의 모든 조합에서, 표시 없이 나간 문장은 반드시 검증된 문장
    # - 판정: 맞음+구절 있음, 맞음+구절 없음, 틀림, 일부만, 예외 / 수정: 성공, 예외
    def test_no_unverified_sentence_leaks(self):
        outcomes = [OK, ('supported', 'not in the passage'), ('unsupported', 'removes the oldest element'),
                    ('partial', 'removes the oldest element'), LLMUnavailable('down'), RuntimeError('boom')]
        for first, repair_ok, second in itertools.product(outcomes, (True, False), outcomes):
            with self.subTest(first=first, repair_ok=repair_ok, second=second):
                gw = FakeGateway(sentences=[('대상.', ('p1',)), ('기준 하나.', ('p1',)), ('기준 둘.', ('p2',))],
                                 judges={'대상.': [first], '고친 대상.': [second], '기준 하나.': [OK], '기준 둘.': [OK2]},
                                 repairs={'대상.': ('고친 대상.', ('p1',)) if repair_ok else LLMUnavailable('down')})
                events = self.run_flow(gw)
                verified = first == OK or (not isinstance(first, Exception) and repair_ok and second == OK)
                shown = [e['text'] for e in events if e['type'] in ('text_delta', 'answer')]
                self.assertTrue(any('대상.[^p1]' in text for text in shown))
                for text in shown:
                    marked = text.count(f'대상.[^p1]{NEEDS_CHECK_MARK}')
                    self.assertEqual(marked, 0 if verified else text.count('대상.[^p1]'))
                grounding = next(e for e in events if e.get('kind') == 'grounding')['grounding']
                self.assertEqual(any('대상' in v for v in grounding['verified']), verified)

    # 검증 목적: 다음 턴이 이어받을 근거 기록(grounding)이 answer 바로 앞에 나가고, 검증된 문장만 담음
    def test_grounding_artifact_emitted_last_before_answer(self):
        gw = FakeGateway(sentences=[('맞음.', ('p1',)), ('표시.', ('p2',)), ('또 맞음.', ('p1',))],
                         judges={'맞음.': [OK], '표시.': [LLMUnavailable('down')], '또 맞음.': [OK]})
        events = self.run_flow(gw)
        self.assertEqual([e['type'] for e in events[-3:]], ['artifact', 'answer', 'done'])
        artifact = events[-3]
        self.assertEqual(artifact['kind'], 'grounding')
        self.assertRegex(artifact['artifact_id'], r'^[0-9a-f]{32}$')
        self.assertEqual(artifact['grounding'], {'concepts': [CONCEPT], 'passage_ids': [p.id for p in PASSAGES],
                                                 'verified': ['맞음.', '또 맞음.']})

    # 검증 목적: 그림은 근거 문단과 섞을 수 있는 것만 받아 grounding 앞에 artifact로 나가고, 화면 출처를 함께 담음
    def test_figure_artifact_before_grounding(self):
        figure = Figure('hailperin:hail_f0303', 'hailperin', 's.tex#3.3', 'Thread states', Path('/x/f.png'), 0.5)
        retriever = FakeRetriever(figure=figure)
        gw = FakeGateway(sentences=[('맞음.', ('p1',))], judges={'맞음.': [OK]})
        events = self.run_flow(gw, retriever)
        self.assertEqual([e.get('kind') for e in events[-4:-2]], ['figure', 'grounding'])
        self.assertEqual(events[-4]['path'], '/x/f.png')
        self.assertEqual(events[-4]['figure']['caption'], 'Thread states')
        self.assertIn(BOOKS['hailperin'].license, events[-4]['figure']['source'])
        self.assertIn(('figure', tuple(p.id for p in PASSAGES)), retriever.calls)

    # 검증 목적: 후속 질문은 직전 기록의 개념·문단을 그대로 쓰고, 직전 검증 문장을 생성 입력(prior)으로 넘김
    def test_follow_up_reuses_previous_passages_and_prior(self):
        previous = Grounding(('queue',), (PASSAGES[1].id,), ('이전 문장.',))
        gw = FakeGateway(scope=([], True), sentences=[('쉽게.', ('p1',))], judges={'쉽게.': [OK2]})
        retriever = FakeRetriever()
        events = self.run_flow(gw, retriever, previous, '더 쉽게')
        self.assertEqual(gw.calls[0], ('scope', '더 쉽게', ('queue',)))
        self.assertEqual(retriever.calls[0], ('by_ids', (PASSAGES[1].id,)))
        self.assertIn(('ground', ('p1',), ('이전 문장.',)), gw.calls)
        grounding = next(e for e in events if e.get('kind') == 'grounding')['grounding']
        self.assertEqual(grounding['concepts'], ['queue'])

    # 검증 목적: 직전 근거 기록이 없으면(스위치를 켜기 전 대화) 후속 판정이어도 범위 결정 결과로 검색함
    def test_follow_up_without_previous_uses_scope(self):
        gw = FakeGateway(scope=([], True))
        events = self.run_flow(gw, question='더 쉽게')
        self.assertEqual(next(e for e in events if e['type'] == 'answer')['text'], BLOCKED_TEXT)

    # 검증 목적: 한 질문의 기록에 고른 개념·문단 유사도·문장별 판정과 근거 구절·단계별 소요 시간이 남음(spec 5-7)
    def test_log_record_has_stage_timings(self):
        gw = FakeGateway(sentences=[('문장.', ('p1',))],
                         judges={'문장.': [('unsupported', 'x')], '고친 문장.': [OK]}, repairs={'문장.': ('고친 문장.', ('p1',))})
        self.run_flow(gw)
        [record] = self.records()
        self.assertEqual(set(record['timings']), {'scope', 'search', 'write', 'check'})
        self.assertTrue(all(v >= 0 for v in record['timings'].values()))
        self.assertEqual(record['concepts'], [CONCEPT])
        self.assertEqual(record['passages'][0], {'key': 'p1', 'id': PASSAGES[0].id, 'score': 0.8})
        self.assertEqual(record['outcome'], 'answered')
        [sentence] = record['sentences']
        self.assertEqual(sentence['status'], 'verified')
        self.assertEqual(sentence['text'], '고친 문장.')
        self.assertEqual([j['verdict'] for j in sentence['judgments']], ['unsupported', 'supported'])
        self.assertEqual(sentence['repairs'], 1)

    # 검증 목적: 범위 결정 호출이 실패하면 예외를 호출한 쪽(채팅 연결)으로 넘기되 기록은 남김
    def test_scope_failure_raises_and_logs(self):
        class Failing(FakeGateway):
            def scope(self, *args):
                raise LLMUnavailable('down')

        with self.assertRaises(LLMUnavailable):
            self.run_flow(Failing())
        self.assertEqual(self.records()[0]['outcome'], 'error')


class RenderTests(unittest.TestCase):
    # 검증 목적: 각주 정의는 문장이 가리킨 문단만 원문 인용과 교재명·저자·절·라이선스·발췌로 쓰고, 확인 필요 표시를 유지
    def test_render_answer_footnotes_and_sources(self):
        text = render_answer([Sentence('하나.', ('p2',), 'verified'), Sentence('둘.', ('p2',), 'needs_check')],
                             {'p1': PASSAGES[0], 'p2': PASSAGES[1]}, BOOKS)
        self.assertTrue(text.startswith(f'하나.[^p2] 둘.[^p2]{NEEDS_CHECK_MARK}\n\n'))
        self.assertNotIn('[^p1]', text)
        self.assertIn('[^p2]: > Round robin gives each thread a time slice in turn.', text)
        book = BOOKS['hailperin']
        self.assertIn(f'{book.title}, {book.authors}, 3.3 Round Robin, [{book.license}]({book.license_url}), 발췌', text)


if __name__ == '__main__':
    unittest.main()
