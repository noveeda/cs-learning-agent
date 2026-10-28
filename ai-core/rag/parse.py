## @file
# @brief 형식이 다른 교재 원본(LaTeX·reStructuredText·EPUB·PDF·Wikibooks 위키 문법)을 같은 Section 목록으로 분해
# - 절(section)이 근거 문단 색인(Task 3)과 절 대응표(Task 2)의 기본 단위. section_id는 책 안에서 안정적인 경로
# - 그림은 FigureRef로 소속 절에 붙이고, 책별 라이선스 정책(sources.Book.figures)에 맞지 않는 그림은 여기서 뺌
# - 형식별 파서는 표준 라이브러리만 쓰고, PDF 텍스트 추출만 서버의 Ghostscript(gs)를 호출함
import html
import posixpath
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from pathlib import Path

from rag.sources import Book


## @brief 절에 실린 그림 하나의 참조
# - file: 원본 폴더(raw/<book>/) 기준 경로. LaTeX처럼 확장자 없이 쓰는 형식은 그대로 둠
# - 수집(fetch)이 이 경로로 원본을 찾아 figures/<book>/<file>.png로 변환함
@dataclass(frozen=True)
class FigureRef:
    file: str
    caption: str
    label: str | None


## @brief 교재의 절 하나: 검색 문단과 절 대응표가 가리키는 단위
# - text는 문단 사이를 빈 줄로 구분한 평문. 마크업 명령은 지우고 수식($...$)은 그대로 둠
@dataclass(frozen=True)
class Section:
    book: str
    section_id: str
    title: str
    text: str
    figures: tuple[FigureRef, ...]


## @brief 문단 안 공백을 하나로 줄이고 문단은 빈 줄 하나로 구분
def _tidy(text):
    paragraphs = (' '.join(p.split()) for p in re.split(r'\n\s*\n', text))
    return '\n\n'.join(p for p in paragraphs if p)


## @brief text[i]의 여는 중괄호와 짝이 맞는 닫는 중괄호까지의 내용과 그 다음 위치 반환
# - 캡션·제목 안의 중첩 중괄호(\emph{...})를 정규식만으로는 자를 수 없어 짝을 셈
def _braced(text, i):
    depth, start = 0, i + 1
    for j in range(i, len(text)):
        if text[j] == '{' and text[j - 1:j] != '\\':
            depth += 1
        elif text[j] == '}' and text[j - 1:j] != '\\':
            depth -= 1
            if depth == 0:
                return text[start:j], j + 1
    return text[start:], len(text)


# 인자까지 통째로 지우는 LaTeX 명령: 상호 참조·색인·인용과 ODS의 코드 삽입 명령은 본문으로 읽을 내용이 없음
# - ODS는 \seclabel·\secref·\figlabel처럼 이름이 label·ref로 끝나는 전용 명령을 쓰므로 끝말로 함께 지움
_DROP_WITH_ARG = re.compile(
    r'\\([a-zA-Z]*(?:label|ref)|index|cite|includegraphics|bibliography\w*|codeimport|javaimport|cppimport'
    r'|pythonimport|pcodeimport|addcontentsline|markboth|markright|pagenumbering|vspace|hspace)\*?\s*(\[[^\]]*\])?\s*\{')


## @brief LaTeX 조각을 평문으로 바꿈
# - 수식 구간은 명령이 의미를 가지므로 보호해 두었다가 되돌림
# - ODS가 코드 이름을 #ArrayStack#처럼 감싸는 표기는 # 기호만 지움
def _latex_text(source):
    maths = []

    def keep_math(match):
        maths.append(match.group(0))
        return f'\x00{len(maths) - 1}\x00'

    text = re.sub(r'\$\$.+?\$\$|\$[^$]+\$', keep_math, source, flags=re.S)
    while match := _DROP_WITH_ARG.search(text):
        _, end = _braced(text, match.end() - 1)
        text = text[:match.start()] + text[end:]
    text = re.sub(r'\\\\(\[[^\]]*\])?', '\n', text)
    for escaped, char in (('\\%', '%'), ('\\&', '&'), ('\\_', '_'), ('\\$', '$'), ('\\{', '\x01'), ('\\}', '\x02')):
        text = text.replace(escaped, char)
    text = text.replace('\\#', '\x03').replace('#', '').replace('\x03', '#')
    text = re.sub(r'\\(begin|end)\s*\{[^}]*\}(\[[^\]]*\])?', '\n', text)
    # 기호 악센트(\'e, \"o)는 단어 중간이라 공백 없이 지우고, \, \  같은 간격 명령은 공백으로 바꿈
    text = re.sub(r'\\[`\'"^~=.]', '', text)
    text = re.sub(r'\\[a-zA-Z@]+\*?(\[[^\]]*\])?', ' ', text)
    text = re.sub(r'\\.', ' ', text).replace('{', '').replace('}', '').replace('~', ' ')
    text = text.replace('\x01', '{').replace('\x02', '}')
    text = re.sub(r'\x00(\d+)\x00', lambda m: maths[int(m.group(1))], text)
    # 문장부호 앞에 남은 명령 자리 공백은 지워 원문 문장 모양을 되살림
    return re.sub(r' +([.,;:!?)])', r'\1', _tidy(text))


