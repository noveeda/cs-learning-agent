## @file
# @brief 진단 문항 지문 옆에 보여 줄 정지 도식의 스키마와 검사기(구조·정답 누설·라벨 길이·겹침)
# - 도식은 문항마다 미리 만들어 문항 은행(items-*.jsonl)의 figure 필드에 저장; 실행 중 모델 호출 없음
# - 좌표를 데이터에 넣어 자동 배치 없이 그림; 프런트 ItemFigure.tsx가 같은 크기 상수로 SVG를 그림
# - 강조·결과 상태 필드는 두지 않음: 최단 경로 표시나 정답 보기 표시처럼 답을 알려 주는 그림을 데이터로 만들 수 없게 함
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 격자 한 칸의 화면 크기(px)와 격자 범위; 좌표는 반 칸 단위라 칸(cell)을 맞닿게 이어 배열·큐를 그릴 수 있음
# - 프런트 ItemFigure.tsx의 같은 이름 상수와 값이 같아야 검사 결과와 실제 그림이 맞음
CELL_W, CELL_H = 100, 72
GRID_COLS, GRID_ROWS = 7, 8
# 모양별 도형 크기(폭, 높이); text는 테두리 없는 짧은 이름표이고 크기는 겹침·폭 검사에만 씀
SHAPES = {'box': (88, 40), 'circle': (44, 44), 'cylinder': (72, 48), 'cell': (50, 36), 'text': (96, 28)}
# 글자 크기(px)와 표 칸 크기; 라벨 폭은 이 글자 크기로 어림해 도형 안에 들어가는지 확인
NODE_FONT, EDGE_FONT, TABLE_FONT = 15, 13, 13
TABLE_COL_W, TABLE_ROW_H, TABLE_NAME_H = 80, 28, 24
# 글 최소화를 위한 분량 상한: 라벨은 짧은 이름·숫자만, 도형·선·표 수는 화면 한 장에 읽히는 정도로 제한
MAX_LABEL, MAX_EDGE_LABEL = 12, 6
# 겹침 판정 여유(px): 맞닿은 칸은 겹침이 아니고, 선이 도형 모서리를 스치는 정도는 허용
TOUCH = 2

Coordinate = Field(ge=0, multiple_of=0.5)


## @brief 도형 하나(정점, 칸, 상자 등); 좌표는 격자 칸 단위이고 도형 중심이 그 칸의 가운데에 옴
class FigureNode(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,24}$')
    label: str = Field(min_length=1, max_length=MAX_LABEL)
    shape: Literal['box', 'circle', 'cylinder', 'cell', 'text']
    x: float = Coordinate
    y: float = Coordinate


