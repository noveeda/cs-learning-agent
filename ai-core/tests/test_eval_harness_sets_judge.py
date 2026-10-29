## @file
# @brief 하네스 측정 세트 생성기(범위·판정·채점, eval.harness_sets_judge)가 정답이 생성 규칙에서 정해지도록 세트를 만드는지 검증
# - 모델(26B) 대신 결정적 가짜 생성 함수를 넣어, 개수·종류·정답·seed 재현성만 확인함
import random
import tempfile
import unittest
from pathlib import Path

from eval.harness_sets_judge import (grading_set, judge_sheet, negate, number_perturbation, perturbation_set, scope_set,
                                     verified_rows_from_logs)
from serving.concept_map import load_concept_map

CONCEPT_MAP = load_concept_map()


## @brief 종류·개념·개수를 그대로 담은 가짜 질문을 만들고 호출을 기록하는 생성 함수
class FakeWriter:
    def __init__(self):
        self.calls = []

    def __call__(self, kind, concepts, n):
        self.calls.append((kind, tuple(c.id for c in concepts), n))
        return [f'{kind}:{"+".join(c.id for c in concepts)}:{i}' for i in range(n)]


class ScopeSetTests(unittest.TestCase):
    # 검증 목적: 종류별 개수(개념당 직접 질문, 비교, 지도 밖·그중 코드 요청, 후속)와 정답 형식이 생성 규칙대로인지 확인
    def test_scope_set_kinds_and_counts(self):
        rows = scope_set(CONCEPT_MAP, FakeWriter(), per_concept=2, compares=10, outside=6, code_requests=2, follow_ups=5)
        by_kind = {k: [r for r in rows if r['kind'] == k] for k in ('direct', 'compare', 'outside', 'follow_up')}
        self.assertEqual({k: len(v) for k, v in by_kind.items()},
                         {'direct': 2 * len(CONCEPT_MAP.concepts), 'compare': 10, 'outside': 6, 'follow_up': 5})
        self.assertEqual(len({r['id'] for r in rows}), len(rows))
        direct = by_kind['direct'][0]
        self.assertEqual((direct['expected'], direct['previous']), ([direct['question'].split(':')[1]], []))
        self.assertTrue(all(r['expected'] == [] and r['previous'] == [] for r in by_kind['outside']))
        self.assertEqual(sum(r.get('variant') == 'code' for r in by_kind['outside']), 2)
        for row in by_kind['follow_up']:
            self.assertEqual(len(row['previous']), 1)
            self.assertEqual(row['expected'], row['previous'])
        self.assertEqual(rows, scope_set(CONCEPT_MAP, FakeWriter(), per_concept=2, compares=10, outside=6,
                                         code_requests=2, follow_ups=5))

    # 검증 목적: 제외 개념 추가 세트는 고른 개념의 직접 질문만 담고, ID가 기존 범위 세트(scope-0000…)와 겹치지 않아야
    # 측정 도구가 두 세트를 한 rows.jsonl에 기록할 때 (task, id)로 건너뛰는 일이 없음
    def test_scope_set_only_concepts(self):
        rows = scope_set(CONCEPT_MAP, FakeWriter(), per_concept=3, compares=0, outside=0, code_requests=0, follow_ups=0,
                         only={'bfs', 'stack'}, id_prefix='holdout-scope')
        self.assertEqual(len(rows), 6)
        self.assertEqual({r['expected'][0] for r in rows}, {'bfs', 'stack'})
        self.assertTrue(all(r['kind'] == 'direct' and r['id'].startswith('holdout-scope-') for r in rows))

    # 검증 목적: 비교 질문은 선수 관계로 이어진 개념 쌍에서만 만들어 "스택과 큐 차이"처럼 실제로 함께 배우는 쌍이 되게 함
    def test_scope_compare_pairs_are_prerequisites(self):
        edges = {(c.id, p.id) for c in CONCEPT_MAP.concepts for p in c.prerequisites}
        rows = scope_set(CONCEPT_MAP, FakeWriter(), per_concept=1, compares=20, outside=3, code_requests=1, follow_ups=1)
        compares = [r for r in rows if r['kind'] == 'compare']
        self.assertEqual(len(compares), 20)
        self.assertEqual(len({tuple(r['expected']) for r in compares}), 20)
        self.assertTrue(all(tuple(r['expected']) in edges for r in compares))