## @brief figure 환경 하나에서 그림 파일·캡션·라벨을 뽑음. 파일이 여러 개면 같은 캡션으로 각각 만듦
def _latex_figures(body, base):
    files = re.findall(r'\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}', body)
    caption = ''
    if match := re.search(r'\\caption\s*(\[[^\]]*\])?\s*\{', body):
        caption = _latex_text(_braced(body, match.end() - 1)[0])
    label = re.search(r'\\[a-z]*label\s*\{([^}]*)\}', body)
    return [FigureRef(posixpath.normpath(posixpath.join(base, f.strip())), caption, label and label.group(1))
            for f in files]


_LATEX_HEADING = re.compile(r'\\(chapter|section|subsection)(\*?)\s*(\[[^\]]*\])?\s*\{')


## @brief LaTeX 장 파일 하나를 chapter·section·subsection 경계로 나눔
# - chapter: 장 번호 문자열(부록은 'A'). section_id는 '<file>#<장>.<절>[.<소절>]'
# - 번호 없는 절(\section*)은 번호 대신 제목으로 '<file>#<장>:<제목>'을 씀
# - 장 제목 바로 뒤 도입 글처럼 본문이 빈 구간은 절로 만들지 않음
def parse_latex(book_id, file, source, chapter='1'):
    source = re.sub(r'(?<!\\)%.*', '', source)
    base = posixpath.dirname(file)
    heads = []
    for match in _LATEX_HEADING.finditer(source):
        title, end = _braced(source, match.end() - 1)
        heads.append((match.start(), end, match.group(1), bool(match.group(2)), _latex_text(title)))
    section_no = sub_no = 0
    sections = []
    spans = [(0, 0, None, False, '')] + heads
    for k, (_, end, kind, starred, title) in enumerate(spans):
        stop = heads[k][0] if k < len(heads) else len(source)
        body = source[end:stop]
        if kind == 'chapter':
            number = chapter
        elif kind == 'section' and starred:
            number = f'{chapter}:{title}'
        elif kind == 'section':
            section_no, sub_no = section_no + 1, 0
            number = f'{chapter}.{section_no}'
        elif kind == 'subsection':
            sub_no += 1
            number = f'{chapter}.{section_no}.{sub_no}'
        else:
            number = None
        figures = []

        def take_figure(match):
            figures.extend(_latex_figures(match.group(0), base))
            return '\n\n'

        body = re.sub(r'\\begin\{(figure\*?)\}.*?\\end\{\1\}', take_figure, body, flags=re.S)
        text = _latex_text(body)
        if number and text:
            sections.append(Section(book_id, f'{file}#{number}', title, text, tuple(figures)))
    return sections


## @brief LaTeX 책 전체: \documentclass가 있는 주 파일의 \include 순서대로 장 파일을 읽음
# - ODS는 언어별 생성 파일(intro-lang)을 include하므로 없으면 '-lang'을 뗀 원본(intro.tex)을 읽음
# - 번호 있는 \chapter가 없는 앞부분(서문·감사의 글)은 근거로 쓰지 않아 건너뜀
# - \appendix 뒤 장은 A, B 순서로 번호를 매김
def _parse_latex_book(book, root):
    mains = [p for p in sorted(root.rglob('*.tex')) if '\\documentclass' in p.read_text(errors='replace')]
    if not mains:
        return []
    main = max(mains, key=lambda p: p.read_text(errors='replace').count('\\include{'))
    main_text = re.sub(r'(?<!\\)%.*', '', main.read_text(errors='replace'))
    sections, number, appendix = [], 0, False
    for match in re.finditer(r'\\appendix\b|\\include\{([^}]*)\}', main_text):
        if not match.group(1):
            appendix, number = True, 0
            continue
        path = main.parent / f'{match.group(1)}.tex'
        if not path.exists():
            path = main.parent / f'{match.group(1).removesuffix("-lang")}.tex'
        if not path.exists():
            continue
        source = path.read_text(errors='replace')
        if not re.search(r'\\chapter\s*(\[[^\]]*\])?\s*\{', re.sub(r'(?<!\\)%.*', '', source)):
            continue
        number += 1
        chapter = chr(ord('A') + number - 1) if appendix else str(number)
        sections += parse_latex(book.id, path.relative_to(root).as_posix(), source, chapter)
    return sections


