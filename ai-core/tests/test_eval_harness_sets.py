## @file
# @brief 하네스 검색 측정 세트 생성기(eval.harness_sets)와 회수율 계산(eval.harness_retrieval)을 검증
# - 질문 작성(26B 호출)은 문단 id를 그대로 돌려주는 가짜 함수로 대신해, 행과 정답 문단의 연결만 확인함
# - 회수율은 순위를 미리 정해 둔 가짜 검색기로, 정답 점수는 실제 색인(rag.index)과 단어 주머니 임베더로 확인함
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from eval.harness_retrieval import gold_scores, recall_at_k
from eval.harness_sets import retrieval_set, sample_sheet, usable_question
from rag.index import Chunk, build_index
from rag.parse import Section
from rag.search import Retriever


def _chunks(concept_counts):
    return [Chunk(f'ods:{concept}:{n}', 'ods', concept, concept, f'text {concept} {n}')
            for concept, count in concept_counts.items() for n in range(count)]


## @brief 문단 id의 절 부분(= 개념 이름으로 만든 가짜 절)을 개념 꼬리표로 돌려줌
def _concepts_of(chunk_id):
    return (chunk_id.split(':')[1],)


## @brief 질문 대신 문단 id를 그대로 적어 어느 문단으로 만든 질문인지 드러내는 가짜 질문 작성기
def _write_question(chunk):
    return f'Q {chunk.id}'


class RetrievalSetTests(unittest.TestCase):
    # 검증 목적: 정답 문단이 질문을 만든 바로 그 문단이고, 그 문단에 행의 개념 꼬리표가 붙어 있는지 확인
    #   (정답이 어긋나면 회수율이 모델과 무관하게 틀어짐)
    def test_retrieval_set_gold_is_source_chunk(self):
        rows = retrieval_set(_chunks({'stack': 4, 'queue': 4}), _concepts_of, _write_question)
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row['question'], f'Q {row["gold_chunk"]}')
            self.assertIn(row['concept'], _concepts_of(row['gold_chunk']))
            self.assertEqual(set(row), {'id', 'concept', 'question', 'gold_chunk'})
        self.assertEqual(len({row['id'] for row in rows}), len(rows))

    # 검증 목적: 개념마다 per_concept개(문단이 모자라면 있는 만큼)를 고르고, 같은 seed면 같은 세트가 나와
    #   측정을 다시 재현할 수 있는지 확인. seed가 바뀌면 고르는 문단도 바뀌어야 함
    def test_retrieval_set_per_concept_and_seeded(self):
        chunks = _chunks({'stack': 10, 'queue': 2})
        rows = retrieval_set(chunks, _concepts_of, _write_question, per_concept=3, seed=0)
        counts = {c: sum(r['concept'] == c for r in rows) for c in ('stack', 'queue')}
        self.assertEqual(counts, {'stack': 3, 'queue': 2})
        self.assertEqual(rows, retrieval_set(list(reversed(chunks)), _concepts_of, _write_question, per_concept=3, seed=0))
        picks = {tuple(r['gold_chunk'] for r in retrieval_set(chunks, _concepts_of, _write_question, seed=s))
                 for s in range(5)}
        self.assertGreater(len(picks), 1)

    # 검증 목적: 표본 시트가 개념마다 먼저 하나씩 골라 n개를 쓰고, 정답 문단 원문을 함께 보여 주는지 확인
    def test_sample_sheet_one_per_concept_first(self):
        rows = retrieval_set(_chunks({'stack': 3, 'queue': 3, 'heap': 3}), _concepts_of, _write_question)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'sheet.md'
            sample_sheet(rows, 3, out, passages={r['gold_chunk']: f'원문 {r["gold_chunk"]}' for r in rows})
            sheet = out.read_text()
        for concept in ('stack', 'queue', 'heap'):
            self.assertEqual(sheet.count(f'· {concept}'), 1)
        self.assertEqual(sheet.count('원문 ods:'), 3)

    # 검증 목적: 한글 질문은 통과하고 한자 섞인 질문·잘린 출력·너무 긴 출력만 버리는지 확인
    #   (한자 범위를 잘못 적으면 한글까지 걸러 세트가 통째로 비는 사고가 있었음)
    def test_usable_question_keeps_hangul_drops_han(self):
        self.assertEqual(usable_question(' "스택은 왜 LIFO인가요?" ', 'stop'), '스택은 왜 LIFO인가요?')
        self.assertEqual(usable_question('스택\u5806은 무엇인가요?', 'stop'), '')
        self.assertEqual(usable_question('스택\uf900은 무엇인가요?', 'stop'), '')
        self.assertEqual(usable_question('스택은', 'length'), '')
        self.assertEqual(usable_question('가' * 201, 'stop'), '')