VERIFIED = [{'id': f'v{i}', 'sentence': s, 'passage': p} for i, (s, p) in enumerate([
    ('스택은 마지막에 넣은 원소를 먼저 꺼낸다.', 'A stack removes the most recently added element.'),
    ('라운드 로빈은 시간 조각을 4밀리초로 둔다.', 'The time slice is 4 ms.'),
    ('해시 테이블은 버킷 10개를 쓴다.', 'The table has 10 buckets.'),
    ('큐는 가장 오래된 원소를 먼저 꺼낸다.', 'A queue removes the oldest element.'),
    ('힙은 완전 이진 트리이다.', 'A heap is a complete binary tree.'),
    ('연결 리스트는 포인터로 다음 노드를 가리킨다.', 'Each node points to the next.'),
    ('TCP는 연결을 먼저 맺는다.', 'TCP sets up a connection first.'),
    ('페이지 크기는 4096바이트이다.', 'Pages are 4096 bytes.'),
])]


class PerturbationTests(unittest.TestCase):
    # 검증 목적: 맞는 문장과 망가뜨린 문장이 반반이고, 망가뜨린 문장은 모두 원래 문장과 달라 정답(unsupported)이 생성 방식에서 정해짐
    def test_perturbation_balanced_and_changed(self):
        def perturb(kind, row):
            return row['sentence'] + ' 이 방식은 1970년에 처음 제안되었다.' if kind == 'addition' else None

        rows = perturbation_set(VERIFIED, perturb, seed=3)
        supported = [r for r in rows if r['label'] == 'supported']
        unsupported = [r for r in rows if r['label'] == 'unsupported']
        self.assertEqual(len(supported), len(unsupported))
        self.assertGreater(len(unsupported), 0)
        originals = {v['passage']: v['sentence'] for v in VERIFIED}
        for row in unsupported:
            self.assertNotEqual(row['sentence'], originals[row['passage']])
            self.assertIn(row['perturbation'], ('number', 'negation', 'addition', 'swap_concept'))
        self.assertTrue(all(r['perturbation'] is None and r['sentence'] == originals[r['passage']] for r in supported))
        self.assertEqual(rows, perturbation_set(VERIFIED, perturb, seed=3))

    # 검증 목적: 모델 교란 결과가 ':'처럼 내용이 없으면 버리고 다음 방식을 시도하며, 앞의 ':'만 붙은 문장은 떼어 냄
    # - 실측: 26B에 strict JSON schema를 걸면 {"sentence":": "}를 내는 경우가 많았음
    # - 숫자가 있는 문장은 숫자 바꾸기를 먼저 시도함(가장 확실하게 틀린 문장이 되는 방식)
    def test_perturbation_rejects_contentless_output(self):
        def perturb(kind, row):
            return ':' if kind == 'swap_concept' else ': ' + row['sentence'] + ' 이 방식은 1970년에 처음 제안되었다.'

        rows = perturbation_set(VERIFIED, perturb, seed=1)
        unsupported = [r for r in rows if r['label'] == 'unsupported']
        self.assertTrue(all(len(r['sentence']) > 5 and not r['sentence'].startswith(':') for r in rows))
        self.assertNotIn('swap_concept', {r['perturbation'] for r in unsupported})
        originals = {v['passage']: v['sentence'] for v in VERIFIED}
        with_number = [r for r in unsupported if any(ch.isdigit() for ch in originals[r['passage']])]
        self.assertTrue(with_number)
        self.assertTrue(all(r['perturbation'] == 'number' for r in with_number))

    # 검증 목적: 숫자 바꾸기는 문장의 숫자 하나를 근거 문단에 없는 다른 값으로 바꾸고, 숫자가 없으면 None
    def test_number_perturbation_changes_a_number(self):
        changed = number_perturbation('시간 조각은 4밀리초이다.', 'The time slice is 4 ms, not 5 ms.', random.Random(0))
        self.assertNotEqual(changed, '시간 조각은 4밀리초이다.')
        number = ''.join(ch for ch in changed if ch.isdigit())
        self.assertNotIn(number, ('4', '5'))
        self.assertIsNone(number_perturbation('숫자가 없다.', '', random.Random(0)))
        # 'L1'·'IPv4'처럼 영문자에 붙은 숫자는 값이 아니라 이름이라 바꾸지 않음
        self.assertEqual(number_perturbation('L1 캐시는 4개 있다.', 'four', random.Random(0)), 'L1 캐시는 5개 있다.')
        self.assertIsNone(number_perturbation('IPv4 주소를 쓴다.', '', random.Random(0)))

    # 검증 목적: 부정 규칙이 문장 끝 서술어를 반대로 바꾸고(받침에 맞는 조사 포함), 바꿀 서술어가 없으면 None
    def test_negation_flips_predicate(self):
        self.assertEqual(negate('힙은 완전 이진 트리이다.'), '힙은 완전 이진 트리가 아니다.')
        self.assertEqual(negate('스택은 원소를 꺼낼 수 있다.'), '스택은 원소를 꺼낼 수 없다.')
        self.assertEqual(negate('TCP는 연결을 먼저 맺는다.'), None)
        self.assertEqual(negate('큐는 원소를 먼저 꺼냅니다.'), None)
        self.assertEqual(negate('페이지는 고정 크기를 사용한다.'), '페이지는 고정 크기를 사용하지 않는다.')

    # 검증 목적: 하네스 기록에서 verified이고 근거 문단이 하나인 문장만 맞는 문장 후보로 뽑고, 문단 원문을 붙임
    def test_verified_rows_from_logs(self):
        records = [{'passages': [{'key': 'p1', 'id': 'ods:a:0'}, {'key': 'p2', 'id': 'ods:b:0'}],
                    'sentences': [{'text': '맞음.', 'sources': ['p1'], 'status': 'verified'},
                                  {'text': '둘.', 'sources': ['p1', 'p2'], 'status': 'verified'},
                                  {'text': '확인 필요.', 'sources': ['p2'], 'status': 'needs_check'}]},
                   {'outcome': 'blocked'}]
        rows = verified_rows_from_logs(records, {'ods:a:0': 'Text A', 'ods:b:0': 'Text B'}.get)
        self.assertEqual(rows, [{'id': 'v0000', 'sentence': '맞음.', 'passage': 'Text A', 'passage_id': 'ods:a:0'}])