# reStructuredText 제목 밑줄로 쓰는 문장부호
_ADORN = re.compile(r'^([=\-~^"\'`#*+:._])\1{2,}\s*$')
# 본문으로 읽을 내용이 없는 지시문: 블록 전체를 지움. 나머지 지시문(admonition 등)은 들여쓴 내용만 남김
_RST_DROP = {'toctree', 'image', 'raw', 'include', 'literalinclude', 'only', 'comment'}


## @brief reStructuredText 인라인 표기(역할·링크·강조·코드)를 평문으로 바꿈
def _rst_inline(text):
    text = re.sub(r':[\w:-]+:`([^`<]*?)\s*(<[^>]*>)?`', r'\1', text)
    text = re.sub(r'`([^`<]*?)\s*<[^>]*>`__?', r'\1', text)
    text = re.sub(r'``(.*?)``', r'\1', text)
    text = re.sub(r'`([^`]*)`_{0,2}', r'\1', text)
    text = re.sub(r'\*\*(.*?)\*\*|\*(\S(?:.*?\S)?)\*', lambda m: m.group(1) or m.group(2), text)
    return re.sub(r'\|([^|\s][^|]*)\|_?', r'\1', text)


## @brief 지시문 뒤 들여쓴 블록의 끝 줄 번호. 빈 줄은 블록 안에 포함함
def _block_end(lines, i):
    j = i + 1
    while j < len(lines) and (not lines[j].strip() or lines[j][:1] in ' \t'):
        j += 1
    return j


## @brief rst 문서 하나를 제목 단위로 나눔
# - 제목 수준은 밑줄(또는 위아래 줄) 모양이 처음 나온 순서로 정함(docutils 규칙)
# - 맨 위 제목의 번호('3.4 Routing', 'Chapter 3: ...')를 section_id에 쓰고, 그 아래 제목은 순번을 붙임
#   예: internetworking/routing.rst#3.4.2. 번호가 없으면 1부터 매김
# - figure 지시문은 FigureRef로 옮기고, 지시문 바로 앞 '.. _label:'을 그림 라벨로 씀
def parse_rst(book_id, file, source):
    lines = source.replace('\t', '    ').splitlines()
    base = posixpath.dirname(file)
    styles, counters, top, title, tops = [], [], None, None, 0
    sections, body, figures, label = [], [], [], None

    def flush():
        text = _tidy(_rst_inline('\n'.join(body)))
        if title is not None and (text or figures):
            number = '.'.join([top, *map(str, counters[1:])])
            sections.append(Section(book_id, f'{file}#{number}', title, text, tuple(figures)))

    i = 0
    while i < len(lines):
        line = lines[i]
        over = _ADORN.match(line) and i + 2 < len(lines) and lines[i + 2].rstrip() == line.rstrip() and lines[i + 1].strip()
        under = (line.strip() and not line[:1].isspace() and not _ADORN.match(line) and i + 1 < len(lines)
                 and _ADORN.match(lines[i + 1]) and len(lines[i + 1].strip()) >= min(len(line.strip()), 3))
        if over or under:
            heading = (lines[i + 1] if over else line).strip()
            style = ((line if over else lines[i + 1]).strip()[0], bool(over))
            if style not in styles:
                styles.append(style)
            level = styles.index(style)
            flush()
            body, figures, label = [], [], None
            if level == 0:
                match = re.match(r'(?:Chapter\s+)?(\d+(?:\.\d+)*)\.?:?\s+(.*)', heading)
                tops += 1
                top, heading = (match.group(1), match.group(2)) if match else (str(tops), heading)
                counters = [0]
            else:
                counters = (counters + [0] * level)[:level + 1]
                counters[level] += 1
                # 하위 제목에 원문 번호('3.4.1 ...')가 있으면 section_id와 겹치므로 제목에서 뗌
                heading = re.sub(r'^\d+(?:\.\d+)+\s+', '', heading)
            title = _rst_inline(heading)
            i += 3 if over else 2
            continue
        directive = re.match(r'\.\.\s+([\w:-]+)::\s*(.*)', line)
        if directive:
            end = _block_end(lines, i)
            name = directive.group(1)
            block = [b[3:] if b.startswith('   ') else b.strip() for b in lines[i + 1:end]]
            # 지시문 바로 뒤 옵션 줄(:width: 등)은 첫 빈 줄 전까지만 있음
            options = next((k for k, b in enumerate(block) if not re.match(r':[\w-]+:', b)), len(block))
            content = block[options:]
            if name == 'figure':
                caption = _tidy(_rst_inline('\n'.join(content))).split('\n\n')[0] if content else ''
                path = posixpath.normpath(posixpath.join(base, directive.group(2).strip()))
                figures.append(FigureRef(path, caption, label))
                body.append('')
            elif name not in _RST_DROP:
                body += [''] + content + ['']
            label = None
            i = end
            continue
        if line.startswith('..'):
            match = re.match(r'\.\.\s+_([^:]+):\s*$', line)
            label = match.group(1) if match else None
            i = _block_end(lines, i) if not match else i + 1
            continue
        if line.strip():
            label = None
        body.append(line)
        i += 1
    flush()
    return sections


