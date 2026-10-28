## @file
# @brief 절 대응표에 있는 교재 절을 문단 묶음(Chunk)으로 잘라 임베딩하고, 개념 꼬리표와 함께 SQLite 색인에 저장
# - 실행: python -m rag.index → RAG_DATA_DIR/sections.jsonl(rag.fetch 결과)을 읽어 RAG_DATA_DIR/index.sqlite를 만듦
# - 개념 꼬리표는 절 단위 대응표(Task 2 SectionMap)에서 받아 그 절의 모든 문단·그림에 똑같이 붙임
# - 검색(rag.search)은 이 꼬리표로 먼저 거른 뒤 벡터 유사도로 순위를 매김
import argparse
import json
import os
import re
import sqlite3
import sys
from array import array
from dataclasses import dataclass
from pathlib import Path

from rag.embed import Embedder
from rag.fetch import DATA_DIR
from rag.parse import FigureRef, Section

# 문장 끝(마침표·물음표·느낌표 뒤 공백). 빈 줄 없는 긴 문단(PDF 교재)을 문장 경계에서 자를 때 씀
_SENTENCE_END = re.compile(r'(?<=[.!?])\s+')


## @brief 색인·검색 단위인 문단 묶음 하나
# - id: f'{book}:{section_id}:{n}'. 같은 원본으로 다시 색인해도 같은 id가 나오므로 기록·exclude가 계속 유효함
# - text: 절 본문의 연속 구간. 판정 근거 대조(공백 정규화 후 부분 문자열)가 이 원문을 기준으로 함
@dataclass(frozen=True)
class Chunk:
    id: str
    book: str
    section_id: str
    title: str
    text: str


## @brief 문단 하나를 max_chars 이하 조각들로 나눔: 짧으면 그대로, 길면 문장 경계, 문장도 길면 공백(없으면 글자 수)에서 자름
# - OpenStax·Erickson PDF는 빈 줄이 거의 없어 절 전체가 문단 하나(최대 6만여 자)로 들어오므로 필요함
def _pieces(paragraph, max_chars):
    if len(paragraph) <= max_chars:
        yield paragraph
        return
    buffer = ''
    for sentence in _SENTENCE_END.split(paragraph):
        while len(sentence) > max_chars:
            cut = sentence.rfind(' ', 0, max_chars + 1)
            cut = cut if cut > 0 else max_chars
            if buffer:
                yield buffer
                buffer = ''
            yield sentence[:cut]
            sentence = sentence[cut:].lstrip()
        if buffer and len(buffer) + 1 + len(sentence) > max_chars:
            yield buffer
            buffer = ''
        buffer = f'{buffer} {sentence}' if buffer else sentence
    if buffer:
        yield buffer


## @brief 절 하나를 문단 경계에서 max_chars 이하 묶음으로 자름
# - 이어지는 문단은 빈 줄로 이어 붙여 한 묶음이 max_chars를 넘기 직전까지 모음. 짧은 문단이 근거 하나로 쪼개지지 않게 함
# - 1200자는 근거 문단 3개를 작은 모델 프롬프트에 함께 넣어도 부담이 적은 크기(교육적 분량이 아니라 입력 길이 보호)
def chunk_section(section: Section, max_chars: int = 1200) -> list[Chunk]:
    groups, buffer = [], ''
    for paragraph in section.text.split('\n\n'):
        for piece in _pieces(paragraph.strip(), max_chars):
            if buffer and len(buffer) + 2 + len(piece) > max_chars:
                groups.append(buffer)
                buffer = ''
            buffer = f'{buffer}\n\n{piece}' if buffer else piece
    if buffer:
        groups.append(buffer)
    return [Chunk(f'{section.book}:{section.section_id}:{n}', section.book, section.section_id, section.title, text)
            for n, text in enumerate(groups)]


## @brief 벡터를 float32 바이트로 바꿈. 검색이 array('f')로 그대로 되읽음
def _blob(vector):
    return array('f', vector).tobytes()


