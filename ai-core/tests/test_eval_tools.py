# 파일 역할: 측정 도구(한국어 답 수집 시트)가 채점 기준을 숨기고 원문을 Git 밖에만 쓰는지 검증
import csv
import tempfile
import unittest
from pathlib import Path

from eval import answer_sheet
from serving.item_bank import load_item_bank

EXPLANATIONS = [item for item in load_item_bank() if item.purpose == 'explanation']


# 역할: 시트 파일을 BOM을 뗀 UTF-8로 읽어 행 목록 반환
def read_sheet(path):
    with open(path, encoding='utf-8-sig', newline='') as file:
        return list(csv.DictReader(file))


class AnswerSheetTest(unittest.TestCase):
    # 역할: 실제 ai-core/outputs/ 안 임시 폴더에 시트를 만들고 끝나면 지움; 테스트가 outputs 밖 경로를 쓰지 않게 함
    def setUp(self):
        answer_sheet.OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=answer_sheet.OUTPUT_ROOT)
        self.out = Path(self.tmp.name) / 'answers.csv'

    def tearDown(self):
        self.tmp.cleanup()

    # 검증: 열 구성과 순서가 계약대로이고, 서술 문항마다 before·after 두 줄이 생기며, 답 칸은 비어 있음
    def test_columns_and_rows(self):
        answer_sheet.main(['--out', str(self.out)])
        rows = read_sheet(self.out)
        with open(self.out, encoding='utf-8-sig', newline='') as file:
            self.assertEqual(next(csv.reader(file)), list(answer_sheet.COLUMNS))
        self.assertEqual(list(answer_sheet.COLUMNS),
                         ['answer_id', 'item_id', 'concept', 'question', 'phase', 'author', 'answer'])
        self.assertEqual(len(rows), 2 * len(EXPLANATIONS))
        for item in EXPLANATIONS:
            mine = [row for row in rows if row['item_id'] == item.id]
            self.assertEqual([row['phase'] for row in mine], ['before', 'after'])
            self.assertTrue(all(row['question'] == item.question and row['concept'] == item.concept for row in mine))
        self.assertTrue(all(row['author'] == '' and row['answer'] == '' for row in rows))

    # 검증: answer_id가 8자리이고 겹치지 않음; 채점 단계에서 답을 문항·작성자와 떼어 가리킬 키로 쓰기 때문
    def test_answer_ids_unique(self):
        answer_sheet.main(['--out', str(self.out)])
        ids = [row['answer_id'] for row in read_sheet(self.out)]
        self.assertTrue(all(len(value) == 8 for value in ids))
        self.assertEqual(len(set(ids)), len(ids))

    # 검증: 엑셀에서 한글이 깨지지 않도록 파일이 UTF-8 BOM으로 시작함
    def test_utf8_bom(self):
        answer_sheet.main(['--out', str(self.out)])
        self.assertTrue(self.out.read_bytes().startswith(b'\xef\xbb\xbf'))

    # 검증: 루브릭 항목과 모범 근거 문장이 시트 어디에도 없음; 답 쓰는 사람이 채점 기준을 보고 쓰는 일을 막음
    def test_no_rubric_or_rationale(self):
        answer_sheet.main(['--out', str(self.out)])
        text = self.out.read_text(encoding='utf-8-sig')
        for item in EXPLANATIONS:
            self.assertNotIn(item.rationale, text)
            for criterion in item.rubric:
                self.assertNotIn(criterion, text)
        self.assertNotIn('rubric', text)
        self.assertNotIn('rationale', text)

    # 검증: --concepts로 고른 개념의 서술 문항만 들어감
    def test_concepts_filter(self):
        answer_sheet.main(['--out', str(self.out), '--concepts', 'stack', 'queue'])
        rows = read_sheet(self.out)
        self.assertEqual({row['concept'] for row in rows}, {'stack', 'queue'})
        self.assertEqual(len(rows), 4)

    # 검증: 서술 문항이 없는 개념 이름(오타 포함)은 빈 시트 대신 오류로 알림
    def test_unknown_concept_rejected(self):
        with self.assertRaises(SystemExit):
            answer_sheet.main(['--out', str(self.out), '--concepts', 'stak'])
        self.assertFalse(self.out.exists())

    # 검증: 이미 있는 시트를 다시 만들라고 하면 거부하고 내용을 그대로 둠; 사람이 채운 답 원문은 Git 밖이라 되살릴 수 없기 때문
    def test_refuses_to_overwrite_existing_sheet(self):
        self.out.write_text('filled', encoding='utf-8')
        with self.assertRaises(SystemExit):
            answer_sheet.main(['--out', str(self.out)])
        self.assertEqual(self.out.read_text(encoding='utf-8'), 'filled')

    # 검증: ai-core/outputs/ 밖 경로와 ../로 빠져나가는 경로를 거부하고 파일을 만들지 않음; 답 원문이 Git에 들어가는 일을 막음
    def test_rejects_path_outside_outputs(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / 'answers.csv'
            with self.assertRaises(SystemExit):
                answer_sheet.main(['--out', str(outside)])
            self.assertFalse(outside.exists())
        escape = answer_sheet.OUTPUT_ROOT / '..' / 'content' / 'answers.csv'
        with self.assertRaises(SystemExit):
            answer_sheet.main(['--out', str(escape)])
        self.assertFalse(escape.exists())


if __name__ == '__main__':
    unittest.main()