## @brief toctree 지시문에 적힌 문서 경로 목록(원본 폴더 기준)
def _toctree(file, source):
    entries, base = [], posixpath.dirname(file)
    for block in re.finditer(r'^\.\. toctree::\n((?:[ \t]+.*\n|\s*\n)*)', source + '\n', re.M):
        for entry in block.group(1).splitlines():
            entry = entry.strip()
            if entry and not entry.startswith(':'):
                entries.append(posixpath.normpath(posixpath.join(base, entry)))
    return entries


## @brief rst 책 전체: index.rst 목차에서 'Chapter N' 제목을 가진 장과 그 장의 하위 문서만 읽음
# - 머리말·인쇄판 안내 같은 장 밖 문서는 근거로 쓰지 않음
def _parse_rst_book(book, root):
    index = root / 'index.rst'
    if not index.exists():
        return []
    sections = []
    for chapter in _toctree('index.rst', index.read_text(errors='replace')):
        path = root / chapter
        if not path.exists():
            continue
        source = path.read_text(errors='replace')
        if not re.search(r'^Chapter\s+\d+', source, re.M):
            continue
        sections += parse_rst(book.id, chapter, source)
        for child in _toctree(chapter, source):
            if (root / child).exists():
                sections += parse_rst(book.id, child, (root / child).read_text(errors='replace'))
    return sections


