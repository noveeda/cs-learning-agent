# 파일 역할: Mohler 개발 검증 도구(eval/mohler.py)가 문항 단위로 분할하고, 통과 기준을 dev 분할에서만 고르며,
#            거짓 통과율·구간을 바르게 내고, 중단 뒤 채점을 이어 하는지 검증(모델 서버 없이 가짜 Gateway 사용)
import json
import tempfile
import unittest
from pathlib import Path

from eval import mohler
from eval.metrics import wilson_interval


def row(question, n, score, grader_1=None, grader_2=None):
    return {'id': f'{question}.A{n:02d}', 'question': f'question {question}', 'instructor_answer': 'reference',
            'student_answer': f'answer {question} {n}', 'score_grader_1': score if grader_1 is None else grader_1,
            'score_grader_2': score if grader_2 is None else grader_2, 'score_avg': score}


# 역할: 답마다 정해 둔 met 목록을 돌려주는 가짜 Gateway; 채점한 답 문장을 calls에 남김
class FakeGateway:
    def __init__(self, met_by_answer=None):
        self.met_by_answer, self.calls = met_by_answer or {}, []

    def grade_explanation(self, item, answer):
        self.calls.append(answer)
        return self.met_by_answer.get(answer, [True] * len(item.rubric))


class SplitTest(unittest.TestCase):
    # 검증: 행 id 'E01.Q01.A00'의 문항 ID는 답 번호를 뗀 'E01.Q01'(루브릭 파일의 question_id와 같은 규칙)
    def test_question_id_rule(self):
        self.assertEqual(mohler.question_id('E01.Q01.A00'), 'E01.Q01')
        self.assertEqual(mohler.question_id('E12.Q10.A27'), 'E12.Q10')

    # 검증: 문항 단위로 절반씩 나뉘고 같은 문항의 답이 양쪽에 섞이지 않으며, 같은 seed면 같은 분할
    # - 같은 문항의 답이 양쪽에 있으면 dev에서 고른 기준이 report 문항을 이미 본 셈이 되어 일치도가 부풀려짐
    def test_split_by_question_no_leak(self):
        questions = [f'E01.Q{q:02d}' for q in range(1, 11)]
        rows = [row(q, n, 4.0) for q in questions for n in range(3)]
        dev, report = mohler.split_questions(rows, seed=7)
        self.assertEqual(len(dev), 5)
        self.assertEqual(len(report), 5)
        self.assertFalse(set(dev) & set(report))
        self.assertEqual(set(dev) | set(report), set(questions))
        self.assertEqual((dev, report), mohler.split_questions(list(reversed(rows)), seed=7))

    # 검증: 문항 ID가 달라도 질문 문장(대소문자·문장부호·공백 정리 후)이 같으면 seed와 무관하게 늘 같은 분할에 들어감
    # - 원본에는 같은 질문이 다른 과제에 다시 나옴(예: E09.Q01·E12.Q06 'What is a queue?')
    #   둘이 갈리면 dev에서 고른 기준이 report 문항과 같은 질문·모범 답을 이미 본 셈이 됨
    def test_same_question_text_stays_together(self):
        rows = [row(f'E01.Q{q:02d}', 0, 4.0) for q in range(1, 9)]
        twins = [row('E02.Q01', 0, 4.0), row('E03.Q05', 0, 4.0)]
        twins[0]['question'], twins[1]['question'] = 'What is a queue?', '  what is a QUEUE '
        for seed in range(40):
            dev, report = mohler.split_questions(rows + twins, seed=seed)
            self.assertEqual('E02.Q01' in dev, 'E03.Q05' in dev, seed)
            self.assertEqual(set(dev) | set(report), {r['id'].rsplit('.', 1)[0] for r in rows + twins})
            self.assertLessEqual(abs(len(dev) - len(report)), 2)

    # 검증: 문장은 다르지만 뜻이 같다고 명시한 묶음(SAME_MEANING)도 seed와 무관하게 늘 같은 분할에 들어감
    def test_same_meaning_pairs_stay_together(self):
        ids = sorted({q for pair in mohler.SAME_MEANING for q in pair}) + [f'E01.Q{q:02d}' for q in range(1, 9)]
        rows = [row(q, 0, 4.0) for q in ids]
        for seed in range(40):
            dev, _ = mohler.split_questions(rows, seed=seed)
            for first, second in mohler.SAME_MEANING:
                self.assertEqual(first in dev, second in dev, (seed, first, second))


