## @file
# @brief 공개 교재 원본(LaTeX·reStructuredText·EPUB)을 절과 그림 참조로 나누는 파서와 책별 그림 라이선스 필터를 검증
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path

from rag.parse import (PDF_LAYOUTS, SUBHEADING_MARK, FigureRef, parse_book, parse_epub, parse_latex, parse_pdf_text,
                       parse_rst, text_from_char_boxes)
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


# OpenStax PDF를 gs txtwrite로 뽑은 모양을 줄인 예
# - 목차·장 첫머리 Chapter Outline에도 같은 x.y 제목이 나오고, 장 끝 Summary는 절 제목을 다시 나열함
# - 홀수 쪽 머리말은 'x.y • 제목  쪽', 짝수 쪽은 '쪽  장 • 장 제목', 꼬리말은 'Access for free at openstax.org'
# - {M}은 하위 제목 글꼴 줄 표시(SUBHEADING_MARK, 수준만큼 반복). 7.1처럼 번호와 제목이 여러 줄로 나뉜 절 제목도 있음
OPENSTAX_TEXT = """Contents
6.1 What Is an Operating System?  253
6.2 Fundamental OS Concepts  255
Chapter Outline
6.1 What Is an Operating System?
6.2 Fundamental OS Concepts
Introduction
TechWorks is a start-up company.
6.1  What Is an Operating System?
Learning Objectives
An OS manages hardware.
Access for free at openstax.org
6.1 • What Is an Operating System?     253
More OS text.
254     6 • Infrastructure Abstraction Layer: Operating Systems
6.2 Fundamental OS Concepts
Learning Objectives
Kernel text.
{M}Kernel Mode and
{M}User Mode
Mode text.
{M}{M}Rings
Ring text.
Chapter Review
{M}Key Terms
kernel the core of the OS
Summary
6.1 What Is an Operating System?
Summary text.
7.1
Cloud
Computing
Learning Objectives
Cloud text.
""".format(M=SUBHEADING_MARK)


# UTA 'Computer Organization and Assembly Language Programming' PDF를 gs txtwrite로 뽑은 모양을 줄인 예
# - 제목은 글꼴로만 구분됨: 장(Calibri 20)={M}, 절(15.96)={M}{M}, 소절(14.04)={M}{M}{M}. 목차·번호 목록은 본문 글꼴
# - 원문 그대로의 특이점: 2장 뒤쪽 절은 '2. Octal'처럼 장 번호 없이 매겨지고, 5.2.2처럼 같은 번호가 두 번 나옴
UTA_TEXT = """CONTENTS
Chapter 2. The Number Systems .............................. 14
2.1.3 Two’s Complement ..................................... 18
{M}Acknowledgement
Thanks to reviewers.
{M}Chapter 2. The Number
{M}Systems
Intro.
{M}{M}2.1 Binary (Base-2) Number System
Binary.
{M}{M}{M}2.1.3 Two’s Complement
Invert and add one.
2.  Add R1 and R2:
{M}{M}{M}Negation
Negate text.
{M}{M}2. Octal (Base-8) Number System
{M}{M}3. Decimal (Base-10) Number System
Decimal.
17
{M}{M}{M}2.1.3 Repeated Number
Again.
{M}AI Usage Statement
Figures drawn with tools.
""".format(M=SUBHEADING_MARK)


