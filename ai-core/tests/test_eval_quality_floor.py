# 파일 역할: 품질 하한 시험 도구(eval/quality_floor.py)가 문항을 규칙대로 고르고, 보기만 조건에서 질문을 숨기며,
#            파싱 실패를 오답과 따로 세고, 반복 실행 결과를 평균·범위로 집계하는지 검증(모델 서버 없이 가짜 클라이언트 사용)
import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from eval import quality_floor


# 역할: 정해 둔 응답 문자열을 차례로 돌려주는 가짜 OpenAI 클라이언트; 받은 요청 인자는 calls에 남김
class FakeClient:
    def __init__(self, contents, finish_reason='stop'):
        self.contents, self.finish_reason, self.calls = list(contents), finish_reason, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.contents.pop(0))
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=self.finish_reason)])


def cs_row(id_, domain, format_, answer='A'):
    row = {'ID': id_, 'Domain': domain, 'SubDomain': 'x', 'Format': format_, 'Question': f'question {id_}', 'Answer': answer}
    if format_ == 'Multiple-choice':
        row.update({'A': 'opt a', 'B': 'opt b', 'C': 'opt c', 'D': 'opt d'})
    return row


MC = {'id': 1, 'area': 'dsa', 'format': 'multiple_choice', 'question': 'Which structure is LIFO?',
      'options': {'A': 'queue', 'B': 'stack', 'C': 'heap', 'D': 'tree'}, 'answer': 'B'}
ASSERTION = {'id': 2, 'area': 'os', 'format': 'assertion', 'question': 'A process can contain several threads.',
             'options': None, 'answer': 'True'}