## @brief 질문별로 미리 정한 순위 목록을 돌려주는 가짜 검색기; 받은 인자를 기록해 개념·k 전달을 확인함
class FakeRetriever:
    def __init__(self, ranking):
        self.ranking, self.calls = ranking, []

    def passages(self, concepts, query, k=3, exclude=frozenset()):
        self.calls.append((tuple(concepts), query, k))
        return [SimpleNamespace(id=i) for i in self.ranking[query][:k]]


## @brief 어휘표 단어 개수로 결정적 벡터를 만드는 가짜 임베더(정답 점수 계산용)
class BagEmbedder:
    VOCAB = ('stack', 'queue', 'lifo', 'fifo')

    def _vector(self, text):
        words = text.lower().split()
        counts = [float(words.count(w)) for w in self.VOCAB]
        norm = math.sqrt(sum(c * c for c in counts)) or 1.0
        return [c / norm for c in counts]

    def embed_queries(self, texts):
        return [self._vector(t) for t in texts]

    def embed_passages(self, texts):
        return [self._vector(t) for t in texts]


class RecallTests(unittest.TestCase):
    # 검증 목적: 정답 문단이 상위 k개 안에 있을 때만 맞힌 것으로 세고, 검색기에 행의 개념과 k를 넘기는지 확인
    def test_recall_at_k_counts_gold_in_top_k(self):
        rows = [{'id': 'r1', 'concept': 'stack', 'question': 'q1', 'gold_chunk': 'a'},
                {'id': 'r2', 'concept': 'stack', 'question': 'q2', 'gold_chunk': 'c'},
                {'id': 'r3', 'concept': 'queue', 'question': 'q3', 'gold_chunk': 'd'}]
        retriever = FakeRetriever({'q1': ['a', 'b', 'c'], 'q2': ['a', 'b', 'c', 'd'], 'q3': ['a', 'b', 'c', 'd']})
        self.assertAlmostEqual(recall_at_k(rows, retriever, k=3), 2 / 3)
        self.assertEqual(retriever.calls[2], (('queue',), 'q3', 3))
        self.assertEqual(recall_at_k([], retriever), 0.0)

    # 검증 목적: 정답 점수가 질문과 정답 문단(색인에 저장된 벡터)의 코사인 유사도인지 확인
    #   RAG_MIN_SCORE를 이 분포의 하위 5%로 정하므로, 다른 문단 점수가 섞이면 기준이 틀어짐
    def test_gold_scores_use_indexed_gold_vector(self):
        sections = [Section(book='ods', section_id='s1', title='', text='stack lifo', figures=()),
                    Section(book='ods', section_id='s2', title='', text='queue fifo', figures=())]
        section_map = SimpleNamespace(concepts_for=lambda book, section_id: ('stack',))
        rows = [{'id': 'r1', 'concept': 'stack', 'question': 'stack lifo', 'gold_chunk': 'ods:s1:0'},
                {'id': 'r2', 'concept': 'stack', 'question': 'stack', 'gold_chunk': 'ods:s2:0'}]
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'index.sqlite'
            build_index(sections, section_map, BagEmbedder(), db)
            scores = gold_scores(rows, Retriever(db, BagEmbedder(), min_score=-1.0))
        self.assertAlmostEqual(scores[0], 1.0, places=5)
        self.assertAlmostEqual(scores[1], 0.0, places=5)


if __name__ == '__main__':
    unittest.main()
