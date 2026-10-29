## @file
# @brief 미세조정 학습 데이터와 측정 세트를 가르는 규칙(finetune.split)을 검증
# - 제외 개념이 학습으로 새거나 측정 질문이 학습에 섞이면, 미세조정 모델의 측정 점수가 부풀어 합격 판정을 믿을 수 없음
import json
import tempfile
import unittest
from pathlib import Path

from finetune.split import HOLDOUT_PATH, choose_holdout, load_holdout, measured_questions, normalize_question, trainable_passages
from serving.concept_map import Concept, load_concept_map


## @brief 과목·번호로 가짜 개념을 만듦(choose_holdout은 id와 subject만 봄)
def _concept(subject, n):
    return Concept(id=f'{subject}-c{n}', title='개념', description='설명', objectives=['설명할 수 있다.'], subject=subject)


## @brief 개념마다 그 개념만 대응된 절을 counts만큼 만든 가짜 절 대응표(절 공유 없음)
def _own_sections(counts):
    return {'version': 1, 'gaps': [], 'entries': [
        {'book': 'b', 'section_id': f'{c}#{i}', 'concepts': [c]} for c, n in counts.items() for i in range(n)]}


class FinetuneSplitTests(unittest.TestCase):
    # 검증 목적: 과목마다 2개, 근거 문단 3개 이상인 개념만 고르고, 같은 seed면 다시 돌려도 같은 목록이어야 문서의 목록을 재현할 수 있음
    def test_choose_holdout_two_per_subject(self):
        subjects = ['dsa', 'os', 'db', 'network', 'arch']
        concepts = [_concept(s, n) for s in subjects for n in range(6)]
        # 번호가 짝수인 개념만 문단 3개 이상: 문단이 모자란 개념이 뽑히면 제외 개념으로 측정할 재료가 없음
        counts = {c.id: (3 if int(c.id[-1]) % 2 == 0 else 2) for c in concepts}
        chosen, seed = choose_holdout(concepts, counts, _own_sections(counts))
        # 절 공유가 없으면 처음 seed가 바로 제약을 만족함
        self.assertEqual(seed, 0)
        self.assertEqual(len(chosen), 10)
        self.assertEqual(chosen, sorted(chosen))
        by_subject = {s: [c for c in chosen if c.startswith(f'{s}-')] for s in subjects}
        self.assertEqual({s: len(ids) for s, ids in by_subject.items()}, {s: 2 for s in subjects})
        self.assertTrue(all(counts[c] >= 3 for c in chosen))
        self.assertEqual(choose_holdout(concepts, counts, _own_sections(counts), seed=0), (chosen, 0))

    # 검증 목적: 제외 개념과 절을 공유한 개념의 학습 절이 3개 미만이 되면 사실상 학습되지 않는 개념이 생겨
    # 학습 개념/제외 개념 구분이 흐려지므로, 그런 seed는 건너뛰고 다음 seed로 다시 뽑아야 함
    def test_holdout_keeps_other_concepts_trainable(self):
        subjects = ['dsa', 'os']
        concepts = [_concept(s, n) for s in subjects for n in range(4)]
        counts = {c.id: 3 for c in concepts}
        first, _ = choose_holdout(concepts, counts, _own_sections(counts))
        # seed 0이 고른 개념 하나의 절 3개를 고르지 않은 개념(피해 개념)의 절로도 대응시킴
        picked = first[0]
        victim = next(c.id for c in concepts if c.id not in first and c.subject == picked.split('-')[0])
        section_map = _own_sections({c: n for c, n in counts.items() if c != victim})
        for entry in section_map['entries']:
            if entry['concepts'] == [picked]:
                entry['concepts'] = [picked, victim]
        chosen, seed = choose_holdout(concepts, counts, section_map)
        self.assertGreater(seed, 0)
        trainable = trainable_passages(section_map, set(chosen))
        for concept_id in counts.keys() - set(chosen):
            self.assertGreaterEqual(sum(1 for e in section_map['entries']
                                        if concept_id in e['concepts'] and (e['book'], e['section_id']) in trainable), 3)
        # 1000번 안에 제약을 만족하는 seed가 없으면 조용히 넘어가지 않고 예외
        with self.assertRaises(ValueError):
            choose_holdout(concepts, {c: 3 for c in counts}, {'version': 1, 'gaps': [], 'entries': [
                {'book': 'b', 'section_id': f'all#{i}', 'concepts': list(counts)} for i in range(3)]})

    # 검증 목적: 절 공유 때문에 제약을 만족할 수 없는 과목(OS)은 제외 개념을 고르지 않고, 나머지 과목만 2개씩 골라야 함
    def test_skipped_subject_has_no_holdout(self):
        subjects = ['dsa', 'os']
        concepts = [_concept(s, n) for s in subjects for n in range(4)]
        counts = {c.id: 3 for c in concepts}
        chosen, _ = choose_holdout(concepts, counts, _own_sections(counts), skip_subjects={'os'})
        self.assertEqual(len(chosen), 2)
        self.assertTrue(all(c.startswith('dsa-') for c in chosen))

    # 검증 목적: 띄어쓰기·문장부호만 다른 질문을 다른 질문으로 보면 측정 질문이 학습에 그대로 들어감
    def test_normalized_duplicate_is_measured(self):
        self.assertEqual(normalize_question('스택이 뭐야?'), normalize_question(' 스택이  뭐야 '))
        self.assertEqual(normalize_question('What is a Stack?'), normalize_question('what is a stack'))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'scope.jsonl'
            path.write_text(json.dumps({'id': 's-0', 'question': '스택이 뭐야?'}, ensure_ascii=False) + '\n', encoding='utf-8')
            self.assertIn(normalize_question('스택이, 뭐야!'), measured_questions([path]))

    # 검증 목적: 여러 개념에 대응된 절이 학습에 남으면 제외 개념의 근거 문단이 학습 예시로 새어 나감
    def test_passage_shared_with_holdout_is_excluded(self):
        section_map = {'version': 1, 'gaps': [], 'entries': [
            {'book': 'ods', 'section_id': 'a#1', 'concepts': ['stack', 'queue']},
            {'book': 'ods', 'section_id': 'a#2', 'concepts': ['stack']}]}
        self.assertEqual(trainable_passages(section_map, {'queue'}), {('ods', 'a#2')})

    # 검증 목적: 커밋한 제외 개념 목록이 형식(건너뛴 과목 0개, 나머지 과목마다 2개)을 지키고 실제 개념 지도에 있는 ID인지 확인
    def test_committed_holdout_matches_concept_map(self):
        subject_of = {c.id: c.subject for c in load_concept_map().concepts}
        data = json.loads(HOLDOUT_PATH.read_text(encoding='utf-8'))
        holdout = load_holdout()
        self.assertEqual(set(data['skipped_subjects']), {'os'})
        self.assertEqual(len(holdout), 8)
        self.assertLessEqual(holdout, set(subject_of))
        subjects = [subject_of[c] for c in holdout]
        expected = {s: 2 for s in set(subject_of.values()) - set(data['skipped_subjects'])}
        self.assertEqual({s: subjects.count(s) for s in subjects}, expected)

if __name__ == '__main__':
    unittest.main()