## @brief XHTML 한 파일을 (제목·문단·그림) 사건 목록으로 바꾸는 파서
# - h1·h2는 절 경계, h3 이하는 본문 문단으로 둠. 제목의 class(Pressbooks의 chapter-title 등)도 함께 넘김
# - img는 figure 안 figcaption이 있으면 그것을, 없으면 alt를 캡션으로 씀
class _XhtmlEvents(HTMLParser):
    _BLOCKS = {'p', 'div', 'li', 'tr', 'br', 'blockquote', 'pre', 'h3', 'h4', 'h5', 'h6', 'section', 'table', 'dd', 'dt'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events, self.buffer, self.heading, self.skip = [], [], None, 0
        self.figure, self.caption, self.figure_caption = None, None, ''

    def _text(self):
        if self.buffer:
            self.events.append(('text', ''.join(self.buffer)))
            self.buffer = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style', 'nav'):
            self.skip += 1
        elif tag in ('h1', 'h2'):
            self._text()
            self.heading = (tag, attrs.get('class') or '', [])
        elif tag == 'figure':
            self.figure = []
        elif tag == 'figcaption':
            self.caption = []
        elif tag == 'img':
            item = (attrs.get('src', ''), attrs.get('alt') or '')
            if self.figure is not None:
                self.figure.append(item)
            else:
                self.events.append(('figure', *item))
        elif tag in self._BLOCKS:
            self.buffer.append('\n\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'nav'):
            self.skip = max(0, self.skip - 1)
        elif tag in ('h1', 'h2') and self.heading:
            tag, cls, parts = self.heading
            self.events.append(('heading', tag, ' '.join(''.join(parts).split()), cls))
            self.heading = None
        elif tag == 'figcaption' and self.caption is not None:
            if self.figure is not None:
                self.figure_caption = ' '.join(''.join(self.caption).split())
            self.caption = None
        elif tag == 'figure' and self.figure is not None:
            self.events += [('figure', src, self.figure_caption or alt) for src, alt in self.figure]
            self.figure, self.figure_caption = None, ''
        elif tag in self._BLOCKS:
            self.buffer.append('\n\n')

    def handle_data(self, data):
        if self.skip:
            return
        if self.heading:
            self.heading[2].append(data)
        elif self.caption is not None:
            self.caption.append(data)
        else:
            self.buffer.append(data)

    def close(self):
        super().close()
        self._text()


# 장 단위 제목의 class. Pressbooks는 장 제목을 h2.chapter-title, 부록을 h1.back-matter-title로 씀
_EPUB_CHAPTER_CLASS = re.compile(r'-title\b')
# 장 제목의 'Chapter 8 ...'·'Appendix C ...'에서 장 번호와 나머지 제목을 나눔
_EPUB_CHAPTER_TITLE = re.compile(r'(?:Chapter|Appendix)\s+(\d+|[A-Z])\b[\s.:–-]*(.*)')


## @brief EPUB 한 권: container.xml → OPF spine 순서로 XHTML을 읽고 장·절 제목으로 나눔
# - 장 단위 제목: class가 '-title'로 끝나는 제목(Pressbooks), 그런 제목이 없는 파일에서는 h1
#   - 'Chapter 8 ...'·'Appendix C ...'이면 그 번호를 쓰고 제목에서 뗌. 아니면 파일 안 순번
#   - 장 제목 바로 뒤 본문(장 도입)은 장 제목을 단 '<파일>#<장>' 절이 됨
# - 절 단위 제목: 나머지 h1·h2. section_id는 '<OPF 기준 XHTML 경로>#<장>.<절 순번>'(예: chapter-008-...html#8.3)
# - 저자 머리글(h2.chapter-author)은 절 경계로 쓰지 않음. 예전에는 저자 이름이 장 첫 절 제목이 됨
# - 그림 경로는 OPF 폴더 기준으로 정규화해 FigureRef.file에 둠
def parse_epub(book_id, path):
    sections = []
    with zipfile.ZipFile(path) as epub:
        container = ET.fromstring(epub.read('META-INF/container.xml'))
        opf_path = next(e.get('full-path') for e in container.iter() if e.tag.endswith('rootfile'))
        opf = ET.fromstring(epub.read(opf_path))
        opf_dir = posixpath.dirname(opf_path)
        hrefs = {e.get('id'): e.get('href') for e in opf.iter() if e.tag.endswith('item')}
        for ref in (e for e in opf.iter() if e.tag.endswith('itemref')):
            href = hrefs.get(ref.get('idref'))
            if not href or not href.endswith(('.xhtml', '.html', '.htm')):
                continue
            parser = _XhtmlEvents()
            parser.feed(epub.read(posixpath.join(opf_dir, href)).decode('utf-8', errors='replace'))
            parser.close()
            classed = any(e[0] == 'heading' and _EPUB_CHAPTER_CLASS.search(e[3]) for e in parser.events)
            chapter, ordinal, number = '0', 0, 0
            title, body, figures = None, [], []

            def flush():
                text = _tidy(''.join(body))
                if title is not None and (text or figures):
                    section_id = f'{href}#{chapter}.{number}' if number else f'{href}#{chapter}'
                    sections.append(Section(book_id, section_id, title, text, tuple(figures)))

            for event in parser.events:
                if event[0] == 'heading':
                    _, tag, heading, cls = event
                    if 'chapter-author' in cls:
                        continue
                    flush()
                    body, figures = [], []
                    if _EPUB_CHAPTER_CLASS.search(cls) or (tag == 'h1' and not classed):
                        ordinal, number = ordinal + 1, 0
                        match = _EPUB_CHAPTER_TITLE.match(heading)
                        chapter = match.group(1) if match else str(ordinal)
                        title = match.group(2) if match and match.group(2) else heading
                    else:
                        number, title = number + 1, heading
                elif event[0] == 'text':
                    body.append(event[1])
                else:
                    src = posixpath.normpath(posixpath.join(posixpath.dirname(href), event[1]))
                    figures.append(FigureRef(src, event[2], None))
            flush()
    return sections


# txtwrite 글자 상자 출력(TextFormat=0)의 글꼴(span)·글자(char) 항목과 쪽 경계
_CHAR_BOX = re.compile(r'<span [^>]*font="([^"]*)" size="([\d.]+)"'
                       r'|<char bbox="([-\d.]+) ([-\d.]+) ([-\d.]+) [-\d.]+" c="([^"]*)"/>|<page>')
# 하위 제목 글꼴로만 된 줄 앞에 수준 수만큼 붙이는 표시. 본문에 나오지 않는 제어 문자라 일반 줄과 섞이지 않음
SUBHEADING_MARK = '\x1f'


## @brief txtwrite 글자 상자 출력에서 줄과 단어 공백을 다시 만듦
# - 기본 텍스트 출력은 공백 글리프 없이 글자 간격만으로 띄운 PDF(Erickson)에서 단어를 붙여 버림
# - 앞 글자 오른쪽 끝과 다음 글자 왼쪽 끝 사이가 글꼴 크기의 15%를 넘으면 단어 공백으로 봄
#   (합자·커닝 때문에 같은 단어가 span으로 나뉘어도 간격은 0에 가까움)
# - 기준선(y)이 글꼴 크기 절반 넘게 바뀌거나 x가 크게 되돌아가면 새 줄로 봄
# - subheadings: 하위 제목 수준별 '<글꼴 이름> <크기>' 정규식. 모든 글자가 n번째 글꼴인 줄 앞에 SUBHEADING_MARK를 n개 붙임
def text_from_char_boxes(xml, subheadings=()):
    lines, chars, size, sub, last = [], [], 10.0, 0, None

    def finish():
        text = ''.join(c for c, _ in chars)
        levels = {level for c, level in chars if not c.isspace()}
        level = levels.pop() if len(levels) == 1 else 0
        lines.append(SUBHEADING_MARK * level + text)
        chars.clear()

    for match in _CHAR_BOX.finditer(xml):
        if match.group(0) == '<page>':
            finish()
            last = None
        elif match.group(1) is not None:
            size = float(match.group(2))
            font = f'{match.group(1)} {match.group(2)}'
            sub = next((n for n, pattern in enumerate(subheadings, 1) if re.search(pattern, font)), 0)
        else:
            x0, y, x1 = float(match.group(3)), float(match.group(4)), float(match.group(5))
            if last and (abs(y - last[1]) > size * 0.5 or x0 < last[0] - size):
                finish()
            elif last and x0 - last[0] > size * 0.15:
                chars.append((' ', sub))
            chars.append((html.unescape(match.group(6)), sub))
            last = (x1, y)
    finish()
    return '\n'.join(lines)


## @brief Ghostscript txtwrite 글자 상자 출력으로 PDF 본문 텍스트를 뽑음(서버에 다른 PDF 도구가 없음)
def pdf_text(path, subheadings=()):
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'out.xml'
        subprocess.run(['gs', '-q', '-dNOPAUSE', '-dBATCH', '-sDEVICE=txtwrite', '-dTextFormat=0',
                        f'-sOutputFile={out}', str(path)], check=False, capture_output=True, timeout=600)
        return text_from_char_boxes(out.read_text(errors='replace'), subheadings) if out.exists() else ''


## @brief 책마다 다른 PDF 쪽 구성 규칙
# - confirm: 진짜 절 제목 바로 다음 줄의 모양. 있으면 순서 검사 대신 이것으로 제목을 확정함
# - stop: 본문이 끝나고 장 끝 반복 구역(용어·요약·문제)이 시작되는 줄. 다음 절 제목까지 버림
# - noise: 머리말·꼬리말·쪽 번호처럼 쪽마다 반복되어 본문에서 지울 줄
# - subheadings: 절 안 하위 제목 글꼴('<글꼴 이름> <크기>' 정규식)을 수준 순서로. 긴 절을 <장>.<절>.<하위>[.<하위>]로 나눔
@dataclass(frozen=True)
class PdfLayout:
    confirm: str | None = None
    stop: str | None = None
    noise: str | None = None
    subheadings: tuple[str, ...] = ()


# OpenStax: 목차·장 첫머리 Chapter Outline·장 끝 Summary에 같은 x.y 제목이 반복되지만,
# 본문 절 제목 바로 다음 줄은 항상 'Learning Objectives'이고 장 끝은 'Chapter Review'로 시작함
# - 절 제목은 RobotoCondensed-Bold 15.55, 하위 제목은 NotoSans-Bold 12.96과 10.8이라 글꼴로 구분됨
#   (10.8 글꼴인 'Learning Objectives'는 noise로 먼저 지움)
PDF_LAYOUTS = {
    'openstax-cs': PdfLayout(
        confirm=r'\s*Learning Objectives\s*$',
        stop=r'\s*Chapter Review\s*$',
        noise=r'\s*(Access for free at openstax\.org.*|\d+(\.\d+)? • .*\s\d+|\d+\s+\d+ • .*|\d{1,3}'
              r'|Learning Objectives|By the end of this section, you will be able to:)\s*$',
        subheadings=(r'NotoSans-Bold 12\.9\d*$', r'NotoSans-Bold 10\.8\d*$')),
}

_PDF_HEADING = re.compile(r'^\s*(\d+)\.(\d+)(?:\s+(\S.{0,90}?))?\s*$')


## @brief 줄 i가 절 제목이면 ((장, 절), 제목, 제목 다음 줄 번호), 아니면 None
# - 목차 줄(점선·쪽 번호로 끝남)과 글자로 시작하지 않는 제목은 받지 않음
# - layout.confirm이 없으면(Erickson) 번호 순서로 고름
#   - 본문 속 '1.58496' 같은 숫자 줄을 제목으로 오인하지 않도록 앞 제목 바로 다음(절 +1~3, 또는 다음 장 첫 절)만 받음
# - layout.confirm이 있으면 제목 바로 뒤에 그 모양의 줄이 나오는 제목만 받음. 같은 번호가 다시 확정되면 처음 것만 씀
#   - 제목이 줄을 넘기면 이어 붙임: 번호 줄에 제목이 있으면 1줄, 'x.y'만 있는 줄(12.2)이면 3줄까지
#   - 그 사이에 다른 번호 줄이 나오면 목차·장 개요 목록이므로 받지 않음
def _pdf_heading(lines, i, layout, last, first, seen):
    match = _PDF_HEADING.match(lines[i].lstrip(SUBHEADING_MARK))
    if not match:
        return None
    number, title = (int(match.group(1)), int(match.group(2))), match.group(3)
    if title and (not title[0].isalpha() or re.search(r'(\. ?){3}|\d$', title)):
        return None
    if not layout.confirm:
        same_chapter = number[0] == last[0] and last[1] < number[1] <= last[1] + 3
        next_chapter = number[0] == last[0] + 1 and number[1] <= 3
        accept = title and (same_chapter or next_chapter or (first and number[1] == 1))
        return (number, title, i + 1) if accept else None
    parts, limit = ([title], 2) if title else ([], 3)
    for j in range(i + 1, len(lines)):
        following = lines[j].lstrip(SUBHEADING_MARK).strip()
        if re.match(layout.confirm, following):
            return (number, ' '.join(parts), j) if parts and number not in seen else None
        if not following:
            continue
        if _PDF_HEADING.match(following) or len(parts) >= limit:
            return None
        parts.append(following)
    return None


## @brief PDF에서 뽑은 텍스트를 '<장>.<절>' 제목 기준으로 나누고, 하위 제목 표시가 있으면 수준별 번호를 붙여 더 나눔
# - 예: 6.3(절 도입) → 6.3.2(Concurrency) → 6.3.2.4(Allocation)
# - layout.stop 줄부터 다음 절 제목까지(장 끝 용어·요약·문제)와 layout.noise 줄은 버림
# - 여러 줄로 나뉜 하위 제목(본문 없이 같은 수준 표시가 연달아 나옴)은 하나로 이음
def parse_pdf_text(book_id, file, text, layout=PdfLayout()):
    lines = text.replace('\r', '').splitlines()
    sections, seen = [], set()
    last, counters, title, body = (0, 0), [], None, []

    def flush():
        joined = _tidy('\n'.join(body))
        if title is not None and joined:
            number = '.'.join(map(str, [*last, *counters]))
            sections.append(Section(book_id, f'{file}#{number}', title, joined, ()))

    i = 0
    while i < len(lines):
        line = lines[i]
        heading = _pdf_heading(lines, i, layout, last, title is None and not sections, seen)
        if heading:
            flush()
            seen.add(heading[0])
            (last, title, i), counters, body = heading, [], []
            continue
        i += 1
        plain = line.lstrip(SUBHEADING_MARK)
        level = len(line) - len(plain)
        if layout.stop and re.match(layout.stop, plain):
            flush()
            title, body = None, []
        elif layout.noise and re.match(layout.noise, plain):
            continue
        elif level and title is not None:
            if len(counters) == level and not body:
                title += ' ' + plain.strip()
                continue
            flush()
            counters = (counters + [0] * level)[:level]
            counters[-1] += 1
            title, body = plain.strip(), []
        elif not level:
            body.append(line)
    flush()
    return sections


## @brief MediaWiki 위키 문법을 평문으로 바꿈(틀·각주·파일 링크·표 서식 제거, 링크는 표시 글자만)
def _wiki_text(source):
    text = re.sub(r'<ref[^>/]*/>|<ref[^>]*>.*?</ref>|<!--.*?-->', '', source, flags=re.S)
    while re.search(r'\{\{[^{}]*\}\}', text):
        text = re.sub(r'\{\{[^{}]*\}\}', '', text)
    while re.search(r'\[\[(?:File|Image):[^\[\]]*\]\]', text, re.I):
        text = re.sub(r'\[\[(?:File|Image):[^\[\]]*\]\]', '', text, flags=re.I)
    text = re.sub(r'\[\[(?:Category):[^\]]*\]\]', '', text, flags=re.I)
    text = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]*)\]\]', r'\1', text)
    text = re.sub(r'\[(?:https?:)?//\S+\s+([^\]]*)\]', r'\1', text)
    text = re.sub(r"'{2,}", '', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'^\{\|.*$|^\|\}.*$|^\|-.*$', '', text, flags=re.M)
    text = re.sub(r'^[*#:;!|]+\s*', '', text, flags=re.M)
    return _tidy(text)