# 역할: 평가 시험용 데이터; dev 문항 2개·report 문항 2개, 답마다 사람 점수와 AI 충족 목록을 둠
def evaluation_data(report_cases):
    dev_cases = [  # (사람 점수, AI met) — 사람 기준 4.0에서 임계값 0.5만 완전 일치(kappa 1)가 되게 구성
        (5.0, [True, False]), (4.5, [True, False]), (2.0, [False, False]), (1.0, [False, False]),
        (4.0, [True, True]), (3.0, [False, False])]
    rows, grades = [], []
    for question, cases in (('E01.Q01', dev_cases[:3]), ('E01.Q02', dev_cases[3:]),
                            ('E02.Q01', report_cases[:len(report_cases) // 2]),
                            ('E02.Q02', report_cases[len(report_cases) // 2:])):
        for n, (score, met) in enumerate(cases):
            rows.append(row(question, n, score))
            grades.append({'id': rows[-1]['id'], 'met': met})
    return rows, grades, ['E01.Q01', 'E01.Q02'], ['E02.Q01', 'E02.Q02']


class EvaluateTest(unittest.TestCase):
    # 검증: AI 통과 임계값은 dev 분할에서만 고름; report 분할의 점수·판정을 바꿔도 고른 값이 같음(Review Focus 3)
    # - report 결과를 보고 기준을 고르면 보고하는 일치도가 낙관적으로 치우침
    def test_threshold_chosen_on_dev_only(self):
        first = mohler.evaluate(*evaluation_data([(5.0, [True, True]), (1.0, [False, False])]))
        # report에는 충족 비율 0.5인 사람 미통과 답을 많이 넣음: report까지 보고 고르면 1.0이 뽑혀 이 시험이 실패함
        second = mohler.evaluate(*evaluation_data([(5.0, [True, True])] + [(2.0, [True, False])] * 5
                                                  + [(4.5, [True, True]), (1.0, [True, False])]))
        self.assertEqual(first['primary']['ai_threshold'], 0.5)
        self.assertEqual(second['primary']['ai_threshold'], 0.5)
        for human in ('3.5', '4.0', '4.5'):
            self.assertEqual(first['by_human_threshold'][human]['ai_threshold'],
                             second['by_human_threshold'][human]['ai_threshold'])

    # 검증: 거짓 통과율 = 사람 미통과 중 AI 통과 비율이고, Wilson 95% 구간이 metrics.wilson_interval과 같음
    def test_false_pass_rate_and_interval(self):
        report_cases = [  # 사람 미통과 4개 중 AI 통과 1개, 사람 통과 2개 중 AI 통과 2개
            (1.0, [True, True]), (2.0, [False, False]), (3.0, [False, False]), (3.5, [False, False]),
            (4.0, [True, True]), (5.0, [True, True])]
        report = mohler.evaluate(*evaluation_data(report_cases))['primary']['report']
        self.assertEqual((report['human_fail'], report['false_pass']), (4, 1))
        self.assertAlmostEqual(report['false_pass_rate'], 0.25)
        self.assertEqual(tuple(report['false_pass_interval']), tuple(round(v, 4) for v in wilson_interval(1, 4)))
        self.assertAlmostEqual(report['sensitivity'], 1.0)
        self.assertAlmostEqual(report['specificity'], 0.75)
        self.assertAlmostEqual(report['agreement'], 5 / 6, places=4)

    # 검증: 사람 채점자 둘 사이 kappa는 같은 통과 기준으로 report 분할에서 계산함(AI-사람 일치도의 현실적 상한)
    def test_human_kappa_between_graders(self):
        rows, grades, dev, report_ids = evaluation_data([(4.0, [True, True]), (2.0, [False, False])])
        for r in rows:  # dev 분할에서만 두 채점자가 정반대: report에서만 계산하면 1.0이어야 함
            if r['id'].startswith('E01'):
                r['score_grader_1'], r['score_grader_2'] = 5.0, 0.0
        result = mohler.evaluate(rows, grades, dev, report_ids)
        self.assertEqual(result['primary']['report']['human_kappa'], 1.0)


class GradeTest(unittest.TestCase):
    # 검증: 결과 파일에 이미 있는 답은 다시 채점하지 않고 남은 답만 채점해 덧붙임; 루브릭과 영어 원문을 그대로 넘김
    # - 2,273개를 운영 8000에 순차로 보내므로(약 40분) 중간에 끊겨도 처음부터 다시 보내지 않기 위함
    def test_resume_skips_graded_ids(self):
        rows = [row('E01.Q01', n, 4.0) for n in range(3)]
        rubrics = {'E01.Q01': ['element one', 'element two']}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'grades.jsonl'
            out.write_text(json.dumps({'id': rows[0]['id'], 'met': [False, False]}) + '\n', encoding='utf-8')
            gateway = FakeGateway({rows[2]['student_answer']: [True, False]})
            grades = mohler.grade(rows, rubrics, gateway, out)
            saved = [json.loads(line) for line in out.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(gateway.calls, [rows[1]['student_answer'], rows[2]['student_answer']])
        self.assertEqual([g['id'] for g in grades], [r['id'] for r in rows])
        self.assertEqual(grades[0]['met'], [False, False])
        self.assertEqual(grades[2]['met'], [True, False])
        self.assertEqual(len(saved), 3)

    # 검증: 루브릭이 없는 문항의 답은 채점하지 않고 건너뜀(모르는 기준으로 판정하지 않음)
    def test_rows_without_rubric_skipped(self):
        rows = [row('E01.Q01', 0, 4.0), row('E01.Q02', 0, 4.0)]
        with tempfile.TemporaryDirectory() as tmp:
            grades = mohler.grade(rows, {'E01.Q01': ['element']}, FakeGateway(), Path(tmp) / 'grades.jsonl')
        self.assertEqual([g['id'] for g in grades], ['E01.Q01.A00'])


if __name__ == '__main__':
    unittest.main()
