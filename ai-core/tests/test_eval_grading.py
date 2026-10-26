# 파일 역할: 한국어 답 채점 도구(채점 시트·AI 채점·일치도)가 눈가림을 지키고, 채점자 수와 빈칸에 상관없이 지표를 바르게 내는지 검증
import unittest

from eval import agreement, ai_grade, grading_sheet
from eval.metrics import cohen_kappa
from serving.item_bank import load_item_bank
from serving.stuck import LLMUnavailable

ITEMS = load_item_bank()


# 역할: MVP 2 답 시트 형식의 가짜 답; 개발자 답 8개(연습 5개를 빼도 3개 남음), 학생 답 4개, 빈 답 1개
def fake_answers():
    rows = []
    for n in range(13):
        author = 'dev' if n < 8 else ('S1' if n < 12 else 'S2')
        rows.append({'answer_id': f'a{n:02d}', 'item_id': 'stack-e1' if n % 2 else 'queue-e1', 'concept': '',
                     'question': '', 'phase': 'before' if n % 3 else 'after', 'author': author,
                     'answer': '' if n == 12 else f'답 {n}'})
    return rows


# 역할: 시트 행에 사람 판정을 채움; verdicts는 answer_id → 통과 여부, 요소 판정은 통과면 모두 1, 아니면 첫 요소만 0
def fill(rows, verdicts):
    for row in rows:
        if row['answer_id'] in verdicts:
            passed = verdicts[row['answer_id']]
            for k in range(1, 4):
                row[f'met_{k}'] = '1' if passed or k > 1 else '0'
            row['overall_pass'] = '1' if passed else '0'
    return rows


def scored_ids(rows):
    return [r['answer_id'] for r in rows if r['practice'] == '0']


class GradingSheetTests(unittest.TestCase):
    # 검증 목적: 채점 시트에 작성자·phase·AI 판정이 없고(눈가림), 빈 답은 빠지며, 연습 5개는 개발자 답에서 맨 앞에 오고, 채점자마다 순서가 다름
    def test_sheet_hides_author_phase_ai(self):
        answers = fake_answers()
        sheet = grading_sheet.build_sheet(answers, ITEMS, 'G1')
        self.assertEqual(list(sheet[0]), list(grading_sheet.SHEET_COLUMNS))
        self.assertFalse({'author', 'phase', 'item_id', 'ai'} & set(grading_sheet.SHEET_COLUMNS))
        self.assertNotIn('a12', [r['answer_id'] for r in sheet])
        self.assertEqual(len(sheet), 12)
        authors = {a['answer_id']: a['author'] for a in answers}
        practice = [r for r in sheet if r['practice'] == '1']
        self.assertEqual(len(practice), grading_sheet.PRACTICE_COUNT)
        self.assertEqual(sheet[:5], practice)
        self.assertTrue(all(authors[r['answer_id']] == 'dev' for r in practice))
        stack = next(i for i in ITEMS if i.id == 'stack-e1')
        row = next(r for r in sheet if r['answer_id'] == 'a01')
        self.assertEqual([row[f'rubric_{k}'] for k in range(1, 5)], stack.rubric + [''] * (4 - len(stack.rubric)))
        self.assertTrue(all(row[f'met_{k}'] == '' for k in range(1, 5)) and row['overall_pass'] == '')
        other = grading_sheet.build_sheet(answers, ITEMS, 'G2')
        self.assertEqual(sorted(scored_ids(sheet)), sorted(scored_ids(other)))
        self.assertNotEqual(scored_ids(sheet), scored_ids(other))
        criteria = grading_sheet.build_criteria(answers, ITEMS)
        self.assertEqual(len(criteria), len(stack.rubric) + len(next(i for i in ITEMS if i.id == 'queue-e1').rubric))


