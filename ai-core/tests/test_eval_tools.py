# 파일 역할: 측정 도구(한국어 답 수집 시트, 장면 선택 성공률)가 채점 기준을 숨기고, 결과를 Git 밖에만 쓰며, 결과를 바르게 분류·집계하는지 검증
import csv
import tempfile
import unittest
from pathlib import Path

from eval import animation_choice, answer_sheet
from serving.animation import CONCEPT_SCENES
from serving.animation.model import AnimationRequestModel
from serving.item_bank import load_item_bank
from serving.stuck import LLMUnavailable

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



# 역할: 장면 선택 가짜 Gateway; 개념마다 정한 행동(장면 dict·None·예외)을 돌려주고 받은 입력을 기록
class FakeChooser:
    def __init__(self, behaviors):
        self.behaviors, self.calls = behaviors, []

    def choose_animation(self, concept, entry, kinds):
        self.calls.append((concept.id, entry, kinds))
        behavior = self.behaviors.get(concept.id)
        if isinstance(behavior, Exception):
            raise behavior
        return None if behavior is None else AnimationRequestModel.model_validate(behavior).root


class AnimationChoiceTest(unittest.TestCase):
    # 검증: 시도 결과를 성공·null 선택·스키마 실패·시뮬레이션 실패 네 가지로 나누고, 지연과 원인(요청·오류)을 남김
    def test_measure_classifies_outcomes(self):
        chooser = FakeChooser({
            'stack': {'kind': 'stack', 'ops': [{'op': 'push', 'value': 1}, {'op': 'pop'}]},
            'queue': None,
            'heap': LLMUnavailable('모델 응답을 받지 못했습니다.'),
            'array': {'kind': 'array', 'values': [1, 2], 'ops': [{'op': 'delete', 'index': 5}]},
        })
        results = animation_choice.measure(chooser, runs=1, concepts=['stack', 'queue', 'heap', 'array'])
        outcome = {(r['concept'], r['entry']): r['outcome'] for r in results}
        self.assertEqual(outcome, {(c, e): o for c, o in (('stack', 'success'), ('queue', 'null'), ('heap', 'schema_failure'),
                                                           ('array', 'simulation_failure')) for e in ('roadmap', 'excerpt')})
        failed = next(r for r in results if r['outcome'] == 'simulation_failure')
        self.assertEqual(failed['request']['kind'], 'array')
        self.assertIn('범위', failed['error'])
        self.assertTrue(all(r['seconds'] >= 0 and r['kinds'] == list(CONCEPT_SCENES[r['concept']]) for r in results))

    # 검증: 개념마다 로드맵·붙여넣기 진입을 하나씩, runs 횟수만큼 차례로 시도하고, 붙여넣기 문장에 문항·정답이 들어가지 않음
    def test_measure_entries_and_runs(self):
        chooser = FakeChooser({})
        results = animation_choice.measure(chooser, runs=2, concepts=['stack'])
        self.assertEqual([(r['run'], r['entry']) for r in results], [(1, 'roadmap'), (1, 'excerpt'), (2, 'roadmap'), (2, 'excerpt')])
        roadmap, excerpt = chooser.calls[0][1], chooser.calls[1][1]
        self.assertEqual((roadmap.kind, roadmap.concept), ('roadmap', 'stack'))
        self.assertEqual(excerpt.kind, 'excerpt')
        for item in load_item_bank():
            self.assertNotIn(item.question, excerpt.excerpt)
        self.assertEqual(set(animation_choice.measure(FakeChooser({}), runs=1)[i]['concept'] for i in range(2 * len(CONCEPT_SCENES))),
                         set(CONCEPT_SCENES))

    # 검증: 네 분류의 개수·성공률과 장면 종류별 성공률, 평균·최대 지연을 집계
    def test_summarize(self):
        rows = [('stack', 'stack', 'success', 1.0), ('stack', 'stack', 'null', 3.0), ('heap', 'heap', 'schema_failure', 2.0),
                ('array', 'array', 'simulation_failure', 0.5), ('heap', 'heap', 'success', 1.5)]
        results = [{'concept': c, 'kinds': [k], 'outcome': o, 'seconds': t} for c, k, o, t in rows]
        summary = animation_choice.summarize(results)
        self.assertEqual(summary['counts'], {'success': 2, 'null': 1, 'schema_failure': 1, 'simulation_failure': 1})
        self.assertEqual((summary['total'], summary['success_rate']), (5, 0.4))
        self.assertEqual(summary['by_kind']['stack'], {'total': 2, 'success': 1, 'success_rate': 0.5})
        self.assertEqual(summary['by_kind']['array']['success_rate'], 0.0)
        self.assertEqual((summary['seconds_mean'], summary['seconds_max']), (1.6, 3.0))
        self.assertEqual(animation_choice.summarize([])['success_rate'], 0.0)

    # 검증: ai-core/outputs/eval/ 밖 경로와 이미 있는 결과 파일은 모델을 부르기 전에 거부하고 파일을 만들지 않음
    def test_rejects_bad_out_before_calling_model(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / 'choice.json'
            with self.assertRaises(SystemExit):
                animation_choice.main(['--base-url', 'http://127.0.0.1:21019/v1', '--out', str(outside)])
            self.assertFalse(outside.exists())
        not_eval = answer_sheet.OUTPUT_ROOT / 'choice.json'
        with self.assertRaises(SystemExit):
            animation_choice.main(['--base-url', 'http://127.0.0.1:21019/v1', '--out', str(not_eval)])
        self.assertFalse(not_eval.exists())
        animation_choice.EVAL_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=animation_choice.EVAL_ROOT) as folder:
            existing = Path(folder) / 'choice.json'
            existing.write_text('kept', encoding='utf-8')
            with self.assertRaises(SystemExit):
                animation_choice.main(['--base-url', 'http://127.0.0.1:21019/v1', '--out', str(existing)])
            self.assertEqual(existing.read_text(encoding='utf-8'), 'kept')

if __name__ == '__main__':
    unittest.main()
