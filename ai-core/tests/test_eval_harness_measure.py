## @file
# @brief 하네스 모델별 측정(eval.harness_measure)의 지표 계산 metrics(rows)가 spec 8-2 정의대로 수치를 내는지 검증
# - 모델 호출 없이 측정 결과 행(rows.jsonl과 같은 모양)을 직접 만들어 넣음. 지표 정의가 바뀌면 모델별 비교가 무너지므로 순수 함수로 고정함
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from eval.harness_measure import astra_pass, bootstrap_diff, judge_by_origin, metrics, metrics_by_group, report


def _answer(outcome, sentences, task='answer', **extra):
    return {'task': task, 'id': extra.pop('id', 'q'), 'kind': 'direct', 'outcome': outcome, 'sentences': sentences, **extra}


class MetricsTests(unittest.TestCase):
    # 검증 목적: 시간 지표가 단일 요청 표본(timing 행)만으로 중앙값·90% 값을 내는지 확인
    # - 90% 값은 양 끝을 포함한 선형 보간(값 1~10이면 9.1). 동시 요청으로 잰 answer 행의 시간이 섞이면 시간 기준 판정이 틀어짐
    def test_metrics_percentiles(self):
        rows = [_answer('answered', [], task='timing', id=f't{n}', first_s=float(n), total_s=float(n * 10))
                for n in range(1, 11)]
        rows.append(_answer('answered', [], id='concurrent', first_s=999.0, total_s=999.0))
        timing = metrics(rows)['timing']
        self.assertEqual(timing['n'], 10)
        self.assertAlmostEqual(timing['first']['median'], 5.5)
        self.assertAlmostEqual(timing['first']['p90'], 9.1)
        self.assertAlmostEqual(timing['total']['median'], 55.0)
        self.assertAlmostEqual(timing['total']['p90'], 91.0)

    # 검증 목적: 노출 지표의 분모·분자가 "표시 없이 나간 문장"(답변 완료 답의 verified 문장)만 세는지 확인
    # - 확인 필요 문장은 화면에 ⚠️가 붙고, 원문 대체(fallback) 답의 문장은 화면에서 지워지므로 둘 다 노출이 아님
    # - 26B 판정 호출이 실패한 문장(audit None)은 불일치로도 일치로도 세지 않고 따로 셈
    def test_exposure_counts_only_unmarked_sentences(self):
        rows = [
            _answer('answered', [{'status': 'verified', 'han': False, 'audit': False},
                                 {'status': 'verified', 'han': False, 'audit': True},
                                 {'status': 'verified', 'han': False, 'audit': None},
                                 {'status': 'needs_check', 'han': False, 'audit': False}]),
            _answer('fallback', [{'status': 'verified', 'han': False, 'audit': False},
                                 {'status': 'needs_check', 'han': False, 'audit': False}], id='f'),
        ]
        answers = metrics(rows)['answers']
        self.assertEqual(answers['exposure'], {'unmarked': 3, 'audited': 2, 'mismatch': 1, 'audit_errors': 1, 'rate': 0.5})
        self.assertEqual(answers['shown_sentences'], 4)
        self.assertAlmostEqual(answers['needs_check_rate'], 0.25)

    # 검증 목적: 거짓 통과는 사람 미통과 중 모델 통과, 거짓 불통과는 사람 통과 중 모델 미통과 비율인지 확인
    # - 모델 호출이 실패한 답(model_pass None)은 판정이 없으므로 분모에서 빼고 errors로 셈
    def test_false_pass_and_false_fail(self):
        cases = [(False, True), (False, False), (False, False), (False, False),
                 (True, True), (True, False), (True, None)]
        rows = [{'task': 'grading', 'id': f'g{n}', 'human_pass': human, 'model_pass': model}
                for n, (human, model) in enumerate(cases)]
        grading = metrics(rows)['grading']
        self.assertEqual((grading['n'], grading['errors']), (6, 1))
        self.assertEqual((grading['human_fail'], grading['false_pass']), (4, 1))
        self.assertAlmostEqual(grading['false_pass_rate'], 0.25)
        self.assertEqual((grading['human_pass'], grading['false_fail']), (2, 1))
        self.assertAlmostEqual(grading['false_fail_rate'], 0.5)


