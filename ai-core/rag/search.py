## @file
# @brief 개념 꼬리표로 거른 교재 문단·그림을 질문과의 코사인 유사도 순으로 고르는 검색기(하네스 5-2 단계)
# - 모델 생성이 없는 단계. 색인(rag.index)이 없거나 개념에 문단이 없으면 예외 대신 빈 결과를 돌려주고,
#   하네스가 이를 "근거 없음"으로 처리함
# - 한 답의 근거는 섞을 수 있는 라이선스 계열 안에서만 고름(CC BY-SA와 CC BY-NC-SA는 함께 쓰지 않음)
import os
import sqlite3
from array import array
from dataclasses import dataclass
from pathlib import Path

from rag.sources import BOOKS

# 다른 계열과 함께 써도 되는 계열(출처 표기만 요구하는 CC BY)
_OPEN_FAMILY = 'by'


## @brief 검색된 근거 문단 하나; score는 질문과의 코사인 유사도
@dataclass(frozen=True)
class Passage:
    id: str
    book: str
    section_id: str
    title: str
    text: str
    score: float


## @brief 검색된 교재 그림 하나; path는 변환된 PNG의 실제 경로
@dataclass(frozen=True)
class Figure:
    id: str
    book: str
    section_id: str
    caption: str
    path: Path
    score: float


## @brief 책의 라이선스 계열: 'by'(CC BY), 'by-sa', 'by-nc-sa'
# - BOOKS에 없거나 위 셋이 아닌 라이선스는 책 id 자체를 계열로 둠. 같은 책과 CC BY하고만 섞이는 가장 보수적인 처리
def license_family(book: str) -> str:
    source = BOOKS.get(book)
    family = source.license.split()[1].lower() if source and source.license.startswith('CC ') else None
    return family if family in ('by', 'by-sa', 'by-nc-sa') else book


## @brief 이미 고른 근거의 계열 집합에 book을 더해도 되는지: CC BY가 아닌 계열은 한 답에 하나만 허용
def _mixable(families, book):
    family = license_family(book)
    return family == _OPEN_FAMILY or families <= {family, _OPEN_FAMILY}


## @brief 색인 파일 하나를 읽는 검색기; 요청마다 읽기 전용 연결을 새로 열어 여러 스레드에서 같이 써도 됨
# - min_score: 이 유사도 미만은 근거로 쓰지 않음. 없으면 RAG_MIN_SCORE(기본 0.0, Task 4 측정으로 정함)
class Retriever:
    def __init__(self, db_path: Path, embedder, min_score: float | None = None):
        self.db_path = Path(db_path)
        self.embedder = embedder
        self.min_score = float(os.getenv('RAG_MIN_SCORE', '0.0')) if min_score is None else min_score

    ## @brief 개념 꼬리표가 붙은 행을 모두 읽음. 색인 파일이 없거나 개념이 비었으면 [] (파일을 새로 만들지 않음)
    def _rows(self, kind, columns, concepts):
        if not concepts or not self.db_path.is_file():
            return []
        marks = ', '.join('?' * len(concepts))
        sql = (f'SELECT DISTINCT {columns}, t.vector FROM {kind}s t JOIN {kind}_concepts c ON c.{kind}_id = t.id '
               f'WHERE c.concept IN ({marks})')
        with sqlite3.connect(f'{self.db_path.as_uri()}?mode=ro', uri=True) as db:
            return db.execute(sql, list(concepts)).fetchall()

    ## @brief 후보 행마다 질문 벡터와의 내적(정규화 벡터라 코사인)을 붙이고, min_score 미만을 빼서 높은 순으로 정렬
    # - 후보가 없으면 임베딩 서버를 부르지 않음. 빈칸 개념 질문마다 불필요한 호출이 생기지 않게 함
    # - 점수가 같으면 id 순. 같은 질문에 늘 같은 근거가 나와야 기록·측정을 다시 재현할 수 있음
    def _ranked(self, rows, query):
        if not rows:
            return []
        [q] = self.embedder.embed_queries([query])
        scored = []
        for *fields, blob in rows:
            score = sum(a * b for a, b in zip(q, array('f', blob)))
            if score >= self.min_score:
                scored.append((score, fields))
        return sorted(scored, key=lambda item: (-item[0], item[1][0]))

    ## @brief 개념 꼬리표가 붙은 문단 중 질문과 가까운 상위 k개를 고름
    # - exclude: 앞 시도에서 이미 쓴 문단 id. [막혔어요] 3차 보충이 다른 문단으로 설명할 때 씀
    # - 높은 순으로 보며 이미 고른 문단과 라이선스 계열을 섞을 수 없는 문단은 건너뛰고 다음 순위를 봄
    def passages(self, concepts: list[str], query: str, k: int = 3, exclude: set[str] = frozenset()) -> list[Passage]:
        rows = [r for r in self._rows('chunk', 't.id, t.book, t.section_id, t.title, t.text', concepts)
                if r[0] not in exclude]
        chosen, families = [], set()
        for score, fields in self._ranked(rows, query):
            if len(chosen) == k:
                break
            if _mixable(families, fields[1]):
                chosen.append(Passage(*fields, score))
                families.add(license_family(fields[1]))
        return chosen

    ## @brief 문단 id 목록으로 문단을 그대로 다시 읽음(주어진 순서 유지, 색인에 없는 id는 건너뜀)
    # - 후속 질문("더 쉽게")이 직전 답의 문단을 이어 쓸 때 씀. 질문에 개념어가 없어 다시 검색하면 엉뚱한 문단이 오르기 때문
    # - 다시 재지 않으므로 score는 0.0. 직전 답에서 이미 라이선스 계열을 맞춰 고른 문단이라 계열 검사도 다시 하지 않음
    def by_ids(self, ids: list[str]) -> list[Passage]:
        if not ids or not self.db_path.is_file():
            return []
        marks = ', '.join('?' * len(ids))
        with sqlite3.connect(f'{self.db_path.as_uri()}?mode=ro', uri=True) as db:
            rows = {r[0]: r for r in db.execute(
                f'SELECT id, book, section_id, title, text FROM chunks WHERE id IN ({marks})', list(ids))}
        return [Passage(*rows[i], 0.0) for i in ids if i in rows]

    ## @brief 개념 꼬리표가 붙은 그림 중 캡션이 질문과 가장 가까운 1개(없으면 None)
    # - compatible_with: 같은 답에 함께 보일 근거 문단. 이 문단들과 라이선스 계열을 섞을 수 없는 그림은 고르지 않음
    def figure(self, concepts: list[str], query: str, compatible_with=()) -> Figure | None:
        families = {license_family(p.book) for p in compatible_with}
        rows = [r for r in self._rows('figure', 't.id, t.book, t.section_id, t.caption, t.path', concepts)
                if _mixable(families, r[1])]
        for score, (figure_id, book, section_id, caption, path) in self._ranked(rows, query)[:1]:
            return Figure(figure_id, book, section_id, caption, self.db_path.parent / path, score)
        return None
