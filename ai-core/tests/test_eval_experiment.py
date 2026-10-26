# 파일 역할: 비교 실험 결과 계산(eval/experiment_report.py)이 서술 답 채점 시트를 눈가림으로 만들고,
#            학생별 O1~O3 표와 조건별 차이(system − chat) 요약을 바르게 내는지 검증
# - 실험 API·자료가 아직 구현 중이라, 계획 Task 1·2 Interfaces 형식의 가짜 자료와 가짜 기록 3명(S1~S3)을 씀
import csv
import tempfile
import unittest
from pathlib import Path

from eval import experiment_report as report
from eval.grading_sheet import OUTPUT_ROOT, SHEET_COLUMNS


# 역할: 개념 하나의 가짜 자료; 형(A/B)마다 선택형 1·단답 1·서술 1(루브릭 2개)
def concept(cid):
    def form(f):
        return [{'id': f'exp-{cid}-{f}1', 'format': 'multiple_choice', 'question': f'{cid} {f} q1',
                 'options': {'A': 'a', 'B': 'b', 'C': 'c', 'D': 'd'}, 'answer': 'B', 'rationale': 'r'},
                {'id': f'exp-{cid}-{f}2', 'format': 'short_exact', 'question': f'{cid} {f} q2', 'answer': '3,1', 'rationale': 'r'},
                {'id': f'exp-{cid}-{f}3', 'format': 'explanation', 'question': f'{cid} {f} 설명하라',
                 'rationale': 'r', 'rubric': [f'{cid} 요소1', f'{cid} 요소2']}]
    return {'id': cid, 'title': cid.upper(), 'passage': {'part1': 'p1', 'part2': 'p2'}, 'pre_post': {'A': form('A'), 'B': form('B')}}


MATERIALS = {'version': 1, 'concepts': [concept('heap'), concept('bst'), concept('bfs')]}


def at(minute, second=0):
    return f'2026-11-10T10:{minute:02d}:{second:02d}Z'


# 역할: 학습 단계 하나; help·cont는 도움 요청·계속 읽기 시각(분, 초), None이면 그 사건 없음
def slot(n, cid, condition, help_at=None, cont_at=None, timeout=False):
    events = []
    if help_at:
        events.append({'type': 'help_open', 'at': at(*help_at)})
        events.append({'type': 'stuck_session' if condition == 'system' else 'chat_conversation', 'ref': 'x', 'at': at(*help_at)})
    if cont_at:
        events.append({'type': 'continue_reading', 'at': at(*cont_at)})
    if timeout:
        events.append({'type': 'timeout', 'at': at(30)})
    return {'slot': n, 'concept': cid, 'condition': condition, 'started_at': at(0), 'deadline': at(15), 'events': events}


# 역할: 가짜 실험 기록 3명
# - S1(홀수: 개념 1 system, A형 사전): heap 사전 0 → 사후 2, bst 사전 1 → 사후 1
# - S2(짝수: 개념 1 chat, B형 사전): bst 사전 0 → 사후 1, heap 사전 0 → 사후 2; heap은 시간 초과(O2 미복귀)
# - S3(사전 점수 높음): 사후 시험 전(posttest 없음)
def export():
    def answers(form, spec):  # spec: {concept: (q1 답, q2 답, 서술 답)}
        out = {}
        for cid, (a1, a2, a3) in spec.items():
            out.update({f'exp-{cid}-{form}1': a1, f'exp-{cid}-{form}2': a2})
            if a3 is not None:
                out[f'exp-{cid}-{form}3'] = a3
        return out
    s1 = {'id': '1' * 32, 'participant': 'S1', 'grade': 3, 'dsa': 'taken', 'exclude': [], 'stage': 'done',
          'pretest_form': 'A', 'posttest_form': 'B', 'pretest_high': False,
          'pretest': {'answers': answers('A', {'heap': ('A', '9', None), 'bst': ('B', '9', None), 'bfs': ('B', '3,1', None)}),
                      'submitted_at': at(0)},
          'learn': [slot(1, 'heap', 'system', help_at=(2, 0), cont_at=(6, 30)), slot(2, 'bst', 'chat', help_at=(3, 0), cont_at=(5, 0))],
          'posttest': {'answers': answers('B', {'heap': ('b', ' 3, 1 ', 'S1 힙 설명'), 'bst': ('B', '0', 'S1 BST 설명')}),
                       'submitted_at': at(40)},
          'survey': {'answers': {'q1': 4}}}
    s2 = {'id': '2' * 32, 'participant': 'S2', 'grade': 2, 'dsa': 'taking', 'exclude': ['bfs'], 'stage': 'done',
          'pretest_form': 'B', 'posttest_form': 'A', 'pretest_high': False,
          'pretest': {'answers': answers('B', {'heap': ('A', '0', None), 'bst': ('C', '0', None)}), 'submitted_at': at(0)},
          'learn': [slot(1, 'bst', 'chat', help_at=(1, 0), cont_at=(4, 0)), slot(2, 'heap', 'system', help_at=(2, 0), timeout=True)],
          'posttest': {'answers': answers('A', {'bst': ('B', '0', 'S2 BST 설명'), 'heap': ('B', '3,1', '')}),
                       'submitted_at': at(40)},
          'survey': None}
    s3 = {'id': '3' * 32, 'participant': 'S3', 'grade': 4, 'dsa': 'taken', 'exclude': [], 'stage': 'posttest',
          'pretest_form': 'A', 'posttest_form': 'B', 'pretest_high': True,
          'pretest': {'answers': answers('A', {'heap': ('B', '3,1', None), 'bst': ('B', '3,1', None), 'bfs': ('B', '9', None)}),
                      'submitted_at': at(0)},
          'learn': [slot(1, 'bfs', 'system', cont_at=(3, 0)), slot(2, 'heap', 'chat', help_at=(1, 0), cont_at=(2, 0))],
          'posttest': None, 'survey': None}
    return {'exported_at': at(59), 'sessions': [s1, s2, s3]}