## @brief Wikibooks 쪽 하나를 '== 제목 ==' 단위로 나눔. 제목 없는 첫 구간은 쪽 이름을 제목으로 씀
# - 쪽마다 번호가 없으므로 section_id는 '<쪽 이름>#<제목>'(같은 쪽 안에서 제목이 겹치면 순번을 붙임)
def parse_wikitext(book_id, page, source):
    parts = re.split(r'^(={2,6})\s*(.*?)\s*\1\s*$', source, flags=re.M)
    sections, seen = [], {}
    chunks = [(page, parts[0])] + [(parts[k + 1], parts[k + 2]) for k in range(1, len(parts) - 2, 3)]
    for title, body in chunks:
        text = _wiki_text(body)
        title = _wiki_text(title) or page
        seen[title] = seen.get(title, 0) + 1
        key = title if seen[title] == 1 else f'{title} ({seen[title]})'
        if text:
            sections.append(Section(book_id, f'{page}#{key}', title, text, ()))
    return sections


# 근거 문단으로 쓰지 않는 절 제목: 목차·판권·감사의 글·출처 표기·참고 문헌·더 읽을거리·판 이력, 그리고 제목 없는 상자
_NON_BODY_TITLE = re.compile(
    r'(table of )?contents|copyright|acknowledge?ments?|(image )?attributions?|references|bibliography'
    r'|further reading|see also|external links|notes|versioning history|about the authors?|', re.I)