class SelectionTest(unittest.TestCase):
    # 검증: 영역(자료구조·운영체제)과 형식(객관식·참거짓) 둘 다 맞는 행만 고르고, 영역·형식을 짧은 이름으로 바꿔 ID 순서로 냄
    # - 다른 영역(네트워크)이나 다른 형식(빈칸·서술)이 섞이면 공개한 860문항 규칙과 다른 세트를 재게 됨
    def test_cs_bench_selection_rule(self):
        rows = [cs_row(5, 'Operating System', 'Assertion', answer=True),
                cs_row(1, 'Data Structure and Algorithm', 'Multiple-choice'),
                cs_row(2, 'Data Structure and Algorithm', 'Fill-in-the-blank'),
                cs_row(3, 'Computer Network', 'Multiple-choice'),
                cs_row(4, 'Operating System', 'Open-ended'),
                cs_row(6, 'Computer Organization', 'Assertion', answer=False)]
        self.assertEqual(quality_floor.select_cs_bench(rows),
                         [{'id': 1, 'area': 'dsa', 'format': 'multiple_choice'},
                          {'id': 5, 'area': 'os', 'format': 'assertion'}])

    # 검증: KMMLU 문항 ID는 머리줄을 뺀 1부터의 행 번호이고, 정답 번호 1~4가 보기 기호 A~D로 바뀌며 수험생 정답률이 실수로 남음
    # - 콘텐츠 워커의 분류 목록(kmmlu.json)이 이 ID로 문항을 가리키므로 규칙이 바뀌면 분류가 다른 문항에 붙음
    def test_kmmlu_ids_and_answer_letters(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.csv'
            with open(path, 'w', encoding='utf-8', newline='') as file:
                writer = csv.writer(file)
                writer.writerow(['question', 'answer', 'A', 'B', 'C', 'D', 'Category', 'Human Accuracy'])
                writer.writerow(['q one', '2', 'a', 'b', 'c', 'd', 'Computer Science', '0.5'])
                writer.writerow(['q two', '4', 'a', 'b', 'c', 'd', 'Computer Science', '0.25'])
            items = quality_floor.load_kmmlu(path)
        self.assertEqual([item['id'] for item in items], ['kmmlu-cs-1', 'kmmlu-cs-2'])
        self.assertEqual([item['answer'] for item in items], ['B', 'D'])
        self.assertEqual(items[0]['options'], {'A': 'a', 'B': 'b', 'C': 'c', 'D': 'd'})
        self.assertEqual(items[1]['human_accuracy'], 0.25)


class RequestTest(unittest.TestCase):
    # 검증: 보기만 조건의 요청 어디에도 질문 문장이 없고 보기는 모두 들어감(Balepur 2024 방식)
    # - 질문이 새면 "보기만으로 맞히는 비율"이 아니라 보통 정답률을 재게 됨
    def test_options_only_removes_question(self):
        text = json.dumps(quality_floor.build_messages('cs_bench', MC, options_only=True), ensure_ascii=False)
        self.assertNotIn(MC['question'], text)
        for option in MC['options'].values():
            self.assertIn(option, text)
        normal = json.dumps(quality_floor.build_messages('cs_bench', MC, options_only=False), ensure_ascii=False)
        self.assertIn(MC['question'], normal)

    # 검증: 참거짓 문항은 질문을 지우면 남는 것이 없으므로 보기만 조건에서 빠짐
    def test_options_only_skips_assertion(self):
        self.assertEqual(quality_floor.condition_items([MC, ASSERTION], options_only=True), [MC])
        self.assertEqual(quality_floor.condition_items([MC, ASSERTION], options_only=False), [MC, ASSERTION])

    # 검증: 운영 설정(추론 끔, 온도 0)과 형식별 json_schema 선택지(A~D 또는 True/False)로 요청함
    def test_request_settings_and_schema(self):
        client = FakeClient(['{"answer": "B"}', '{"answer": "True"}'])
        quality_floor.ask(client, 'm', 'cs_bench', MC, options_only=False)
        quality_floor.ask(client, 'm', 'cs_bench', ASSERTION, options_only=False)
        first, second = client.calls
        self.assertEqual(first['temperature'], 0.0)
        self.assertEqual(first['extra_body']['chat_template_kwargs'], {'enable_thinking': False})
        enum = lambda call: call['response_format']['json_schema']['schema']['properties']['answer']['enum']
        self.assertEqual(enum(first), ['A', 'B', 'C', 'D'])
        self.assertEqual(enum(second), ['True', 'False'])

    # 검증: 기호 대신 문장을 내거나, 허용 밖 값을 내거나, 출력이 잘리면 오답이 아니라 parse_failure로 셈(Review Focus 5)
    # - 형식 실패를 오답에 섞으면 "모르는 것"과 "답 형식을 못 지킨 것"을 구분할 수 없음
    def test_parse_failure_counted_separately(self):
        outcomes = [quality_floor.ask(FakeClient([content]), 'm', 'cs_bench', MC, options_only=False)['outcome']
                    for content in ('{"answer": "B"}', '{"answer": "A"}', 'The answer is B.', '{"answer": "E"}')]
        self.assertEqual(outcomes, ['correct', 'wrong', 'parse_failure', 'parse_failure'])
        cut = quality_floor.ask(FakeClient(['{"answer": "B'], finish_reason='length'), 'm', 'cs_bench', MC, False)
        self.assertEqual(cut['outcome'], 'parse_failure')


# 역할: 집계 시험용 기록 한 줄; 실제 실행 기록과 같은 키만 채움
def record(run, id_, outcome, area='dsa', format_='multiple_choice', options_only=False, human=None):
    return {'set': 'kmmlu' if human is not None else 'cs_bench', 'run': run, 'options_only': options_only, 'id': id_,
            'area': area, 'format': format_, 'outcome': outcome, 'human_accuracy': human}


class RunAndSummaryTest(unittest.TestCase):
    # 검증: 회차별 정답률의 평균·최솟값·최댓값, 영역·형식별 칸, 파싱 실패율, 보기만 조건 분리를 계산함
    def test_summary_mean_and_range(self):
        records = [
            # 1회: 4문항 중 2개 정답 → 0.5 / 2회: 3개 → 0.75 / 3회: 4개 → 1.0
            record(1, 1, 'correct'), record(1, 2, 'wrong'), record(1, 3, 'parse_failure', area='os'),
            record(1, 4, 'correct', area='os', format_='assertion'),
            record(2, 1, 'correct'), record(2, 2, 'correct'), record(2, 3, 'wrong', area='os'),
            record(2, 4, 'correct', area='os', format_='assertion'),
            record(3, 1, 'correct'), record(3, 2, 'correct'), record(3, 3, 'correct', area='os'),
            record(3, 4, 'correct', area='os', format_='assertion'),
            record(1, 1, 'correct', options_only=True), record(1, 2, 'wrong', options_only=True),
        ]
        summary = quality_floor.summarize(records)
        overall = summary['cs_bench/standard']['overall']
        self.assertEqual((overall['items'], overall['runs']), (4, 3))
        self.assertAlmostEqual(overall['accuracy_mean'], 0.75)
        self.assertEqual((overall['accuracy_min'], overall['accuracy_max']), (0.5, 1.0))
        self.assertEqual(overall['parse_failures'], 1)
        self.assertAlmostEqual(overall['parse_failure_rate'], round(1 / 12, 4))
        os_cell = summary['cs_bench/standard']['by_area']['os']
        self.assertAlmostEqual(os_cell['accuracy_mean'], (0.5 + 0.5 + 1.0) / 3, places=4)
        self.assertEqual(summary['cs_bench/standard']['by_format']['assertion']['accuracy_mean'], 1.0)
        self.assertEqual(summary['cs_bench/options_only']['overall']['accuracy_mean'], 0.5)

    # 검증: 회차마다 답(파싱 실패는 None)이 달라진 문항 수를 셈; 온도 0이라 3회가 거의 같을 수 있어 반복의 의미를 함께 보이기 위함
    def test_summary_counts_items_changed_between_runs(self):
        records = [dict(record(run, id_, 'correct'), prediction=prediction)
                   for run, id_, prediction in ((1, 1, 'A'), (2, 1, 'A'), (1, 2, 'B'), (2, 2, 'C'), (1, 3, None), (2, 3, 'D'))]
        self.assertEqual(quality_floor.summarize(records)['cs_bench/standard']['overall']['changed_items'], 2)

    # 검증: KMMLU 칸에는 같은 문항의 수험생 정답률 평균이 나란히 붙음(회차가 여러 번이어도 문항당 한 번만 셈)
    def test_summary_human_accuracy(self):
        records = [record(run, id_, 'correct', human=human) for run in (1, 2) for id_, human in ((1, 0.2), (2, 0.6))]
        self.assertAlmostEqual(quality_floor.summarize(records)['kmmlu/standard']['overall']['human_accuracy_mean'], 0.4)

    # 검증: 중단 뒤 같은 출력 파일로 다시 실행하면 이미 기록한 (회차, 문항)은 다시 묻지 않고 남은 것만 이어서 씀
    # - 운영 8000에 수천 건을 순차로 보내므로 중간에 끊겨도 처음부터 다시 보내지 않기 위함
    def test_resume_skips_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'run.jsonl'
            quality_floor.run(FakeClient(['{"answer": "B"}']), 'm', 'cs_bench', [MC], runs=1, options_only=False, out=out)
            client = FakeClient(['{"answer": "False"}', '{"answer": "B"}', '{"answer": "True"}'])
            quality_floor.run(client, 'm', 'cs_bench', [MC, ASSERTION], runs=2, options_only=False, out=out)
            records = [json.loads(line) for line in out.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(sorted((r['run'], r['id']) for r in records), [(1, 1), (1, 2), (2, 1), (2, 2)])
        self.assertEqual({(r['run'], r['id']): r['outcome'] for r in records}[(1, 2)], 'wrong')


if __name__ == '__main__':
    unittest.main()