# 역할: 채점자 시트 하나를 흉내 냄; marks: {(세션 id, 문항 id): (met_1, met_2)} → '1'/'0'/'' 칸
def graded(marks):
    rows = []
    for (sid, item_id), met in marks.items():
        row = {column: '' for column in SHEET_COLUMNS}
        row.update(answer_id=report.answer_id(sid, item_id), practice='0')
        for k, value in enumerate(met, 1):
            row[f'met_{k}'] = '' if value is None else str(int(value))
        row['overall_pass'] = '' if None in met else str(int(all(met)))
        rows.append(row)
    return rows


S1, S2 = '1' * 32, '2' * 32


class GradingSheetTest(unittest.TestCase):
    # 검증: 사후 서술 답 중 빈 답을 뺀 것만 모으고, 열은 MVP 3 채점 시트와 같으며, 참가자·조건·개념·문항 ID가 시트에 없음(눈가림)
    # - 채점자가 조건(제안 시스템/채팅)을 알면 판정이 한쪽으로 기울 수 있기 때문
    def test_sheet_blinds_participant_condition_concept(self):
        rows = report.build_grading_sheet(export(), MATERIALS, 'G1')
        self.assertEqual(sorted(r['answer'] for r in rows), ['S1 BST 설명', 'S1 힙 설명', 'S2 BST 설명'])
        self.assertTrue(all(list(r) == list(SHEET_COLUMNS) for r in rows))
        text = repr(rows)
        for hidden in ('S3', 'system', 'chat', 'exp-heap', 'exp-bst', S1, S2):
            self.assertNotIn(hidden, text.replace('S1 ', '').replace('S2 ', ''))
        heap = next(r for r in rows if r['answer'] == 'S1 힙 설명')
        self.assertEqual(heap['answer_id'], report.answer_id(S1, 'exp-heap-B3'))
        # 문자 접두 'X' + 16진수 8자리: '1e5'·'00123' 같은 값을 엑셀이 숫자로 바꿔 ID가 깨지지 않게 함(PR #69와 같은 이유)
        self.assertRegex(heap['answer_id'], r'^X[0-9a-f]{8}$')
        self.assertEqual((heap['rubric_1'], heap['rubric_2'], heap['rubric_3']), ('heap 요소1', 'heap 요소2', ''))
        self.assertTrue(all(r['practice'] == '0' and r['met_1'] == '' for r in rows))

    # 검증: 채점자마다 섞는 순서가 다르고, 같은 채점자는 다시 만들어도 같음(MVP 3 채점 시트와 같은 seed 규칙)
    def test_sheet_order_per_grader(self):
        ids = lambda grader: [r['answer_id'] for r in report.build_grading_sheet(export(), MATERIALS, grader)]
        self.assertEqual(ids('G1'), ids('G1'))
        self.assertEqual(sorted(ids('G1')), sorted(ids('G2')))

    # 검증: 명령행 grading-sheet가 ai-core/outputs 아래에 시트를 쓰고, 밖 경로는 거부함(학생 답 원문이 Git에 들어가지 않게)
    def test_sheet_cli_writes_under_outputs(self):
        OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=OUTPUT_ROOT) as tmp:
            exp, mat = Path(tmp) / 'export.json', Path(tmp) / 'materials.json'
            exp.write_text(report.json.dumps(export()), encoding='utf-8')
            mat.write_text(report.json.dumps(MATERIALS), encoding='utf-8')
            out = Path(tmp) / 'grading-G1.csv'
            report.main(['grading-sheet', '--export', str(exp), '--materials', str(mat), '--grader', 'G1', '--out', str(out)])
            with open(out, encoding='utf-8-sig', newline='') as file:
                self.assertEqual(len(list(csv.DictReader(file))), 3)
            with self.assertRaises(SystemExit):
                report.main(['grading-sheet', '--export', str(exp), '--materials', str(mat), '--grader', 'G1',
                             '--out', '/tmp/outside.csv'])