# 본문 밖 파일(Pressbooks EPUB의 표지·속표지·목차·판권·머리말 묶음, 저자 소개·판 이력)
_NON_BODY_FILE = re.compile(r'(^|/)(front-cover|title-page|table-of-contents|copyright|front-matter-[^/#]*'
                            r'|back-matter-[^/#]*-(versioning-history|about-the-authors?))\.x?html?#')


## @brief 절이 교재 본문인지: 목차·감사의 글·출처 표기처럼 개념 설명이 없는 절이면 False
# - 이런 절이 검색되면 근거로 보이지만 설명할 내용이 없어 답을 흐리므로 모든 책에서 뺌
def is_body_section(section):
    return not (_NON_BODY_TITLE.fullmatch(section.title.strip()) or _NON_BODY_FILE.search(section.section_id))


## @brief 책의 그림 정책(all·allowlist·none)에 맞는 그림만 남김
def allowed_figure(book, file):
    if book.figures == 'none':
        return False
    if book.figures == 'allowlist':
        return file in book.figure_allow
    return file not in book.figure_deny


## @brief 책 한 권의 원본 폴더(raw_dir/<book.id>/)를 형식에 맞게 읽어 절 목록을 만듦
# - 원본 폴더가 없으면 빈 목록. 사람이 아직 내려받지 않은 책 때문에 다른 책 수집이 멈추지 않게 함
# - 라이선스를 확인하지 않은 그림과 본문이 아닌 절(is_body_section)은 여기서 빼므로, 이후 단계(색인·화면)는 결과를 그대로 써도 됨
def parse_book(book: Book, raw_dir: Path) -> list[Section]:
    root = Path(raw_dir) / book.id
    if not root.is_dir():
        return []
    if book.kind == 'latex':
        sections = _parse_latex_book(book, root)
    elif book.kind == 'rst':
        sections = _parse_rst_book(book, root)
    elif book.kind == 'epub':
        sections = [s for path in sorted(root.glob('*.epub')) for s in parse_epub(book.id, path)]
    elif book.kind == 'pdf':
        layout = PDF_LAYOUTS.get(book.id, PdfLayout())
        sections = [s for path in sorted(root.glob('*.pdf'))
                    for s in parse_pdf_text(book.id, path.name, pdf_text(path, layout.subheadings), layout)]
    elif book.kind == 'wikibooks':
        sections = [s for path in sorted(root.glob('*.wiki'))
                    for s in parse_wikitext(book.id, path.stem, path.read_text(errors='replace'))]
    else:
        raise ValueError(f'지원하지 않는 원본 형식: {book.kind}')
    return [replace(s, figures=tuple(f for f in s.figures if allowed_figure(book, f.file)))
            for s in sections if is_body_section(s)]
