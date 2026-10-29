## @file
# @brief 하네스 모델별 측정(eval.harness_measure)의 지표 계산 metrics(rows)가 spec 8-2 정의대로 수치를 내는지 검증
# - 모델 호출 없이 측정 결과 행(rows.jsonl과 같은 모양)을 직접 만들어 넣음. 지표 정의가 바뀌면 모델별 비교가 무너지므로 순수 함수로 고정함
import unittest

from eval.harness_measure import metrics


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


if __name__ == '__main__':
    unittest.main()