class StudentTableTest(unittest.TestCase):
    def rows(self, sheets=()):
        return {(r['participant'], r['concept']): r for r in report.student_rows(export(), MATERIALS, list(sheets))}

    # 검증: 개념별 사전·사후 자동 채점 정답 수와 변화; 사전은 사전 형, 사후는 사후 형 문항으로 채점(서술은 빼고 2문항)
    # - 선택형은 대소문자 무시, 단답은 공백 무시(운영 채점기와 같은 규칙)
    def test_pre_post_scores_and_change(self):
        rows = self.rows()
        self.assertEqual({k: (r['pre'], r['post'], r['change']) for k, r in rows.items() if k[0] != 'S3'},
                         {('S1', 'heap'): (0, 2, 2), ('S1', 'bst'): (1, 1, 0),
                          ('S2', 'bst'): (0, 1, 1), ('S2', 'heap'): (0, 2, 2)})
        self.assertEqual((rows[('S1', 'heap')]['condition'], rows[('S2', 'heap')]['condition']), ('system', 'system'))

    # 검증: O2는 계속 읽기를 눌렀는지, O3는 첫 도움 요청부터 계속 읽기까지 초
    # - 도움 없이 계속 읽었거나(S3 bfs) 시간 초과(S2 heap)면 O3는 없음(None); 0초로 세면 "빨리 복귀"로 잘못 읽힘
    def test_o2_o3(self):
        rows = self.rows()
        self.assertEqual((rows[('S1', 'heap')]['o2'], rows[('S1', 'heap')]['o3_seconds']), (True, 270))
        self.assertEqual((rows[('S2', 'heap')]['o2'], rows[('S2', 'heap')]['o3_seconds']), (False, None))
        self.assertEqual((rows[('S3', 'bfs')]['o2'], rows[('S3', 'bfs')]['o3_seconds'], rows[('S3', 'bfs')]['helped']),
                         (True, None, False))

    # 검증: 사후 시험 전인 학생은 사후·변화·서술이 None이고, 사전 점수 높음 표시가 행마다 붙음
    def test_missing_posttest_and_high_flag(self):
        rows = self.rows()
        self.assertEqual((rows[('S3', 'heap')]['post'], rows[('S3', 'heap')]['change'], rows[('S3', 'heap')]['explanation']),
                         (None, None, None))
        self.assertTrue(rows[('S3', 'heap')]['pretest_high'])
        self.assertFalse(rows[('S1', 'heap')]['pretest_high'])

    # 검증: 서술 점수는 루브릭 충족 비율이고 채점자가 둘이면 평균; 빈 답은 채점 없이 0
    # - 빈 답(S2 heap)은 시트에 나가지 않으므로, 채점이 없다고 None으로 두면 모른 답이 계산에서 빠져 점수가 부풀기 때문
    # - 전체 판정(overall_pass)을 비운 행(S2 bst)은 채점이 끝나지 않은 답이라 None; MVP 3 일치도 계산(agreement.labels)과 같은 규칙
    def test_explanation_ratio_from_sheets(self):
        g1 = graded({(S1, 'exp-heap-B3'): (True, True), (S1, 'exp-bst-B3'): (True, False), (S2, 'exp-bst-A3'): (False, None)})
        g2 = graded({(S1, 'exp-heap-B3'): (True, False), (S1, 'exp-bst-B3'): (True, False)})
        rows = self.rows([g1, g2])
        self.assertAlmostEqual(rows[('S1', 'heap')]['explanation'], 0.75)
        self.assertAlmostEqual(rows[('S1', 'bst')]['explanation'], 0.5)
        self.assertIsNone(rows[('S2', 'bst')]['explanation'])
        self.assertEqual(rows[('S2', 'heap')]['explanation'], 0.0)
        self.assertIsNone(self.rows()[('S1', 'heap')]['explanation'])