## @brief 대응표에 있는 절의 문단 묶음과 그림을 임베딩해 db_path에 새 색인을 만들고 {'chunks': n, 'figures': m} 반환
# - 대응표에 개념이 없는 절은 건너뜀. 꼬리표 없는 문단은 어떤 개념 검색에도 나올 수 없으므로 색인할 이유가 없음
# - 그림은 db_path와 같은 폴더의 figures/<book>/<file>.png(rag.fetch 변환 결과)가 있을 때만 색인. 없으면 화면에서 깨짐
# - 문단은 제목과 본문을, 그림은 캡션을 임베딩함. 제목이 본문에 없는 주제어를 보태 검색 회수율을 높임
# - 기존 db_path는 지우고 새로 만듦. 운영 중 교체는 CLI(main)가 임시 파일에 만든 뒤 바꿔치움
def build_index(sections, section_map, embedder, db_path: Path) -> dict[str, int]:
    db_path = Path(db_path)
    chunks, figures = [], {}
    for section in sections:
        concepts = section_map.concepts_for(section.book, section.section_id)
        if not concepts:
            continue
        chunks += [(chunk, concepts) for chunk in chunk_section(section)]
        for figure in section.figures:
            path = Path('figures') / section.book / Path(figure.file).with_suffix('.png')
            if (db_path.parent / path).is_file():
                # 같은 그림이 여러 절에 실리면 처음 실린 절을 소속으로 두고 개념 꼬리표만 합침
                _, _, _, tags = figures.setdefault(f'{section.book}:{figure.file}',
                                                   (section, figure, path.as_posix(), []))
                tags += [c for c in concepts if c not in tags]
    chunk_vectors = embedder.embed_passages([f'{c.title}\n{c.text}' for c, _ in chunks])
    figure_vectors = embedder.embed_passages([figure.caption for _, figure, _, _ in figures.values()])
    db_path.unlink(missing_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.executescript(
            'CREATE TABLE chunks(id TEXT PRIMARY KEY, book TEXT, section_id TEXT, title TEXT, text TEXT, vector BLOB);'
            'CREATE TABLE chunk_concepts(chunk_id TEXT, concept TEXT);'
            'CREATE TABLE figures(id TEXT PRIMARY KEY, book TEXT, section_id TEXT, caption TEXT, path TEXT, vector BLOB);'
            'CREATE TABLE figure_concepts(figure_id TEXT, concept TEXT);'
            'CREATE INDEX chunk_concepts_concept ON chunk_concepts(concept);'
            'CREATE INDEX figure_concepts_concept ON figure_concepts(concept);')
        for (chunk, concepts), vector in zip(chunks, chunk_vectors):
            db.execute('INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?)',
                       (chunk.id, chunk.book, chunk.section_id, chunk.title, chunk.text, _blob(vector)))
            db.executemany('INSERT INTO chunk_concepts VALUES (?, ?)', [(chunk.id, c) for c in concepts])
        for (key, (section, figure, path, concepts)), vector in zip(figures.items(), figure_vectors):
            db.execute('INSERT INTO figures VALUES (?, ?, ?, ?, ?, ?)',
                       (key, section.book, section.section_id, figure.caption, path, _blob(vector)))
            db.executemany('INSERT INTO figure_concepts VALUES (?, ?)', [(key, c) for c in concepts])
    return {'chunks': len(chunks), 'figures': len(figures)}


## @brief rag.fetch가 쓴 sections.jsonl을 Section 목록으로 되읽음
def load_sections(path: Path) -> list[Section]:
    sections = []
    for line in Path(path).read_text().splitlines():
        row = json.loads(line)
        row['figures'] = tuple(FigureRef(**f) for f in row['figures'])
        sections.append(Section(**row))
    return sections


## @brief 색인 명령 진입점: sections.jsonl + 절 대응표 + 환경 변수 임베더로 index.sqlite를 다시 만듦
# - 임시 파일에 다 만든 뒤 os.replace로 바꿔치움. 만드는 중에 검색 중인 서버가 반쯤 만든 색인을 읽지 않게 함
def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(prog='python -m rag.index', description='교재 문단·그림 색인을 다시 만듭니다.').parse_args(argv)
    # 절 대응표 모듈(Task 2)은 별도 PR이라, 함수·테스트만 쓰는 경우에는 없어도 이 모듈을 불러올 수 있게 여기서 읽음
    from rag.section_map import load_section_map

    source = DATA_DIR / 'sections.jsonl'
    if not source.exists():
        print(f'{source}가 없습니다. 먼저 python -m rag.fetch를 실행하세요.', file=sys.stderr)
        return 1
    target = DATA_DIR / 'index.sqlite'
    building = target.with_name('index.sqlite.building')
    counts = build_index(load_sections(source), load_section_map(), Embedder.from_env(), building)
    os.replace(building, target)
    print(f'{target}: 문단 묶음 {counts["chunks"]}개, 그림 {counts["figures"]}개')
    return 0


if __name__ == '__main__':
    sys.exit(main())
