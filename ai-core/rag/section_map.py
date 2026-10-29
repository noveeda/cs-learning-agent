## @file
# @brief 교재 절↔개념 대응표(content/rag/section-map.json)를 읽고, 사람이 검수할 시트(마크다운)를 만듦
# - 대응표는 사람이 내용을 보증하는 데이터라 저장소에 두고, 교재 원문(sections.jsonl)은 Git 밖에서 읽기만 함
# - 색인(Task 3)은 concepts_for로 절에 개념 꼬리표를 붙이고, 하네스는 sections_for로 개념의 근거 절을 찾음
# - 공개 교재에 근거가 없는 개념은 gaps에 이유와 함께 적어, 검색이 비어도 예외 대신 근거 없음으로 처리하게 함
import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from rag.fetch import DATA_DIR
from rag.index import load_sections
from rag.parse import Section
from serving.concept_map import load_concept_map

MAP_PATH = Path(__file__).resolve().parents[1] / 'content' / 'rag' / 'section-map.json'
# 한 개념에 붙이는 절의 상한: 핵심 절만 남겨 검색이 곁가지 절에 묻히지 않고, 사람이 한 화면에서 검수할 수 있게 함
MAX_SECTIONS_PER_CONCEPT = 15
# 검수 시트에 보여 줄 절 본문 앞부분 길이(사람이 절 내용을 가늠하는 용도)
PREVIEW_CHARS = 200


## @brief 대응표 한 줄: 책의 절 하나와 그 절이 정의·핵심 원리를 직접 다루는 개념들
@dataclass(frozen=True)
class SectionEntry:
    book: str
    section_id: str
    concepts: tuple[str, ...]


## @brief 공개 교재에서 근거 절을 찾지 못한 개념과 그 이유
@dataclass(frozen=True)
class Gap:
    concept: str
    reason: str


## @brief 대응표 전체와 두 방향 조회(절→개념, 개념→절)
# - entries 순서를 그대로 지켜, sections_for가 대응표에 적은 순서(핵심 절 먼저)로 절을 돌려줌
@dataclass(frozen=True)
class SectionMap:
    entries: tuple[SectionEntry, ...]
    gaps: tuple[Gap, ...]

    ## @brief 절 하나에 붙은 개념 ID들. 대응표에 없는 절이면 빈 튜플
    def concepts_for(self, book: str, section_id: str) -> tuple[str, ...]:
        for entry in self.entries:
            if entry.book == book and entry.section_id == section_id:
                return entry.concepts
        return ()

    ## @brief 개념 하나의 근거 절 (책, 절 ID) 목록. 빈칸 개념이면 빈 튜플
    def sections_for(self, concept: str) -> tuple[tuple[str, str], ...]:
        return tuple((e.book, e.section_id) for e in self.entries if concept in e.concepts)


## @brief 대응표 JSON을 읽어 SectionMap으로 반환. path가 없으면 저장소의 content/rag/section-map.json
def load_section_map(path: Path | None = None) -> SectionMap:
    data = json.loads((path or MAP_PATH).read_text(encoding='utf-8'))
    return SectionMap(
        entries=tuple(SectionEntry(e['book'], e['section_id'], tuple(e['concepts'])) for e in data['entries']),
        gaps=tuple(Gap(g['concept'], g['reason']) for g in data['gaps']))


## @brief 마크다운 표 칸에 넣을 수 있게 줄바꿈을 공백으로 바꾸고 칸 구분자(|)를 이스케이프
def _cell(text):
    return ' '.join(text.split()).replace('|', '\\|')


## @brief 검수 시트에 보일 절 번호: section_id의 '#' 뒤 번호
# - Wikibooks처럼 '#' 뒤가 절 제목과 같으면 번호가 없는 것이므로 '#' 앞의 페이지 이름을 보여 줌
def _section_label(section_id, title):
    page, _, number = section_id.partition('#')
    return page if number == title else number


## @brief 검수 시트(마크다운)를 out에 씀: 개념별 절 수 요약표 → 빈칸 목록 → 개념별 절 표
# - sections: (책, 절 ID) → Section. 아직 받지 않은 책의 절은 '(절 목록에 없음)'으로 표시
# - 개념 순서와 한국어 제목은 합친 개념 지도를 따름(사용자는 영어 원문을 읽으므로 한국어 제목을 머리에 붙임)
def write_review(section_map: SectionMap, sections: dict[tuple[str, str], Section], out: Path) -> None:
    concepts = load_concept_map().concepts
    gaps = {g.concept: g.reason for g in section_map.gaps}
    lines = ['# 교재 절↔개념 대응표 검수 시트', '',
             '- 절이 개념의 정의나 핵심 원리를 직접 다룰 때만 대응했습니다. 틀린 대응·빠진 절을 표시해 주세요.', '',
             '## 개념별 대응 절 수', '', '| 개념 | ID | 절 수 |', '|---|---|---:|']
    lines += [f'| {c.title} | {c.id} | {len(section_map.sections_for(c.id))} |' for c in concepts]
    lines += ['', '## 빈칸(근거 절 없음)', '']
    lines += [f'- {c.title} ({c.id}): {gaps[c.id]}' for c in concepts if c.id in gaps] or ['- 없음']
    for c in concepts:
        lines += ['', f'## {c.title} ({c.id})', '']
        found = section_map.sections_for(c.id)
        if not found:
            lines.append(f'- 빈칸: {gaps.get(c.id, "대응 절 없음")}')
            continue
        lines += ['| 책 | 절 번호 | 절 제목 | 본문 첫 200자 |', '|---|---|---|---|']
        for book, section_id in found:
            section = sections.get((book, section_id))
            if section is None:
                lines.append(f'| {book} | {_cell(section_id)} | (절 목록에 없음) | |')
                continue
            text = ' '.join(section.text.split())
            preview = text[:PREVIEW_CHARS] + ('…' if len(text) > PREVIEW_CHARS else '')
            label = _section_label(section_id, section.title)
            lines.append(f'| {book} | {_cell(label)} | {_cell(section.title)} | {_cell(preview)} |')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('\n'.join(lines) + '\n', encoding='utf-8')


## @brief 검수 시트 명령 진입점: python -m rag.section_map --review OUT.md
# - OUT은 Git 밖(예: ~/cs-handoff/)에 둠. 시트에 교재 원문 일부가 들어가기 때문
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m rag.section_map', description='절 대응표 검수 시트를 만듭니다.')
    parser.add_argument('--review', type=Path, required=True, help='검수 시트(마크다운) 저장 경로')
    args = parser.parse_args(argv)
    section_map = load_section_map()
    source = DATA_DIR / 'sections.jsonl'
    sections = {(s.book, s.section_id): s for s in load_sections(source)} if source.exists() else {}
    write_review(section_map, sections, args.review)
    print(f'{args.review}: 대응 {len(section_map.entries)}절, 빈칸 {len(section_map.gaps)}개념')
    return 0


if __name__ == '__main__':
    sys.exit(main())