## @brief 두 도형(또는 표)을 잇는 선; directed가 참이면 to 쪽에 화살표를 그림
class FigureEdge(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    source: str = Field(alias='from')
    target: str = Field(alias='to')
    label: str | None = Field(default=None, min_length=1, max_length=MAX_EDGE_LABEL)
    directed: bool = True


## @brief 관계·행 목록 같은 표; (x, y) 칸의 왼쪽 위에서 시작해 이름 줄, 열 이름 줄, 행 순서로 그림
# - keys: 키로 밑줄을 그을 열 이름; 지문이 키를 알려 준 경우에만 씀
class FigureTable(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,24}$')
    name: str = Field(min_length=1, max_length=MAX_LABEL)
    columns: list[str] = Field(min_length=1, max_length=5)
    rows: list[list[str]] = Field(default_factory=list, max_length=6)
    keys: list[str] = Field(default_factory=list)
    x: float = Coordinate
    y: float = Coordinate

    @field_validator('columns')
    @classmethod
    def short_columns(cls, columns):
        if any(not c or len(c) > MAX_LABEL for c in columns):
            raise ValueError(f'열 이름은 1~{MAX_LABEL}자여야 합니다.')
        return columns

    @model_validator(mode='after')
    def consistent_table(self):
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError(f'{self.id}: 행마다 열 수와 같은 개수의 값이 있어야 합니다.')
        if any(not value or len(value) > MAX_LABEL for row in self.rows for value in row):
            raise ValueError(f'{self.id}: 표 값은 1~{MAX_LABEL}자여야 합니다.')
        if not set(self.keys) <= set(self.columns):
            raise ValueError(f'{self.id}: 키는 표의 열 이름이어야 합니다.')
        return self


## @brief 문항 하나의 도식 전체; 구조 규칙(ID·선 끝·격자 범위)은 여기서, 지문 대조는 check_figure에서 검사
class Figure(BaseModel):
    model_config = ConfigDict(extra='forbid')
    nodes: list[FigureNode] = Field(default_factory=list, max_length=20)
    edges: list[FigureEdge] = Field(default_factory=list, max_length=24)
    tables: list[FigureTable] = Field(default_factory=list, max_length=3)

    @model_validator(mode='after')
    def consistent_structure(self):
        ids = [n.id for n in self.nodes] + [t.id for t in self.tables]
        if not ids:
            raise ValueError('그릴 도형이나 표가 없습니다. 도식이 없으면 figure를 null로 둡니다.')
        if len(set(ids)) != len(ids):
            raise ValueError('도형·표 ID가 중복됩니다.')
        pairs = [(e.source, e.target) for e in self.edges]
        for source, target in pairs:
            if source not in ids or target not in ids:
                raise ValueError(f'선 {source}→{target}이 없는 도형을 가리킵니다.')
            if source == target:
                raise ValueError(f'선 {source}→{target}은 같은 도형을 잇습니다.')
        if len(set(pairs)) != len(pairs):
            raise ValueError('같은 두 도형을 같은 방향으로 잇는 선이 중복됩니다.')
        for item_id, (left, top, right, bottom) in bounds(self).items():
            if left < 0 or top < 0 or right > GRID_COLS * CELL_W or bottom > GRID_ROWS * CELL_H:
                raise ValueError(f'{item_id}: 격자({GRID_COLS}×{GRID_ROWS}칸) 밖으로 나갑니다.')
        return self


## @brief 라벨 글자 폭 어림값(px); 한글·전각 문자는 글자 크기만큼, 영숫자·기호는 0.6배로 셈
def label_width(text: str, font: int) -> float:
    return sum(font if unicodedata.east_asian_width(ch) in 'WF' else font * 0.6 for ch in text)


## @brief 표의 화면 크기(폭, 높이); 이름 줄 + 열 이름 줄 + 행 수만큼 높이를 잡음
def table_size(table: FigureTable) -> tuple[int, int]:
    return len(table.columns) * TABLE_COL_W, TABLE_NAME_H + TABLE_ROW_H * (1 + len(table.rows))


## @brief 도형·표마다 화면 경계 상자(왼쪽, 위, 오른쪽, 아래) 계산; 겹침·격자 범위·선 통과 검사가 함께 씀
# - 도형은 칸 가운데에 중심을 두고, 표는 칸의 왼쪽 위 모서리에서 시작함
def bounds(figure: Figure) -> dict[str, tuple[float, float, float, float]]:
    boxes = {}
    for node in figure.nodes:
        width, height = SHAPES[node.shape]
        cx, cy = (node.x + 0.5) * CELL_W, (node.y + 0.5) * CELL_H
        boxes[node.id] = (cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2)
    for table in figure.tables:
        width, height = table_size(table)
        boxes[table.id] = (table.x * CELL_W, table.y * CELL_H, table.x * CELL_W + width, table.y * CELL_H + height)
    return boxes


## @brief 정답 누설 검사용 정규화; 공백·대소문자·전각/반각 차이만 없애고 글자 자체는 바꾸지 않음
def _normalize(text: str) -> str:
    return ''.join(unicodedata.normalize('NFKC', text).split()).lower()


## @brief 선분이 상자를 지나는지 판정(Liang–Barsky 선분 자르기); 양 끝이 아닌 도형을 지나는 선을 찾는 데 씀
def _crosses(x1, y1, x2, y2, box) -> bool:
    left, top, right, bottom = box
    t0, t1 = 0.0, 1.0
    for p, q in ((x1 - x2, x1 - left), (x2 - x1, right - x1), (y1 - y2, y1 - top), (y2 - y1, bottom - y1)):
        if p == 0:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    return True


## @brief 두 선분이 서로의 안쪽에서 엇갈리는지 판정(방향 부호 비교); 끝점을 공유하거나 맞닿기만 하는 경우는 교차가 아님
def _segments_cross(a, b, c, d) -> bool:
    def side(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0


## @brief 도식을 지문과 대조해 오류 문장 목록 반환(빈 목록이면 통과)
# - 누설: 모든 라벨·표 글이 지문(question)에 그대로 있어야 함; 계산 결과·정답 보기 글을 그려 넣는 것을 막음
#   선택지 글은 허용하지 않음: 보기 하나만 그림에 옮기면 그 보기가 답이라는 신호가 되기 때문
# - 라벨 폭: 어림 폭이 도형·표 칸 안에 들어가야 함
# - 겹침: 도형·표 경계 상자가 겹치면 안 되고(맞닿음은 허용), 선이 양 끝이 아닌 도형을 지나거나 다른 선과 엇갈리면 안 됨
#   선이 엇갈리면 가운데에 놓이는 선 라벨끼리 겹쳐 어느 값이 어느 선인지 헷갈리기 때문
def check_figure(figure: Figure, question: str) -> list[str]:
    errors = []
    allowed = _normalize(question)
    texts = [(f'도형 {n.id}', n.label) for n in figure.nodes]
    texts += [(f'선 {e.source}→{e.target}', e.label) for e in figure.edges if e.label]
    for table in figure.tables:
        texts += [(f'표 {table.id}', value) for value in (table.name, *table.columns, *(v for row in table.rows for v in row))]
    errors += [f'{where}: 라벨 "{text}"이(가) 지문에 없습니다.' for where, text in texts if _normalize(text) not in allowed]

    for node in figure.nodes:
        width = SHAPES[node.shape][0] - 6
        if label_width(node.label, NODE_FONT) > width:
            errors.append(f'도형 {node.id}: 라벨 "{node.label}"이(가) {node.shape} 폭보다 넓습니다.')
    for table in figure.tables:
        if label_width(table.name, NODE_FONT) > table_size(table)[0]:
            errors.append(f'표 {table.id}: 이름 "{table.name}"이(가) 표 폭보다 넓습니다.')
        for value in (*table.columns, *(v for row in table.rows for v in row)):
            if label_width(value, TABLE_FONT) > TABLE_COL_W - 8:
                errors.append(f'표 {table.id}: 값 "{value}"이(가) 칸 폭보다 넓습니다.')

    boxes = bounds(figure)
    names = list(boxes)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            (l1, t1, r1, b1), (l2, t2, r2, b2) = boxes[a], boxes[b]
            if l1 < r2 - TOUCH and l2 < r1 - TOUCH and t1 < b2 - TOUCH and t2 < b1 - TOUCH:
                errors.append(f'{a}와(과) {b}가 겹칩니다.')
    centers = {k: ((left + right) / 2, (top + bottom) / 2) for k, (left, top, right, bottom) in boxes.items()}
    for i, edge in enumerate(figure.edges):
        (x1, y1), (x2, y2) = centers[edge.source], centers[edge.target]
        for other_edge in figure.edges[i + 1:]:
            if _segments_cross(centers[edge.source], centers[edge.target],
                               centers[other_edge.source], centers[other_edge.target]):
                errors.append(f'선 {edge.source}→{edge.target}과(와) 선 {other_edge.source}→{other_edge.target}이(가) 교차합니다.')
        for other, (left, top, right, bottom) in boxes.items():
            if other not in (edge.source, edge.target) and _crosses(
                    x1, y1, x2, y2, (left + TOUCH, top + TOUCH, right - TOUCH, bottom - TOUCH)):
                errors.append(f'선 {edge.source}→{edge.target}이(가) 다른 도형 {other}를 지나갑니다.')
    return errors
