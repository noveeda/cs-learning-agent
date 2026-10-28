## @file
# @brief 교재 절을 문단 묶음으로 자르고 개념 꼬리표로 색인·검색하는 RAG 색인(rag.index)과 검색기(rag.search)를 검증
# - 실제 임베딩 서버 대신 단어 주머니로 결정적 벡터를 만드는 가짜 임베더를 써서, 순위가 테스트 문장만으로 정해지게 함
# - 절 대응표(Task 2)는 같은 메서드(concepts_for·sections_for)를 가진 가짜 객체로 대신함
import math
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from rag.embed import Embedder
from rag.index import build_index, chunk_section
from rag.parse import FigureRef, Section
from rag.search import Retriever

VOCAB = ('queue', 'stack', 'fifo', 'lifo', 'pointer', 'array', 'thread', 'lock', 'page', 'cache')


## @brief 어휘표 단어 개수를 센 벡터를 L2 정규화해 돌려주는 가짜 임베더
# - 질문과 문단을 같은 방식으로 바꾸므로 같은 단어를 많이 공유할수록 코사인 유사도가 높음
class FakeEmbedder:
    def __init__(self):
        self.calls = 0

    def _vector(self, text):
        words = text.lower().replace('.', ' ').split()
        counts = [float(words.count(w)) for w in VOCAB]
        norm = math.sqrt(sum(c * c for c in counts)) or 1.0
        return [c / norm for c in counts]

    def embed_queries(self, texts):
        self.calls += 1
        return [self._vector(t) for t in texts]

    def embed_passages(self, texts):
        return [self._vector(t) for t in texts]


## @brief (책, 절) → 개념 목록 사전으로 만든 가짜 절 대응표
class FakeSectionMap:
    def __init__(self, mapping):
        self.mapping = mapping

    def concepts_for(self, book, section_id):
        return tuple(self.mapping.get((book, section_id), ()))

    def sections_for(self, concept):
        return tuple(key for key, concepts in self.mapping.items() if concept in concepts)


def _section(book, section_id, text, figures=()):
    return Section(book=book, section_id=section_id, title=section_id, text=text, figures=tuple(figures))


class ChunkTests(unittest.TestCase):
    # 검증 목적: 문단 경계에서 자르고 묶음이 max_chars를 넘지 않으며 id가 책:절:번호 형식인지 확인
    def test_chunk_section_respects_max_chars_and_paragraphs(self):
        paragraphs = ['a' * 40, 'b' * 40, 'c' * 40]
        chunks = chunk_section(_section('ods', 's#1', '\n\n'.join(paragraphs)), max_chars=90)
        self.assertEqual([c.text for c in chunks], [f'{"a" * 40}\n\n{"b" * 40}', 'c' * 40])
        self.assertEqual([c.id for c in chunks], ['ods:s#1:0', 'ods:s#1:1'])
        self.assertTrue(all(c.book == 'ods' and c.section_id == 's#1' and c.title == 's#1' for c in chunks))

    # 검증 목적: PDF 교재(OpenStax·Erickson)처럼 빈 줄 없이 2만 자가 넘는 절도 문장·단어 경계에서 잘려
    #   한 묶음이 max_chars를 넘지 않고, 이어 붙이면 원문 단어가 하나도 빠지지 않는지 확인
    def test_chunk_section_splits_oversized_paragraph(self):
        sentence = 'The scheduler picks the next ready thread from the queue. '
        text = (sentence * 400).strip() + ' ' + 'x' * 2500
        chunks = chunk_section(_section('openstax-cs', 'cs.pdf#14.3', text), max_chars=1200)
        self.assertTrue(all(len(c.text) <= 1200 for c in chunks))
        self.assertGreater(len(chunks), 18)
        self.assertEqual(' '.join(' '.join(c.text.split()) for c in chunks).replace(' ', ''), text.replace(' ', ''))
        self.assertTrue(chunks[0].text.endswith('queue.'))

    # 검증 목적: 본문이 빈 절은 빈 묶음을 색인에 남기지 않음
    def test_chunk_section_empty_text(self):
        self.assertEqual(chunk_section(_section('ods', 's#1', '')), [])


class IndexSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / 'index.sqlite'
        self.embedder = FakeEmbedder()

    def tearDown(self):
        self.tmp.cleanup()

    def _build(self, sections, mapping):
        return build_index(sections, FakeSectionMap(mapping), self.embedder, self.db)

    # 검증 목적: 대응표에 없는 절(개념 꼬리표 없음)은 검색 후보가 될 수 없도록 색인 자체에서 빠지는지 확인
    def test_build_index_only_mapped_sections(self):
        counts = self._build([_section('ods', 'q#1', 'queue fifo'), _section('ods', 'x#9', 'queue unrelated')],
                             {('ods', 'q#1'): ('queue',)})
        self.assertEqual(counts, {'chunks': 1, 'figures': 0})
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT id FROM chunks').fetchall(), [('ods:q#1:0',)])
            self.assertEqual(db.execute('SELECT chunk_id, concept FROM chunk_concepts').fetchall(),
                             [('ods:q#1:0', 'queue')])

    # 검증 목적: 질문과 더 비슷한 문단이라도 다른 개념 꼬리표면 고르지 않음(개념 범위 밖 근거 차단)
    def test_search_filters_by_concept(self):
        self._build([_section('ods', 'q#1', 'queue fifo'), _section('ods', 's#1', 'stack lifo stack lifo')],
                    {('ods', 'q#1'): ('queue',), ('ods', 's#1'): ('stack',)})
        result = Retriever(self.db, self.embedder).passages(['queue'], 'stack lifo')
        self.assertEqual([p.id for p in result], ['ods:q#1:0'])

    # 검증 목적: 같은 개념 문단은 코사인 유사도 내림차순으로 정렬되고 k개까지만 나오는지 확인
    def test_search_orders_by_similarity_and_k(self):
        self._build([_section('ods', 'a#1', 'queue array'), _section('ods', 'b#1', 'queue fifo'),
                     _section('ods', 'c#1', 'queue fifo fifo'), _section('ods', 'd#1', 'array pointer')],
                    {('ods', s): ('queue',) for s in ('a#1', 'b#1', 'c#1', 'd#1')})
        result = Retriever(self.db, self.embedder).passages(['queue'], 'fifo', k=2)
        self.assertEqual([p.id for p in result], ['ods:c#1:0', 'ods:b#1:0'])
        self.assertGreater(result[0].score, result[1].score)
        self.assertEqual((result[0].book, result[0].section_id, result[0].text), ('ods', 'c#1', 'queue fifo fifo'))

    # 검증 목적: 기준 유사도 이상인 문단이 없으면 억지로 고르지 않고 빈 목록(근거 없음)을 돌려줌
    def test_search_min_score_drops_all(self):
        self._build([_section('ods', 'q#1', 'queue fifo')], {('ods', 'q#1'): ('queue',)})
        retriever = Retriever(self.db, self.embedder, min_score=0.5)
        self.assertEqual(retriever.passages(['queue'], 'lock thread'), [])

    # 검증 목적: 대응표 빈칸 개념(문단 없음)은 예외 없이 빈 목록이고 임베딩 서버도 부르지 않음
    def test_search_unknown_concept_returns_empty(self):
        self._build([_section('ods', 'q#1', 'queue fifo')], {('ods', 'q#1'): ('queue',)})
        retriever = Retriever(self.db, self.embedder)
        self.assertEqual(retriever.passages(['join-algorithms'], 'queue'), [])
        self.assertIsNone(retriever.figure(['join-algorithms'], 'queue'))
        self.assertEqual(self.embedder.calls, 0)

    # 검증 목적: 색인 파일이 아직 없을 때(python -m rag.index 전) 예외 대신 빈 결과를 돌려주고 파일을 만들지 않음
    def test_missing_db_returns_empty(self):
        retriever = Retriever(self.root / 'none.sqlite', self.embedder)
        self.assertEqual(retriever.passages(['queue'], 'queue'), [])
        self.assertIsNone(retriever.figure(['queue'], 'queue'))
        self.assertFalse((self.root / 'none.sqlite').exists())

    # 검증 목적: [막혔어요] 3차 보충이 앞 시도에서 쓴 문단을 빼고 다음 순위 문단을 받는지 확인
    def test_exclude_skips_used_passages(self):
        self._build([_section('ods', 'b#1', 'queue fifo'), _section('ods', 'c#1', 'queue fifo fifo')],
                    {('ods', 'b#1'): ('queue',), ('ods', 'c#1'): ('queue',)})
        result = Retriever(self.db, self.embedder).passages(['queue'], 'fifo', exclude={'ods:c#1:0'})
        self.assertEqual([p.id for p in result], ['ods:b#1:0'])

    # 검증 목적: 후속 질문이 직전 답의 문단 id로 같은 문단을 그 순서대로 다시 받고, 사라진 id·없는 색인은 건너뜀
    def test_by_ids_keeps_order_and_skips_missing(self):
        self._build([_section('ods', 'b#1', 'queue fifo'), _section('ods', 'c#1', 'queue fifo fifo')],
                    {('ods', 'b#1'): ('queue',), ('ods', 'c#1'): ('queue',)})
        result = Retriever(self.db, self.embedder).by_ids(['ods:c#1:0', 'gone:x:0', 'ods:b#1:0'])
        self.assertEqual([(p.id, p.text) for p in result], [('ods:c#1:0', 'queue fifo fifo'), ('ods:b#1:0', 'queue fifo')])
        self.assertEqual(Retriever(self.root / 'none.sqlite', self.embedder).by_ids(['ods:b#1:0']), [])
        self.assertEqual(self.embedder.calls, 0)

    # 검증 목적: 한 답의 근거에 CC BY-SA(hailperin)와 CC BY-NC-SA(openstax-cs) 문단이 함께 들어가지 않는지 확인
    # - CC BY(ods)는 어느 계열과도 섞을 수 있어 1등이 by-sa여도 이어서 고를 수 있음
    # - 1등이 CC BY여도 2등으로 고른 계열(여기서는 by-nc-sa)과 충돌하는 by-sa는 건너뜀
    def test_passages_do_not_mix_sa_and_nc_sa(self):
        self._build([_section('hailperin', 'h#1', 'thread lock lock lock'),
                     _section('openstax-cs', 'o#1', 'thread lock lock'),
                     _section('ods', 'd#1', 'thread lock')],
                    {(b, s): ('mutex',) for b, s in (('hailperin', 'h#1'), ('openstax-cs', 'o#1'), ('ods', 'd#1'))})
        retriever = Retriever(self.db, self.embedder)
        self.assertEqual([p.book for p in retriever.passages(['mutex'], 'lock')], ['hailperin', 'ods'])
        self.assertEqual([p.book for p in retriever.passages(['mutex'], 'thread')], ['ods', 'openstax-cs'])
        self.assertEqual([p.book for p in retriever.passages(['mutex'], 'lock', exclude={'hailperin:h#1:0'})],
                         ['openstax-cs', 'ods'])

    # 검증 목적: 변환된 PNG가 있는 그림만 소속 절의 개념 꼬리표로 색인되고, 개념 범위 안에서 캡션이 가장 비슷한 1개가 나오는지 확인
    # - PNG가 없는 그림(ODS Ipe 원본처럼 변환하지 못한 것)은 화면에서 깨지므로 색인하지 않음
    def test_figure_indexed_with_section_concepts(self):
        png = self.root / 'figures' / 'hailperin' / 'hail_f0303.png'
        png.parent.mkdir(parents=True)
        png.write_bytes(b'png')
        figures = (FigureRef('hail_f0303', 'thread lock states', None), FigureRef('hail_f0999', 'thread lock', None))
        counts = self._build([_section('hailperin', 'h#1', 'thread lock', figures)], {('hailperin', 'h#1'): ('mutex',)})
        self.assertEqual(counts, {'chunks': 1, 'figures': 1})
        figure = Retriever(self.db, self.embedder).figure(['mutex'], 'lock')
        self.assertEqual((figure.id, figure.caption, figure.path), ('hailperin:hail_f0303', 'thread lock states', png))
        self.assertIsNone(Retriever(self.db, self.embedder).figure(['queue'], 'lock'))

    # 검증 목적: 근거 문단과 함께 보일 그림도 문단과 섞을 수 없는 라이선스 계열(by-sa ↔ by-nc-sa)이면 고르지 않음
    def test_figure_respects_passage_license(self):
        png = self.root / 'figures' / 'hailperin' / 'hail_f0303.png'
        png.parent.mkdir(parents=True)
        png.write_bytes(b'png')
        self._build([_section('hailperin', 'h#1', 'thread lock', (FigureRef('hail_f0303', 'lock', None),)),
                     _section('openstax-cs', 'o#1', 'thread lock')],
                    {('hailperin', 'h#1'): ('mutex',), ('openstax-cs', 'o#1'): ('mutex',)})
        retriever = Retriever(self.db, self.embedder)
        nc_sa = retriever.passages(['mutex'], 'lock', exclude={'hailperin:h#1:0'})
        self.assertEqual([p.book for p in nc_sa], ['openstax-cs'])
        self.assertIsNone(retriever.figure(['mutex'], 'lock', compatible_with=nc_sa))
        self.assertIsNotNone(retriever.figure(['mutex'], 'lock'))


class EmbedderTests(unittest.TestCase):
    # 검증 목적: 질문·문단에 각자의 접두어를 붙여 embeddings.create를 부르고, 결과를 L2 정규화해 코사인 = 내적이 되게 함
    def test_embedder_prefixes_and_normalizes(self):
        sent = []

        def create(model, input):
            sent.append((model, list(input)))
            return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[3.0, 4.0]) for i in range(len(input))])

        client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
        embedder = Embedder('http://x/v1', 'm', query_prefix='query: ', passage_prefix='passage: ', client=client)
        self.assertEqual(embedder.embed_queries(['a']), [[0.6, 0.8]])
        self.assertEqual(embedder.embed_passages(['b', 'c']), [[0.6, 0.8], [0.6, 0.8]])
        self.assertEqual(sent, [('m', ['query: a']), ('m', ['passage: b', 'passage: c'])])


if __name__ == '__main__':
    unittest.main()
