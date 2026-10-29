## @file
# @brief 미세조정 학습 데이터와 측정 세트를 가르는 규칙(finetune.split)을 검증
# - 제외 개념이 학습으로 새거나 측정 질문이 학습에 섞이면, 미세조정 모델의 측정 점수가 부풀어 합격 판정을 믿을 수 없음
import json
import tempfile
import unittest
from pathlib import Path

from finetune.split import choose_holdout, load_holdout, measured_questions, normalize_question, trainable_passages
from serving.concept_map import Concept, load_concept_map


## @brief 과목·번호로 가짜 개념을 만듦(choose_holdout은 id와 subject만 봄)
def _concept(subject, n):
    return Concept(id=f'{subject}-c{n}', title='개념', description='설명', objectives=['설명할 수 있다.'], subject=subject)


class FinetuneSplitTests(unittest.TestCase):
    # 검증 목적: 과목마다 2개, 근거 문단 3개 이상인 개념만 고르고, 같은 seed면 다시 돌려도 같은 목록이어야 문서의 목록을 재현할 수 있음
    def test_choose_holdout_two_per_subject(self):
        subjects = ['dsa', 'os', 'db', 'network', 'arch']
        concepts = [_concept(s, n) for s in subjects for n in range(6)]
        # 번호가 짝수인 개념만 문단 3개 이상: 문단이 모자란 개념이 뽑히면 제외 개념으로 측정할 재료가 없음
        counts = {c.id: (3 if int(c.id[-1]) % 2 == 0 else 2) for c in concepts}
        chosen = choose_holdout(concepts, counts)
        self.assertEqual(len(chosen), 10)
        self.assertEqual(chosen, sorted(chosen))
        by_subject = {s: [c for c in chosen if c.startswith(f'{s}-')] for s in subjects}
        self.assertEqual({s: len(ids) for s, ids in by_subject.items()}, {s: 2 for s in subjects})
        self.assertTrue(all(counts[c] >= 3 for c in chosen))
        self.assertEqual(choose_holdout(concepts, counts, seed=0), chosen)

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

    # 검증 목적: 커밋한 제외 개념 목록이 형식(과목마다 2개, 10개)을 지키고 실제 개념 지도에 있는 ID인지 확인
    def test_committed_holdout_matches_concept_map(self):
        subject_of = {c.id: c.subject for c in load_concept_map().concepts}
        holdout = load_holdout()
        self.assertEqual(len(holdout), 10)
        self.assertLessEqual(holdout, set(subject_of))
        subjects = [subject_of[c] for c in holdout]
        self.assertEqual({s: subjects.count(s) for s in subjects}, {s: 2 for s in set(subject_of.values())})


if __name__ == '__main__':
    unittest.main()