class FinetuneMeasureTests(unittest.TestCase):
    # 검증 목적: 학습한 개념과 제외 개념의 점수를 나눠야 문단을 미리 본 효과로 부푼 점수가 합격 판정에 섞이지 않음
    # - 범위 행은 expected, 생성 행은 expected(없으면 concepts)로 개념을 봄. 개념 하나라도 제외 개념이면 제외 쪽
    def test_metrics_by_group_splits_holdout(self):
        rows = [{'task': 'scope', 'id': 's1', 'kind': 'direct', 'expected': ['bfs'], 'concepts': ['bfs']},
                {'task': 'scope', 'id': 's2', 'kind': 'direct', 'expected': ['stack'], 'concepts': ['queue']},
                {'task': 'scope', 'id': 's3', 'kind': 'compare', 'expected': ['stack', 'bfs'], 'concepts': ['stack']},
                _answer('answered', [{'status': 'verified', 'han': False, 'audit': False}], id='a1', concepts=['bfs']),
                _answer('answered', [{'status': 'verified', 'han': False, 'audit': True}], id='a2', expected=['stack'],
                        concepts=['stack'])]
        groups = metrics_by_group(rows, {'bfs'})
        self.assertEqual((groups['holdout']['scope']['n'], groups['holdout']['scope']['accuracy']), (2, 0.5))
        self.assertEqual((groups['trained']['scope']['n'], groups['trained']['scope']['accuracy']), (1, 0.0))
        self.assertEqual(groups['holdout']['answers']['exposure']['rate'], 1.0)
        self.assertEqual(groups['trained']['answers']['exposure']['rate'], 0.0)

    # 검증 목적: 미세조정 전후 비율 차이의 95% 구간이 실제 차이(-0.08)를 담고, 차이가 뚜렷하면 0을 담지 않아야
    # "좋아졌다"를 표본 운으로 말하지 않을 수 있음. 고정 seed라 다시 돌려도 같은 구간
    def test_bootstrap_diff_contains_true_difference(self):
        before = [1] * 50 + [0] * 450
        after = [1] * 10 + [0] * 490
        low, high = bootstrap_diff(before, after, seed=0)
        self.assertLess(low, -0.08)
        self.assertGreater(high, -0.08)
        self.assertLess(high, 0)
        self.assertEqual(bootstrap_diff(before, after, seed=0), (low, high))

    # 검증 목적: 규칙 교란(숫자·부정)과 모델 교란(덧붙이기·개념 바꾸기) 검출률을 나눠야
    # 학습이 26B가 만든 교란 모양만 외웠는지 볼 수 있음
    def test_judge_by_origin(self):
        rows = [{'task': 'judge', 'id': f'j{n}', 'label': 'unsupported', 'perturbation': kind, 'passed': passed}
                for n, (kind, passed) in enumerate([('number', False), ('negation', True), ('addition', False),
                                                    ('swap_concept', False), ('addition', True)])]
        rows.append({'task': 'judge', 'id': 'ok', 'label': 'supported', 'perturbation': None, 'passed': True})
        self.assertEqual(judge_by_origin(rows), {'rule': {'n': 2, 'detection': 0.5}, 'model': {'n': 3, 'detection': 0.6667}})


    # 검증 목적: 외부 검사는 노출 대상 문장(답변 완료 답의 verified)만, 아직 판정 못 한 문장만 보내고 문단 원문을 문단 ID로 다시 찾음
    # - 이미 판정한 문장을 다시 보내면 검사 비용이 늘고, 확인 필요·원문 대체 문장을 보내면 노출 지표 분모가 틀어짐
    def test_astra_pass_checks_only_unmarked_unchecked(self):
        rows = [_answer('answered', [{'text': '새 문장', 'sources': ['p1'], 'status': 'verified', 'han': False, 'audit': True},
                                     {'text': '판정됨', 'sources': ['p1'], 'status': 'verified', 'han': False, 'audit': True,
                                      'astra': True},
                                     {'text': '확인 필요', 'sources': ['p1'], 'status': 'needs_check', 'han': False,
                                      'audit': None}], passages={'p1': 'c1'}),
                _answer('fallback', [{'text': '지워짐', 'sources': ['p1'], 'status': 'verified', 'han': False, 'audit': None}],
                        id='f', passages={'p1': 'c1'})]
        retriever = SimpleNamespace(by_ids=lambda ids: [SimpleNamespace(id=i, text=f'{i} 원문') for i in ids])
        sent = []

        def runner(prompt, schema):
            sent.append(prompt)
            return {'results': [{'id': 0, 'supported': False}]}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'rows.jsonl'
            path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
            self.assertEqual(astra_pass(path, retriever, runner), 1)
            saved = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        self.assertIn('새 문장', sent[0])
        self.assertIn('c1 원문', sent[0])
        self.assertNotIn('판정됨', sent[0])
        self.assertEqual([s.get('astra') for s in saved[0]['sentences']], [False, True, None])
        answers = metrics(saved)['answers']
        self.assertEqual(answers['exposure_astra'], {'audited': 2, 'mismatch': 1, 'errors': 0, 'rate': 0.5})
        groups = {'trained': metrics(saved), 'holdout': {}}
        self.assertIn('노출 지표(외부 검사)', report(metrics(saved), {'model': 'm'}, groups))


if __name__ == '__main__':
    unittest.main()