class PdfTests(unittest.TestCase):
    # 검증 목적: 글꼴로 표시된 장·절 제목의 원문 번호를 section_id로 쓰고, 목차·번호 목록·장 밖 구역이 절로 잡히지 않는지 확인
    # - 목차 줄(점선·쪽 번호)과 본문 번호 목록('2.  Add R1')은 본문 글꼴이라 제목이 아님
    # - 번호 없는 소제목은 본문으로 남기고, 원문에서 겹친 번호는 '-2'를 붙여 section_id가 겹치지 않게 함
    # - 본문 없는 절 제목(2.2) 바로 뒤의 번호 제목(2.3)은 줄 넘김으로 이어 붙이지 않음
    def test_numbered_font_headings_use_book_numbers(self):
        sections = parse_pdf_text('uta-coal', 'coal.pdf', UTA_TEXT, PDF_LAYOUTS['uta-coal'])
        self.assertEqual([(s.section_id, s.title, s.text) for s in sections],
                         [('coal.pdf#2', 'The Number Systems', 'Intro.'),
                          ('coal.pdf#2.1', 'Binary (Base-2) Number System', 'Binary.'),
                          ('coal.pdf#2.1.3', 'Two’s Complement', 'Invert and add one. 2. Add R1 and R2: Negation Negate text.'),
                          ('coal.pdf#2.3', 'Decimal (Base-10) Number System', 'Decimal.'),
                          ('coal.pdf#2.1.3-2', 'Repeated Number', 'Again.')])

    # 검증 목적: 목차·장 개요·장 끝 요약에 반복되는 x.y 제목을 절로 오인하지 않고, 머리말·꼬리말이 본문에 섞이지 않는지 확인
    # - 장 개요의 6.1~6.6을 제목으로 받으면 순서 검사 때문에 실제 절 제목이 모두 버려져 한 절이 13만 자가 되던 문제
    # - 하위 제목 표시 줄은 <장>.<절>.<하위> 절이 되어 긴 절이 나뉘고, 여러 줄 제목은 하나로 이어짐
    def test_openstax_layout_splits_sections_and_drops_repeated_zones(self):
        sections = parse_pdf_text('openstax-cs', 'ics.pdf', OPENSTAX_TEXT, PDF_LAYOUTS['openstax-cs'])
        self.assertEqual([(s.section_id, s.title) for s in sections],
                         [('ics.pdf#6.1', 'What Is an Operating System?'), ('ics.pdf#6.2', 'Fundamental OS Concepts'),
                          ('ics.pdf#6.2.1', 'Kernel Mode and User Mode'), ('ics.pdf#6.2.1.1', 'Rings'),
                          ('ics.pdf#7.1', 'Cloud Computing')])
        self.assertEqual([s.text for s in sections],
                         ['An OS manages hardware. More OS text.', 'Kernel text.', 'Mode text.', 'Ring text.', 'Cloud text.'])

    # 검증 목적: 글자 사이 공백 기호가 없는 PDF에서 글자 상자 간격으로 단어 공백을 되살리는지 확인
    # - txtwrite 기본 출력은 Erickson 책에서 'TowerofHanoi'처럼 단어를 붙여 검색어가 맞지 않았음
    def test_char_boxes_restore_word_spaces(self):
        xml = ('<page>\n<span bbox="0 0 0 0" font="F" size="10">\n'
               '<char bbox="10 100 16 100" c="T"/><char bbox="16 100 22 100" c="o"/>'
               '<char bbox="22 100 28 100" c="w"/>\n</span>\n<span bbox="0 0 0 0" font="F" size="10">\n'
               '<char bbox="28 100 34 100" c="e"/><char bbox="34 100 40 100" c="r"/>'
               '<char bbox="44 100 50 100" c="o"/><char bbox="50 100 54 100" c="f"/>'
               '<char bbox="10 112 16 112" c="&amp;"/>\n</span>\n</page>\n')
        self.assertEqual(text_from_char_boxes(xml).strip(), 'Tower of\n&')

    # 검증 목적: 하위 제목 글꼴로만 된 줄에만 표시가 붙고, 본문 글꼴이 섞인 줄에는 붙지 않는지 확인
    def test_char_boxes_mark_subheading_font_lines(self):
        xml = ('<page>\n<span bbox="0 0 0 0" font="PX+NotoSans-Bold" size="12.9600">\n'
               '<char bbox="10 100 18 100" c="K"/><char bbox="18 100 26 100" c="e"/>\n</span>\n'
               '<span bbox="0 0 0 0" font="PX+NotoSans-Regular" size="9.0000">\n'
               '<char bbox="10 120 15 120" c="b"/><char bbox="15 120 20 120" c="y"/>\n</span>\n</page>\n')
        text = text_from_char_boxes(xml, PDF_LAYOUTS['openstax-cs'].subheadings)
        self.assertEqual(text.strip('\n').split('\n'), [SUBHEADING_MARK + 'Ke', 'by'])


## @brief spine 순서대로 XHTML 파일들을 담은 최소 EPUB을 path에 만듦(files: (OPF 기준 경로, 내용) 목록)
def write_epub(path, files):
    with zipfile.ZipFile(path, 'w') as epub:
        epub.writestr('META-INF/container.xml',
                      '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        items = ''.join(f'<item id="i{k}" href="{href}"/>' for k, (href, _) in enumerate(files))
        refs = ''.join(f'<itemref idref="i{k}"/>' for k in range(len(files)))
        epub.writestr('OEBPS/content.opf', f'<package><manifest>{items}</manifest><spine>{refs}</spine></package>')
        for href, body in files:
            epub.writestr(f'OEBPS/{href}', f'<html><body>{body}</body></html>')


class PressbooksTests(unittest.TestCase):
    # 검증 목적: Pressbooks EPUB(Watt)의 장 제목·저자 머리글을 장 도입 절로 바꾸고, 본문이 아닌 절을 빼는지 확인
    # - 장 제목이 h2.chapter-title이라 예전에는 저자 이름('Adrienne Watt')이 첫 절 제목이 되고 번호가 '#0.N'이었음
    # - 표지·목차 파일과 장 끝 Attribution·제목 없는 상자는 근거 문단으로 쓰지 않음
    def test_pressbooks_chapter_numbers_titles_and_non_body_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'watt-db').mkdir()
            write_epub(Path(tmp) / 'watt-db' / 'book.epub', [
                ('title-page.html', '<h1 class="title">Database Design</h1><p>Adrienne Watt</p>'),
                ('chapter-008-er.html',
                 '<h3 class="chapter-number"></h3><h2 class="chapter-title">Chapter 8 The ER Model</h2>'
                 '<h2 class="chapter-author">Adrienne Watt</h2><p>Intro.</p><h2>Keys</h2><p>Key text.</p>'
                 '<h3>Primary key</h3><p>Unique.</p><h2> </h2><p>Key Terms box.</p>'
                 '<h2>Attribution</h2><p>This chapter is adapted.</p>'),
                ('back-matter-003-lab.html', '<h1 class="back-matter-title">Appendix C SQL Lab</h1><p>Lab.</p>'
                 '<h1>Part 1</h1><p>DDL.</p>'),
            ])
            sections = parse_book(BOOKS['watt-db'], Path(tmp))
        self.assertEqual([(s.section_id, s.title, s.text) for s in sections],
                         [('chapter-008-er.html#8', 'The ER Model', 'Intro.'),
                          ('chapter-008-er.html#8.1', 'Keys', 'Key text.\n\nPrimary key\n\nUnique.'),
                          ('back-matter-003-lab.html#C', 'SQL Lab', 'Lab.'),
                          ('back-matter-003-lab.html#C.1', 'Part 1', 'DDL.')])


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