class AgreementTests(unittest.TestCase):
    def setUp(self):
        self.answers = fake_answers()
        self.g1 = grading_sheet.build_sheet(self.answers, ITEMS, 'G1')
        self.g2 = grading_sheet.build_sheet(self.answers, ITEMS, 'G2')
        self.ids = sorted(scored_ids(self.g1))

    # 검증 목적: 채점자 2명이면 사람 간 kappa(전체 판정·요소)를 내고, 갈린 답을 모아 합의 라벨로 최종 라벨을 만듦
    def test_two_graders_kappa_and_disagreements(self):
        first = {i: n % 2 == 0 for n, i in enumerate(self.ids)}
        second = dict(first, **{self.ids[0]: not first[self.ids[0]], self.ids[1]: not first[self.ids[1]]})
        fill(self.g1, first), fill(self.g2, second)
        report = agreement.analyze({'G1': self.g1, 'G2': self.g2}, self.answers)
        human = report['human']
        self.assertEqual(human['kind'], 'inter')
        self.assertAlmostEqual(human['kappa'], cohen_kappa([first[i] for i in self.ids], [second[i] for i in self.ids]))
        self.assertIn('element_kappa', human)
        self.assertAlmostEqual(human['agreement'], (len(self.ids) - 2) / len(self.ids))
        self.assertEqual(report['disagreements'], sorted(self.ids[:2]))
        self.assertEqual(report['final']['pending'], 2)
        settled = agreement.analyze({'G1': self.g1, 'G2': self.g2}, self.answers,
                                    final_rows=[{'answer_id': i, 'final_pass': '1'} for i in self.ids[:2]])
        self.assertEqual((settled['final']['labeled'], settled['final']['pending']), (len(self.ids), 0))

    # 검증 목적: 채점자 1명이면 사람 간 kappa 대신 재채점 경로를 씀; 재채점 전에는 대기, 재채점 시트가 오면 채점자 내 kappa
    def test_single_grader_uses_regrade_path(self):
        fill(self.g1, {i: n % 2 == 0 for n, i in enumerate(self.ids)})
        pending = agreement.analyze({'G1': self.g1}, self.answers)
        self.assertEqual((pending['human']['kind'], pending['human']['status']), ('intra', 'pending'))
        self.assertNotIn('kappa', pending['human'])
        regrade = agreement.regrade_sheet(self.g1, 'G1', size=50)
        self.assertEqual(sorted(r['answer_id'] for r in regrade), self.ids)
        self.assertTrue(all(r['overall_pass'] == '' and r['practice'] == '0' for r in regrade))
        self.assertNotEqual([r['answer_id'] for r in regrade], scored_ids(self.g1))
        fill(regrade, {i: n % 2 == 0 for n, i in enumerate(self.ids)})
        done = agreement.analyze({'G1': self.g1}, self.answers, regrade_rows=regrade)
        self.assertEqual((done['human']['kind'], done['human']['kappa']), ('intra', 1.0))
        self.assertEqual(done['final']['labeled'], len(self.ids))

    # 검증 목적: 한쪽만 채점한 답(또는 전체 판정 빈칸)은 사람 간 계산에서 빼고 개수를 보고하며, 계산이 깨지지 않음
    def test_partial_sheet_does_not_crash(self):
        fill(self.g1, {i: True for i in self.ids})
        fill(self.g2, {i: True for i in self.ids[:3]})
        next(r for r in self.g2 if r['answer_id'] == self.ids[2])['overall_pass'] = ''
        report = agreement.analyze({'G1': self.g1, 'G2': self.g2}, self.answers)
        self.assertEqual(report['human']['compared'], 2)
        self.assertEqual(report['human']['only_one_grader'], len(self.ids) - 2)
        self.assertEqual(report['human']['kappa'], 1.0)

    # 검증 목적: AI 대 최종 라벨 지표를 학생 답·개발자 답·전체로 나눠 내고, 미통과가 100개 미만이면 맨 위에 경고
    def test_split_by_author_group(self):
        verdicts = {i: False for i in self.ids}
        fill(self.g1, verdicts), fill(self.g2, verdicts)
        authors = {a['answer_id']: a['author'] for a in self.answers}
        dev = [i for i in self.ids if authors[i] == 'dev']
        student = [i for i in self.ids if authors[i].startswith('S')]
        ai_rows = [{'answer_id': i, 'overall_pass': '1' if i in (dev[0], student[0]) else '0'} for i in self.ids]
        report = agreement.analyze({'G1': self.g1, 'G2': self.g2}, self.answers, ai_rows=ai_rows)
        groups = report['ai']
        self.assertEqual((groups['student']['n'], groups['dev']['n'], groups['all']['n']), (4, 3, 7))
        self.assertEqual((groups['student']['false_passes'], groups['dev']['false_passes']), (1, 1))
        self.assertAlmostEqual(groups['all']['false_pass_rate'], 2 / 7)
        self.assertEqual(len(groups['all']['false_pass_interval']), 2)
        # 최종 라벨이 모두 미통과(한 범주)라 kappa만으로는 부풀거나 0이 될 수 있어 일치율·민감도·특이도를 함께 냄
        self.assertAlmostEqual(groups['all']['agreement'], 5 / 7)
        self.assertIsNone(groups['all']['sensitivity'])
        self.assertAlmostEqual(groups['all']['specificity'], 5 / 7)
        self.assertEqual((groups['all']['passes'], groups['all']['fails']), (0, 7))
        self.assertEqual(list(report)[0], 'warnings')
        self.assertTrue(any('거짓 통과율 상한 기준 미달 가능' in w for w in report['warnings']))


class AiGradeTests(unittest.TestCase):
    # 역할: 운영 Gateway 대신 루브릭 길이에 맞춘 판정을 돌려주는 가짜; fail에 든 답 내용이면 첫 요소 미충족, broken이면 모델 실패
    class FakeGateway:
        def __init__(self, fail=(), broken=()):
            self.fail, self.broken, self.calls = set(fail), set(broken), []

        def grade_explanation(self, item, answer):
            self.calls.append(answer)
            if answer in self.broken:
                raise LLMUnavailable('모델 실패')
            return [answer not in self.fail] + [True] * (len(item.rubric) - 1)

    # 검증 목적: 서술 판정은 루브릭 요소가 모두 충족이어야 통과이고, 빈 답은 채점하지 않음
    def test_overall_requires_all_rubric(self):
        gateway = self.FakeGateway(fail={'답 1'})
        rows, failed = ai_grade.grade_answers(fake_answers(), ITEMS, gateway)
        by_id = {r['answer_id']: r for r in rows}
        self.assertEqual(len(rows), 12)
        self.assertEqual((by_id['a01']['overall_pass'], by_id['a01']['met_1']), ('0', '0'))
        self.assertEqual(by_id['a02']['overall_pass'], '1')
        self.assertEqual(failed, [])

    # 검증 목적: 이미 채점한 답은 다시 부르지 않고(중단 후 재개), 모델 실패 답은 결과에 쓰지 않고 따로 알려 다음 실행에서 다시 채점
    def test_resume_skips_graded_and_reports_failures(self):
        gateway = self.FakeGateway(broken={'답 3'})
        rows, failed = ai_grade.grade_answers(fake_answers(), ITEMS, gateway, done={'a00', 'a01'})
        self.assertNotIn('답 0', gateway.calls)
        self.assertEqual(failed, ['a03'])
        self.assertEqual(len(rows), 9)


if __name__ == '__main__':
    unittest.main()