class JudgeSheetTests(unittest.TestCase):
    # 검증 목적: 판정 표본 시트가 맞음·틀림을 반반 뽑고, 틀림에는 망가뜨린 방식을 한국어로 적으며,
    #   사용자가 문장과 대조할 수 있게 근거 문단 원문을 자르지 않고 모두 싣는지 확인
    def test_judge_sheet_balanced_with_full_passage(self):
        rows = [{'id': f'judge-{i:04d}', 'sentence': f'문장 {i}번은 검사 대상이다.',
                 'passage': f'Passage {i} ' + 'long text ' * 120 + f'end {i}.',
                 'label': 'supported' if i % 2 else 'unsupported', 'perturbation': None if i % 2 else 'negation',
                 'source_id': f'v{i}'} for i in range(40)]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'judge-sample.md'
            judge_sheet(rows, out, n=10, seed=0)
            text = out.read_text(encoding='utf-8')
        headers = [line for line in text.splitlines() if line.startswith('## judge-')]
        self.assertEqual(len(headers), 10)
        self.assertEqual(sum('· 맞음' in h for h in headers), 5)
        self.assertEqual(sum('· 틀림 · 반대로 말하기' in h for h in headers), 5)
        for header in headers:
            row = rows[int(header.split()[1].split('-')[1])]
            self.assertIn(f'> {row["passage"]}', text)
            self.assertIn(row['sentence'], text)


class GradingSetTests(unittest.TestCase):
    # 검증 목적: Mohler 사람 채점 점수·통과 여부와 학생 답 원문을 그대로 옮기고, 루브릭이 없는 문항은 뺌
    def test_grading_set_keeps_human_labels(self):
        rows = [{'id': 'E01.Q01.A00', 'question': 'Q?', 'instructor_answer': 'Ref.', 'student_answer': 'Ans<br>',
                 'score_grader_1': 3.0, 'score_grader_2': 5.0, 'score_avg': 4.0},
                {'id': 'E01.Q01.A01', 'question': 'Q?', 'instructor_answer': 'Ref.', 'student_answer': 'No',
                 'score_grader_1': 2.0, 'score_grader_2': 3.0, 'score_avg': 2.5},
                {'id': 'E09.Q09.A00', 'question': 'R?', 'instructor_answer': 'x', 'student_answer': 'y',
                 'score_grader_1': 5.0, 'score_grader_2': 5.0, 'score_avg': 5.0}]
        result = grading_set(rows, {'E01.Q01': ['Simulates behavior']})
        self.assertEqual([r['id'] for r in result], ['E01.Q01.A00', 'E01.Q01.A01'])
        first = result[0]
        self.assertEqual(first['answer'], 'Ans<br>')
        self.assertEqual((first['human_scores'], first['human_avg'], first['human_pass']), ([3.0, 5.0], 4.0, True))
        self.assertEqual((first['question'], first['reference'], first['rubric']), ('Q?', 'Ref.', ['Simulates behavior']))
        self.assertFalse(result[1]['human_pass'])
        self.assertIn(first['split'], ('dev', 'report'))


if __name__ == '__main__':
    unittest.main()