class SummaryTest(unittest.TestCase):
    # 검증: 학생마다 차이(system − chat)를 내고 중앙값·최솟값·최댓값·학생 수를 계산; 한쪽 값이 없는 학생은 그 지표에서 빠짐
    # - 사전 점수 높음 학생(S3)은 전체와 "제외" 두 묶음으로 따로 보임(Review Focus 3)
    def test_condition_differences(self):
        result = report.build_report(export(), MATERIALS, [])
        change = result['summary']['all']['change']
        # S1: heap(system) 2 − bst(chat) 0 = 2, S2: heap(system) 2 − bst(chat) 1 = 1, S3: 사후 없음 → 제외
        self.assertEqual((change['n'], change['median'], change['min'], change['max']), (2, 1.5, 1, 2))
        self.assertEqual(sorted(change['by_student']), ['S1', 'S2'])
        o2 = result['summary']['all']['o2']
        self.assertEqual((o2['n'], o2['median']), (3, 0.0))  # S1 1−1, S2 0−1, S3 1−1
        o3 = result['summary']['all']['o3_seconds']
        self.assertEqual((o3['n'], o3['by_student']), (1, {'S1': 150}))  # S1 270−120; S2·S3는 한쪽이 None
        self.assertEqual(result['summary']['excluding_pretest_high']['o2']['n'], 2)
        self.assertEqual(result['pretest_high'], ['S3'])
        self.assertEqual(result['students'], 3)

    # 검증: 채점자 두 명이면 사후 서술 전체 판정의 채점자 간 kappa와 일치율, 비교한 답 수를 냄(metrics.cohen_kappa 사용)
    def test_grader_agreement(self):
        g1 = graded({(S1, 'exp-heap-B3'): (True, True), (S1, 'exp-bst-B3'): (True, False), (S2, 'exp-bst-A3'): (False, False)})
        g2 = graded({(S1, 'exp-heap-B3'): (True, True), (S1, 'exp-bst-B3'): (False, False), (S2, 'exp-bst-A3'): (False, False)})
        agreement = report.build_report(export(), MATERIALS, [g1, g2])['grader_agreement']
        self.assertEqual(agreement['compared'], 3)
        self.assertAlmostEqual(agreement['agreement'], 1.0)
        self.assertAlmostEqual(agreement['kappa'], 1.0)
        self.assertIsNone(report.build_report(export(), MATERIALS, [g1])['grader_agreement'])

    # 검증: 명령행 report가 채운 시트를 읽어 outputs 아래에 보고서를 쓰고, 같은 경로에 다시 쓰지 않음(이전 보고서 보호)
    # - 보고서에는 학생 답 원문이 들어가지 않음(수치와 코드만)
    def test_report_cli(self):
        OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=OUTPUT_ROOT) as tmp:
            exp, mat, sheet = Path(tmp) / 'export.json', Path(tmp) / 'materials.json', Path(tmp) / 'G1.csv'
            exp.write_text(report.json.dumps(export()), encoding='utf-8')
            mat.write_text(report.json.dumps(MATERIALS), encoding='utf-8')
            with open(sheet, 'w', encoding='utf-8-sig', newline='') as file:
                writer = csv.DictWriter(file, fieldnames=SHEET_COLUMNS)
                writer.writeheader()
                writer.writerows(graded({(S1, 'exp-heap-B3'): (True, False)}))
            out = Path(tmp) / 'report.json'
            args = ['report', '--export', str(exp), '--materials', str(mat), '--grades', str(sheet), '--out', str(out)]
            report.main(args)
            saved = report.json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(next(r for r in saved['rows'] if r['participant'] == 'S1' and r['concept'] == 'heap')['explanation'], 0.5)
            self.assertNotIn('S1 힙 설명', out.read_text(encoding='utf-8'))
            with self.assertRaises(SystemExit):
                report.main(args)


if __name__ == '__main__':
    unittest.main()
