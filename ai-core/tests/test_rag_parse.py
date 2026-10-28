## @file
# @brief 공개 교재 원본(LaTeX·reStructuredText·EPUB)을 절과 그림 참조로 나누는 파서와 책별 그림 라이선스 필터를 검증
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path

from rag.parse import FigureRef, parse_book, parse_epub, parse_latex, parse_rst
from rag.sources import BOOKS


class LatexTests(unittest.TestCase):
    # 검증 목적: 절 명령마다 절이 나뉘고, 명령 이름이 본문에 남아 검색 문단을 더럽히지 않는지 확인
    def test_latex_sections_split_on_section_commands(self):
        source = ('\\section{A}\\label{a-sec}\nFirst \\emph{body} text.\n'
                  '\\subsection{B}\nSecond body~text.\n')
        sections = parse_latex('hailperin', 'scheduling.tex', source, '3')
        self.assertEqual([s.title for s in sections], ['A', 'B'])
        self.assertEqual([s.section_id for s in sections], ['scheduling.tex#3.1', 'scheduling.tex#3.1.1'])
        self.assertEqual(sections[0].text, 'First body text.')
        self.assertEqual(sections[1].text, 'Second body text.')
        self.assertFalse(any('\\section' in s.text or 'label' in s.text for s in sections))

    # 검증 목적: 그림 환경의 파일 이름·캡션·라벨이 소속 절의 FigureRef로 옮겨지고 본문에서는 빠지는지 확인
    def test_latex_figure_caption_extracted(self):
        source = ('\\section{Thread States}\nBefore.\n\\begin{figure}\n'
                  '\\centerline{\\includegraphics{hail_f0303}}\n'
                  '\\caption{Threads change states as shown here.}\\label{thread-states}\n'
                  '\\end{figure}\nAfter.\n')
        [section] = parse_latex('hailperin', 'scheduling.tex', source, '3')
        self.assertEqual(section.figures, (FigureRef(file='hail_f0303', caption='Threads change states as shown here.',
                                                     label='thread-states'),))
        self.assertEqual(section.text, 'Before.\n\nAfter.')


class RstTests(unittest.TestCase):
    # 검증 목적: 밑줄 문자의 첫 등장 순서로 제목 수준을 정하고, 제목의 절 번호를 section_id에 쓰는지 확인
    def test_rst_sections_use_underline_headings(self):
        source = '3.4 Routing\n===========\n\nIntro *text*.\n\nNetwork as a Graph\n------------------\n\nBody.\n'
        sections = parse_rst('peterson-davie', 'internetworking/routing.rst', source)
        self.assertEqual([(s.section_id, s.title, s.text) for s in sections],
                         [('internetworking/routing.rst#3.4', 'Routing', 'Intro text.'),
                          ('internetworking/routing.rst#3.4.1', 'Network as a Graph', 'Body.')])

    # 검증 목적: 제목 글자가 아니라 밑줄 문자로 수준을 정해, 같은 숫자로 시작하는 하위 제목이 맨 위 제목으로 오인되지 않는지 확인
    # - 원본(routing.rst)에 '3.4 2 Distance-Vector (RIP)'처럼 번호가 섞인 하위 제목이 있어 section_id가 겹쳤던 문제
    def test_rst_level_comes_from_underline_character(self):
        source = ('3.4 Routing\n===========\n\nA.\n\nGraph\n-----\n\nB.\n\n'
                  '3.4 2 Distance-Vector\n---------------------\n\nC.\n\nDetail\n~~~~~~\n\nD.\n')
        sections = parse_rst('peterson-davie', 'r.rst', source)
        self.assertEqual([s.section_id for s in sections], ['r.rst#3.4', 'r.rst#3.4.1', 'r.rst#3.4.2', 'r.rst#3.4.2.1'])
        self.assertEqual([s.title for s in sections], ['Routing', 'Graph', '2 Distance-Vector', 'Detail'])

    # 검증 목적: figure 지시문의 경로가 원본 폴더 기준으로 바뀌고, 옵션 다음 문단이 캡션이 되는지 확인
    def test_rst_figure_directive(self):
        source = ('Routing\n=======\n\n.. _fig-graph:\n.. figure:: figures/f03-01.png\n'
                  '   :width: 400px\n   :align: center\n\n   Network represented\n   as a graph.\n\nAfter.\n')
        [section] = parse_rst('peterson-davie', 'internetworking/routing.rst', source)
        self.assertEqual(section.figures, (FigureRef(file='internetworking/figures/f03-01.png',
                                                     caption='Network represented as a graph.', label='fig-graph'),))
        self.assertEqual(section.text, 'After.')


class EpubTests(unittest.TestCase):
    # 검증 목적: 사람이 내려받은 EPUB을 spine 순서대로 읽어 h1·h2 제목으로 절을 나누고 그림 대체 문구를 캡션으로 쓰는지 확인
    def test_epub_sections_from_xhtml_headings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'book.epub'
            with zipfile.ZipFile(path, 'w') as epub:
                epub.writestr('META-INF/container.xml',
                              '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
                epub.writestr('OEBPS/content.opf',
                              '<package><manifest><item id="c1" href="text/ch1.xhtml"/></manifest>'
                              '<spine><itemref idref="c1"/></spine></package>')
                epub.writestr('OEBPS/text/ch1.xhtml',
                              '<html><body><h1>Chapter 1</h1><p>Intro.</p><h2>Keys</h2><p>A key is unique.</p>'
                              '<img src="../images/key.png" alt="Primary key example"/></body></html>')
            sections = parse_epub('watt-db', path)
        self.assertEqual([(s.section_id, s.title, s.text) for s in sections],
                         [('text/ch1.xhtml#1', 'Chapter 1', 'Intro.'), ('text/ch1.xhtml#1.1', 'Keys', 'A key is unique.')])
        self.assertEqual(sections[1].figures, (FigureRef(file='images/key.png', caption='Primary key example', label=None),))


class BookTests(unittest.TestCase):
    # 검증 목적: 사람이 내려받을 책의 원본이 아직 없을 때 예외 없이 빈 목록이라 나머지 책 수집이 계속되는지 확인
    def test_missing_manual_book_returns_empty(self):
        self.assertIsNone(BOOKS['watt-db'].fetch)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(parse_book(BOOKS['watt-db'], Path(tmp)), [])

    # 검증 목적: 라이선스를 확인하지 않은 그림이 절 결과에 남아 화면에 나가는 일을 막는지 확인
    # - figures='none'은 모든 그림을 빼고, allowlist는 목록에 있는 파일만 남김
    def test_figures_filtered_by_book_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'hailperin'
            root.mkdir()
            (root / 'book.tex').write_text('\\documentclass{book}\\begin{document}\\include{ch}\\end{document}')
            (root / 'ch.tex').write_text(
                '\\chapter{C}\n\\section{S}\nText.\n'
                '\\begin{figure}\\includegraphics{keep}\\caption{Kept.}\\end{figure}\n'
                '\\begin{figure}\\includegraphics{drop}\\caption{Dropped.}\\end{figure}\n')
            book = BOOKS['hailperin']
            none = parse_book(replace(book, figures='none', figure_deny=()), Path(tmp))
            allow = parse_book(replace(book, figures='allowlist', figure_allow=('keep',), figure_deny=()), Path(tmp))
        self.assertEqual([s.section_id for s in none], ['ch.tex#1.1'])
        self.assertEqual(none[0].figures, ())
        self.assertEqual([f.file for f in allow[0].figures], ['keep'])


if __name__ == '__main__':
    unittest.main()
