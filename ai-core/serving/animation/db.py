## @file
# @brief 데이터베이스 과목 장면(표 연산·속성 닫힘·조인·B-트리·해시 인덱스·트랜잭션)의 요청 스키마와 결정적 시뮬레이터
# - 요청은 model.py의 Strict 규칙(모르는 필드·자동 변환 거부)을 따르고, 단계별 상태는 모두 여기서 실제로 계산함
# - 공유 파일에는 등록만 함: model.py가 DB_REQUESTS를 요청 합집합에, __init__.py가 DB_SIMULATORS·DB_SCENE_FORMATS를 표에 넣음
# - model.py가 자기 정의를 끝낸 뒤 이 모듈을 가져오므로, 여기서는 model.py에 이미 정의된 이름만 씀(순환 import 방지)
# - 실행할 수 없는 연산(없는 표·열, 키 위반, 시작하지 않은 트랜잭션)과 그릴 수 없는 크기는 한국어 ValueError로 거부
"""Database scenes: tables, attribute closure, joins, B-tree, hash index and transactions."""
import operator
from bisect import bisect_left, bisect_right, insort
from dataclasses import dataclass, field
from itertools import combinations, count
from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from .hierarchical import CHAIN_MAX, hash_frame
from .linear import tone_of
from .model import GRID_HEIGHT, GRID_WIDTH, Animation, Arrow, Box, Frame, Strict, Value, build_animation, josa

# 표 칸 값: 상자 안에 들어가는 정수(-99~999) 또는 네 글자 이내 문자열
Text = Annotated[str, Field(min_length=1, max_length=4)]
Cell = Union[Value, Text]
# 표·열 이름: 테두리 없는 머리글(label)로 한 칸 폭 안에 읽히는 다섯 글자 이내
Name = Annotated[str, Field(min_length=1, max_length=5)]
Cmp = Literal['=', '!=', '<', '<=', '>', '>=']
COMPARE = {'=': operator.eq, '!=': operator.ne, '<': operator.lt, '<=': operator.le, '>': operator.gt, '>=': operator.ge}


## @brief 두 값을 비교 연산자로 비교. 크기 비교는 정수끼리만 허용(문자열 대소는 학습 범위 밖이라 오해를 막음)
def compare(left, cmp: str, right, what: str) -> bool:
    if cmp not in ('=', '!=') and not (isinstance(left, int) and isinstance(right, int)):
        raise ValueError(f'{what}: 크기 비교({cmp})는 정수 값에만 쓸 수 있습니다')
    return COMPARE[cmp](left, right)


## @brief 값 목록을 '(1, Kim)'처럼 캡션용 튜플 글자로 바꿈
def tuple_text(values) -> str:
    return '(' + ', '.join(str(v) for v in values) + ')'


# --- 표(db_table): 키 검사·삽입 제약·WHERE·GROUP BY·HAVING·분해 ---

## @brief 외래 키: column 값이 table의 기본 키(한 열) 값 중 하나여야 함(참조 무결성)
class ForeignKey(Strict):
    column: Name
    table: Name


## @brief 표 하나의 정의. 열 4개·행 5개는 이름·머리글 두 줄 아래 세로 6칸에 삽입 1행까지 들어가는 분량
# - key는 기본 키 열 목록(없으면 키 제약 없음). 초기 행도 키·행 길이 규칙을 지켜야 함
class TableSpec(Strict):
    name: Name
    columns: list[Name] = Field(min_length=1, max_length=4)
    key: list[Name] = Field(default_factory=list, max_length=4)
    foreign: ForeignKey | None = None
    rows: list[list[Cell]] = Field(max_length=5)

    ## @brief 열 이름 중복, 열 목록에 없는 키·외래 키 열, 열 수와 다른 행, 초기 기본 키 중복을 거부
    @model_validator(mode='after')
    def validate_table(self):
        if len(set(self.columns)) != len(self.columns):
            raise ValueError(f'{self.name}: 열 이름이 중복되었습니다')
        for column in self.key + ([self.foreign.column] if self.foreign else []):
            if column not in self.columns:
                raise ValueError(f'{self.name}: 열 목록에 없는 이름입니다: {column}')
        for row in self.rows:
            if len(row) != len(self.columns):
                raise ValueError(f'{self.name}: 행 {tuple_text(row)}의 값 수가 열 수({len(self.columns)})와 다릅니다')
        if self.key:
            index = [self.columns.index(c) for c in self.key]
            keys = [tuple(row[i] for i in index) for row in self.rows]
            if len(set(keys)) != len(keys):
                raise ValueError(f'{self.name}: 초기 행의 기본 키가 중복되었습니다')
        return self


## @brief 속성 집합이 표의 행을 하나씩 구별하는지(키가 될 수 있는지) 검사하는 연산
class CheckKeyOp(Strict):
    op: Literal['check_key']
    table: Name
    columns: list[Name] = Field(min_length=1, max_length=3)


## @brief 행 하나를 넣는 연산. 기본 키 중복·없는 부모 키 참조면 거부 단계로 그림
class InsertRowOp(Strict):
    op: Literal['insert']
    table: Name
    row: list[Cell] = Field(min_length=1, max_length=4)


## @brief WHERE 행 조건. 행마다 한 단계씩 검사해 남길 행과 뺄 행을 보여 줌
class WhereOp(Strict):
    op: Literal['where']
    table: Name
    column: Name
    cmp: Cmp
    value: Cell


## @brief GROUP BY + 집계 함수. count는 열 없이 행 수를 세고, sum·max·min은 정수 열(column)이 필요함
class GroupOp(Strict):
    op: Literal['group']
    table: Name
    by: Name
    agg: Literal['count', 'sum', 'max', 'min']
    column: Name | None = None


## @brief HAVING 그룹 조건. 바로 앞 group 결과의 집계 값에 적용
class HavingOp(Strict):
    op: Literal['having']
    cmp: Cmp
    value: Value


## @brief 정규화 분해: 표의 일부 열만 뽑아 중복 행을 없앤 새 표(name)를 만듦
class ProjectOp(Strict):
    op: Literal['project']
    table: Name
    columns: list[Name] = Field(min_length=1, max_length=3)
    name: Name


## @brief 표 장면. 처음 표 2개·연산 6개는 가로 12칸에 결과 표까지 놓고 따라갈 수 있는 분량
class TableRequest(Strict):
    kind: Literal['db_table']
    tables: list[TableSpec] = Field(min_length=1, max_length=2)
    ops: list[Annotated[Union[CheckKeyOp, InsertRowOp, WhereOp, GroupOp, HavingOp, ProjectOp],
                        Field(discriminator='op')]] = Field(min_length=1, max_length=6)

    ## @brief 표 이름 중복, 없는 표를 가리키는 외래 키, 한 열 기본 키가 아닌 참조 대상, 초기 행의 참조 위반을 거부
    @model_validator(mode='after')
    def validate_tables(self):
        tables = {t.name: t for t in self.tables}
        if len(tables) != len(self.tables):
            raise ValueError('표 이름이 중복되었습니다')
        for table in self.tables:
            if not table.foreign:
                continue
            parent = tables.get(table.foreign.table)
            if parent is None or parent is table:
                raise ValueError(f'{table.name}: 외래 키가 가리키는 표 {table.foreign.table}이(가) 없습니다')
            if len(parent.key) != 1:
                raise ValueError(f'{table.name}: 참조 대상 {parent.name}의 기본 키는 한 열이어야 합니다')
            keys = {row[parent.columns.index(parent.key[0])] for row in parent.rows}
            column = table.columns.index(table.foreign.column)
            if any(row[column] not in keys for row in table.rows):
                raise ValueError(f'{table.name}: 초기 행이 {parent.name}에 없는 키를 참조합니다')
        return self


## @brief 시뮬레이션 중인 표. rows는 (행 id, 값 목록)이고 행 id는 프레임 사이에서 같은 행을 같은 상자로 잇는 데 씀
@dataclass
class Relation:
    name: str
    columns: list
    key: list = field(default_factory=list)
    foreign: ForeignKey | None = None
    rows: list = field(default_factory=list)

    ## @brief 열 이름의 위치. 없으면 연산 이름을 담은 한국어 오류
    def index(self, column: str, what: str) -> int:
        if column not in self.columns:
            raise ValueError(f'{what}: {self.name}에 없는 열입니다: {column}')
        return self.columns.index(column)


## @brief 이름으로 표를 찾음
def find_relation(relations, name: str, what: str) -> Relation:
    for relation in relations:
        if relation.name == name:
            return relation
    raise ValueError(f'{what}: 없는 표입니다: {name}')


## @brief 표들을 왼쪽부터 한 칸씩 띄워 그림: y=0 표 이름, y=1 열 머리글, y=2부터 행
# - marks: 행 id → 행 전체 tone, (행 id, 열 번호) → 칸 tone(칸 tone이 우선)
# - ghosts: 표 이름 → 상태에는 없지만 이번 단계에만 removed로 보여 줄 행(거부된 삽입, 분해에서 빠진 중복 행)
# - 외래 키는 참조하는 열 머리글 → 참조되는 기본 키 머리글 화살표(FK)로 그림
def relation_frame(relations, caption, marks=None, ghosts=None) -> Frame:
    marks, ghosts = marks or {}, ghosts or {}
    boxes, headers, left = [], {}, 0
    for t, relation in enumerate(relations):
        if left + len(relation.columns) > GRID_WIDTH:
            raise ValueError(f'표가 가로 {GRID_WIDTH}칸을 넘습니다. 열이나 표 수를 줄이세요')
        rows = relation.rows + [(rid, values, 'removed') for rid, values in ghosts.get(relation.name, [])]
        if 2 + len(rows) > GRID_HEIGHT:
            raise ValueError(f'{relation.name}: 행이 세로 {GRID_HEIGHT - 2}칸을 넘습니다. 행 수를 줄이세요')
        boxes.append(Box(id=f't{t}', label=relation.name, x=left, y=0, shape='label'))
        for c, column in enumerate(relation.columns):
            headers[(relation.name, column)] = f't{t}h{c}'
            boxes.append(Box(id=f't{t}h{c}', label=column, x=left + c, y=1, shape='label'))
        for r, (rid, values, *ghost) in enumerate(rows):
            for c, value in enumerate(values):
                tone = ghost[0] if ghost else marks.get((rid, c), marks.get(rid, 'normal'))
                boxes.append(Box(id=f'{rid}c{c}', label=str(value), x=left + c, y=2 + r, shape='cell', tone=tone))
        left += len(relation.columns) + 1
    arrows = [Arrow(source=headers[(r.name, r.foreign.column)],
                    target=headers[(r.foreign.table, find_relation(relations, r.foreign.table, 'FK').key[0])], label='FK')
              for r in relations if r.foreign]
    return Frame(boxes=boxes, arrows=arrows, caption=caption)


## @brief 속성 목록 값이 모든 행에서 다른지 확인
def unique_on(relation: Relation, index) -> bool:
    values = [tuple(row[i] for i in index) for _, row in relation.rows]
    return len(set(values)) == len(values)


## @brief 키 검사 단계. 겹치는 값은 removed, 나머지 검사 칸은 active
# - 유일하더라도 더 작은 부분집합이 이미 유일하면 최소가 아니라고 알림(후보 키 = 유일 + 최소)
# - 표에 담긴 행만 보고 판단하므로, 모두 다르면 "키가 될 수 있음"으로만 말함(키는 스키마 제약으로 확정)
def check_key_step(relation: Relation, op: CheckKeyOp):
    index = [relation.index(c, 'check_key') for c in op.columns]
    groups = {}
    for rid, row in relation.rows:
        groups.setdefault(tuple(row[i] for i in index), []).append(rid)
    marks = {(rid, i): 'active' for rid, _ in relation.rows for i in index}
    duplicates = [(value, rids) for value, rids in groups.items() if len(rids) > 1]
    for _, rids in duplicates:
        marks.update({(rid, i): 'removed' for rid in rids for i in index})
    name = '{' + ', '.join(op.columns) + '}'
    if duplicates:
        value, rids = duplicates[0]
        return marks, f'{name}: {", ".join(map(str, value))} 값이 {len(rids)}행에 겹침 → 키가 될 수 없음'
    smaller = next((sub for size in range(1, len(index)) for sub in combinations(range(len(index)), size)
                    if unique_on(relation, [index[i] for i in sub])), None)
    if smaller:
        sub = '{' + ', '.join(op.columns[i] for i in smaller) + '}'
        return marks, f'{name}: {len(relation.rows)}행 모두 다름, 하지만 {sub}만으로도 구별 → 최소가 아님'
    return marks, f'{name}: {len(relation.rows)}행 모두 다름 → 후보 키가 될 수 있음'


## @brief 삽입 단계. 기본 키 중복·참조 무결성 위반이면 거부 이유를, 아니면 None과 강조할 부모 행 id를 반환
def insert_violation(relations, relation: Relation, row):
    if relation.key:
        index = [relation.columns.index(c) for c in relation.key]
        key = [row[i] for i in index]
        if any([values[i] for i in index] == key for _, values in relation.rows):
            return f'기본 키 {", ".join(map(str, key))} 중복', None
    if relation.foreign:
        parent = find_relation(relations, relation.foreign.table, 'insert')
        value = row[relation.columns.index(relation.foreign.column)]
        key = parent.columns.index(parent.key[0])
        match = next((rid for rid, values in parent.rows if values[key] == value), None)
        if match is None:
            return f'{parent.name}에 {value} 없음(참조 무결성)', None
        return None, match
    return None, None


## @brief 표 장면 실행. 연산 하나마다 한 단계, WHERE는 행마다 한 단계, GROUP BY는 모으기 + 그룹마다 한 단계
def simulate_table(req: TableRequest) -> Animation:
    ids = count()
    relations = [Relation(t.name, list(t.columns), list(t.key), t.foreign, [(f'r{next(ids)}', list(row)) for row in t.rows])
                 for t in req.tables]
    parts = [f'{r.name} {len(r.rows)}행' + (f'(기본 키 {", ".join(r.key)})' if r.key else '') for r in relations]
    links = [f'{r.name}.{r.foreign.column} → {r.foreign.table}' for r in relations if r.foreign]
    frames = [relation_frame(relations, '표 ' + ', '.join(parts) + (f', 외래 키 {links[0]}' if links else ''))]
    grouped = None  # (결과 표, 집계 이름): HAVING이 적용될 바로 앞 GROUP BY 결과
    for op in req.ops:
        if op.op == 'check_key':
            relation = find_relation(relations, op.table, 'check_key')
            marks, caption = check_key_step(relation, op)
            frames.append(relation_frame(relations, caption, marks=marks))
        elif op.op == 'insert':
            relation = find_relation(relations, op.table, 'insert')
            if len(op.row) != len(relation.columns):
                raise ValueError(f'insert {relation.name}: 값 수가 열 수({len(relation.columns)})와 다릅니다')
            row = (f'r{next(ids)}', list(op.row))
            reason, parent = insert_violation(relations, relation, op.row)
            text = f'insert {relation.name} {tuple_text(op.row)}'
            if reason:
                frames.append(relation_frame(relations, f'{text} 거부: {reason}', ghosts={relation.name: [row]}))
            else:
                relation.rows.append(row)
                marks = {row[0]: 'new', **({parent: 'active'} if parent else {})}
                frames.append(relation_frame(relations, f'{text}: 제약을 지켜 추가', marks=marks))
        elif op.op == 'where':
            relation = find_relation(relations, op.table, 'where')
            column = relation.index(op.column, 'where')
            condition = f'{op.column} {op.cmp} {op.value}'
            kept, dropped = [], []
            for number, (rid, values) in enumerate(relation.rows, start=1):
                passed = compare(values[column], op.cmp, op.value, f'WHERE {condition}')
                (kept if passed else dropped).append(rid)
                marks = {**{r: 'removed' for r in dropped}, rid: 'active' if passed else 'removed'}
                caption = f'WHERE {condition}: {number}행 {op.column}={values[column]} → {"남김" if passed else "제외"}'
                if number == len(relation.rows):
                    caption += f' (남은 행 {len(kept)}개)'
                frames.append(relation_frame(relations, caption, marks=marks))
            relation.rows = [row for row in relation.rows if row[0] in kept]
        elif op.op == 'group':
            if grouped:
                raise ValueError('group: 한 장면에는 GROUP BY를 한 번만 씁니다')
            relation = find_relation(relations, op.table, 'group')
            by = relation.index(op.by, 'group')
            if op.agg != 'count' and op.column is None:
                raise ValueError(f'group: {op.agg}에는 집계할 열(column)이 필요합니다')
            target = relation.index(op.column, 'group') if op.agg != 'count' else None
            if target is not None and not all(isinstance(values[target], int) for _, values in relation.rows):
                raise ValueError(f'group: {op.agg}는 정수 열에만 쓸 수 있습니다')
            order = list(dict.fromkeys(values[by] for _, values in relation.rows))
            relation.rows.sort(key=lambda row: order.index(row[1][by]))
            frames.append(relation_frame(relations, f'GROUP BY {op.by}: 같은 {op.by} 행끼리 모음 → {len(order)}그룹',
                                         marks={(rid, by): 'active' for rid, _ in relation.rows}))
            label = op.agg.upper()
            result = Relation('결과', [op.by, label])
            relations.append(result)
            for group in order:
                members = [(rid, values) for rid, values in relation.rows if values[by] == group]
                value = (len(members) if target is None
                         else {'sum': sum, 'max': max, 'min': min}[op.agg](values[target] for _, values in members))
                result.rows.append((f'r{next(ids)}', [group, value]))
                marks = {**{rid: 'active' for rid, _ in members}, result.rows[-1][0]: 'new'}
                what = f'{label}({op.column or "*"})'
                frames.append(relation_frame(relations, f'{group} 그룹 {len(members)}행 → {what} = {value}', marks=marks))
            grouped = (result, label)
        elif op.op == 'having':
            if not grouped:
                raise ValueError('having: 먼저 group으로 그룹을 만들어야 합니다')
            result, label = grouped
            passed = {rid for rid, values in result.rows if compare(values[1], op.cmp, op.value, 'HAVING')}
            marks = {rid: 'active' if rid in passed else 'removed' for rid, _ in result.rows}
            frames.append(relation_frame(
                relations, f'HAVING {label} {op.cmp} {op.value}: 그룹 조건으로 {len(passed)}그룹만 남김', marks=marks))
            result.rows = [row for row in result.rows if row[0] in passed]
        else:
            relation = find_relation(relations, op.table, 'project')
            if any(r.name == op.name for r in relations):
                raise ValueError(f'project: 이미 있는 표 이름입니다: {op.name}')
            index = [relation.index(c, 'project') for c in op.columns]
            part, seen, duplicates = Relation(op.name, list(op.columns)), set(), []
            for _, values in relation.rows:
                picked = [values[i] for i in index]
                target = duplicates if tuple(picked) in seen else part.rows
                target.append((f'r{next(ids)}', picked))
                seen.add(tuple(picked))
            relations.append(part)
            marks = {**{(rid, i): 'active' for rid, _ in relation.rows for i in index}, **{rid: 'new' for rid, _ in part.rows}}
            caption = f'{op.name}({", ".join(op.columns)})로 분해: ' + (f'같은 행 {len(duplicates)}행 제거' if duplicates
                                                                         else '겹치는 행 없음')
            frames.append(relation_frame(relations, caption, marks=marks, ghosts={op.name: duplicates}))
    return build_animation('db_table', '표 연산', frames)


# --- 속성 닫힘(db_closure): 함수 종속으로 X+를 넓혀 후보 키 판단 ---

# 속성 이름: 대문자 한 글자(교재의 R(A, B, C) 표기)
Attr = Annotated[str, Field(pattern=r'^[A-Z]$')]


## @brief 함수 종속 lhs → rhs. 왼쪽 속성 값이 같으면 오른쪽 속성 값도 같음
class Dependency(Strict):
    lhs: list[Attr] = Field(min_length=1, max_length=3)
    rhs: list[Attr] = Field(min_length=1, max_length=3)


## @brief 속성 닫힘 장면. 속성 6개·종속 5개·시작 집합 2개는 격자 한 화면(종속 한 줄씩)에 들어가는 분량
class ClosureRequest(Strict):
    kind: Literal['db_closure']
    attributes: list[Attr] = Field(min_length=2, max_length=6)
    fds: list[Dependency] = Field(min_length=1, max_length=5)
    starts: list[Annotated[list[Attr], Field(min_length=1, max_length=3)]] = Field(min_length=1, max_length=2)

    ## @brief 속성 이름 중복과 속성 목록에 없는 이름을 쓴 종속·시작 집합을 거부
    @model_validator(mode='after')
    def validate_attributes(self):
        if len(set(self.attributes)) != len(self.attributes):
            raise ValueError('속성 이름이 중복되었습니다')
        used = [a for fd in self.fds for a in fd.lhs + fd.rhs] + [a for start in self.starts for a in start]
        unknown = sorted(set(used) - set(self.attributes))
        if unknown:
            raise ValueError(f'속성 목록에 없는 이름입니다: {", ".join(unknown)}')
        return self


## @brief 집합을 '{A, B}'처럼 속성 순서대로 표기
def attr_set(attrs, order) -> str:
    return '{' + ', '.join(a for a in order if a in attrs) + '}'


## @brief 종속을 'AB→C'처럼 표기
def fd_text(fd: Dependency) -> str:
    return f'{"".join(fd.lhs)}→{"".join(fd.rhs)}'


## @brief 고정점까지 종속을 적용한 속성 닫힘(최소성 판단용 보조 계산)
def closure_of(start, fds) -> set:
    result, changed = set(start), True
    while changed:
        changed = False
        for fd in fds:
            if set(fd.lhs) <= result and not set(fd.rhs) <= result:
                result |= set(fd.rhs)
                changed = True
    return result


## @brief 속성 닫힘 화면: y=0 R의 속성(점선 slot), y=1 지금까지의 X+, y=3부터 종속 한 줄씩(왼쪽 → 오른쪽 화살표)
def closure_frame(req: ClosureRequest, start, closure, caption, new=(), fd=None) -> Frame:
    column = {a: 1 + i for i, a in enumerate(req.attributes)}
    boxes = [Box(id='rel', label='R', x=0, y=0, shape='label'),
             Box(id='xp', label=f'{"".join(start)}+', x=0, y=1, shape='label'),
             Box(id='fd', label='FD', x=0, y=3, shape='label')]
    boxes += [Box(id=f'a{a}', label=a, x=column[a], y=0, shape='slot') for a in req.attributes]
    boxes += [Box(id=f'c{a}', label=a, x=column[a], y=1, shape='cell', tone='new' if a in new else 'normal')
              for a in req.attributes if a in closure]
    arrows = []
    for i, dep in enumerate(req.fds):
        tone = 'active' if i == fd else 'normal'
        left = [Box(id=f'f{i}l{j}', label=a, x=1 + j, y=3 + i, shape='cell', tone=tone) for j, a in enumerate(dep.lhs)]
        right = [Box(id=f'f{i}r{j}', label=a, x=5 + j, y=3 + i, shape='cell', tone=tone) for j, a in enumerate(dep.rhs)]
        boxes += left + right
        arrows.append(Arrow(source=left[-1].id, target=right[0].id))
    return Frame(boxes=boxes, arrows=arrows, caption=caption)


## @brief 시작 집합마다 X+를 한 종속씩 넓히고, 더 늘지 않으면 전체 속성 결정 여부와 최소성으로 키를 판단
# - 종속은 목록 순서대로 반복 적용(결정적 순서). 새 속성을 더하지 못하는 종속은 단계를 만들지 않음
def simulate_closure(req: ClosureRequest) -> Animation:
    frames, order = [], req.attributes
    for start in req.starts:
        closure, name = set(start), attr_set(start, order)
        frames.append(closure_frame(req, start, closure, f'{name}+ 계산 시작: 자기 자신 {name}', new=start))
        changed = True
        while changed:
            changed = False
            for i, fd in enumerate(req.fds):
                if set(fd.lhs) <= closure and not set(fd.rhs) <= closure:
                    added = [a for a in fd.rhs if a not in closure]
                    closure |= set(added)
                    frames.append(closure_frame(req, start, closure, f'{fd_text(fd)}: {attr_set(fd.lhs, order)} ⊆ {name}+ '
                                                f'→ {", ".join(added)} 추가', new=added, fd=i))
                    changed = True
        missing = [a for a in order if a not in closure]
        if missing:
            caption = f'{name}+ = {attr_set(closure, order)}: {", ".join(missing)} 없음 → 키 아님'
        else:
            smaller = next((sub for size in range(1, len(start)) for sub in combinations(start, size)
                            if closure_of(sub, req.fds) >= set(order)), None)
            caption = (f'{name}+ = 전체 속성, 하지만 {attr_set(smaller, order)}만으로도 충분 → 최소 아님' if smaller
                       else f'{name}+ = 전체 속성이고 더 줄일 수 없음 → 후보 키')
        frames.append(closure_frame(req, start, closure, caption))
    return build_animation('db_closure', '속성 닫힘과 후보 키', frames)


# --- 조인(db_join): 중첩 반복·정렬-병합 동등 조인 ---

## @brief 조인 입력 행 하나: 조인 키와 세 글자 이내 값
class JoinRow(Strict):
    key: int = Field(ge=0, le=99)
    val: Annotated[str, Field(min_length=1, max_length=3)]


## @brief 동등 조인 장면(R.key = S.key). 입력 5행씩은 세로 칸 안, 결과는 결과 칸 7행까지
class JoinRequest(Strict):
    kind: Literal['db_join']
    algorithm: Literal['nested_loop', 'sort_merge']
    left: list[JoinRow] = Field(min_length=1, max_length=5)
    right: list[JoinRow] = Field(min_length=1, max_length=5)


# 결과 칸 수: 머리글(y=0) 아래 세로 7칸
JOIN_MAX_RESULTS = GRID_HEIGHT - 1


## @brief 조인 화면: R(x=1~2)·S(x=5~6)·결과(x=8~10)를 y=1부터 한 행씩, 정렬-병합의 포인터 i·j는 각 표 왼쪽 칸에 둠
# - active: 이번 단계에서 비교한 행 id, matched: 짝을 찾은 행 id, fresh: 이번 단계에 생긴 결과 수
def join_frame(left, right, out, caption, active=(), matched=(), fresh=0, i=None, j=None) -> Frame:
    boxes = [Box(id='Rt', label='R', x=1, y=0, shape='label'), Box(id='St', label='S', x=5, y=0, shape='label'),
             Box(id='Ot', label='R⋈S', x=9, y=0, shape='label')]
    for side, rows, x in (('R', left, 1), ('S', right, 5)):
        for y, (rid, row) in enumerate(rows, start=1):
            tone = 'new' if rid in matched else tone_of(rid, active)
            boxes += [Box(id=f'{rid}k', label=str(row.key), x=x, y=y, shape='cell', tone=tone),
                      Box(id=f'{rid}v', label=row.val, x=x + 1, y=y, shape='cell', tone=tone)]
    for y, (key, a, b) in enumerate(out, start=1):
        tone = 'new' if y > len(out) - fresh else 'normal'
        boxes += [Box(id=f'o{y}k', label=str(key), x=8, y=y, shape='cell', tone=tone),
                  Box(id=f'o{y}r', label=a, x=9, y=y, shape='cell', tone=tone),
                  Box(id=f'o{y}s', label=b, x=10, y=y, shape='cell', tone=tone)]
    if i is not None:
        boxes += [Box(id='pi', label='i', x=0, y=1 + i, shape='label'), Box(id='pj', label='j', x=4, y=1 + j, shape='label')]
    return Frame(boxes=boxes, arrows=[], caption=caption)


## @brief 중첩 반복: 바깥 R 행마다 S 전체와 비교. 비교 수를 누적해 |R|×|S|가 되는 것을 보여 줌
def nested_loop(left, right, out, frames):
    comparisons = 0
    for rid, r in left:
        hits = [(sid, s) for sid, s in right if s.key == r.key]
        comparisons += len(right)
        out += [(r.key, r.val, s.val) for _, s in hits]
        frames.append(join_frame(left, right, out, f'R key {r.key}: S {len(right)}행 모두 비교 → 일치 {len(hits)}개 '
                                 f'(누적 비교 {comparisons}번)', active=[rid] + [sid for sid, _ in right],
                                 matched=[sid for sid, _ in hits], fresh=len(hits)))
    frames.append(join_frame(left, right, out, f'끝: 비교 {len(left)}×{len(right)} = {comparisons}번, 결과 {len(out)}행'))


## @brief 정렬-병합: 두 입력을 key 순으로 정렬한 뒤 포인터 i·j를 앞으로만 옮기며 비교
# - 같은 key 묶음을 만나면 R 묶음 × S 묶음의 모든 조합을 결과로 냄(중복 키 처리)
def sort_merge(left, right, out, frames):
    if left != sorted(left, key=lambda row: row[1].key) or right != sorted(right, key=lambda row: row[1].key):
        left.sort(key=lambda row: row[1].key)
        right.sort(key=lambda row: row[1].key)
        frames.append(join_frame(left, right, out, '두 입력을 key 순으로 정렬(정렬-병합 조인의 전제)', i=0, j=0))
    i = j = 0
    while i < len(left) and j < len(right):
        (rid, r), (sid, s) = left[i], right[j]
        if r.key != s.key:
            move = 'i' if r.key < s.key else 'j'
            sign = '<' if r.key < s.key else '>'
            frames.append(join_frame(left, right, out, f'{r.key} {sign} {s.key} → 작은 쪽 {move}를 다음 행으로',
                                     active=[rid, sid], i=i, j=j))
            i, j = (i + 1, j) if move == 'i' else (i, j + 1)
            continue
        i2 = next((n for n in range(i, len(left)) if left[n][1].key != r.key), len(left))
        j2 = next((n for n in range(j, len(right)) if right[n][1].key != r.key), len(right))
        pairs = [(r.key, a.val, b.val) for _, a in left[i:i2] for _, b in right[j:j2]]
        out += pairs
        frames.append(join_frame(left, right, out, f'key {r.key} 같음: R {i2 - i}행 × S {j2 - j}행 → {len(pairs)}개 조합',
                                 matched=[x for x, _ in left[i:i2] + right[j:j2]], fresh=len(pairs), i=i, j=j))
        i, j = i2, j2
    done = 'R' if i == len(left) else 'S'
    frames.append(join_frame(left, right, out, f'{done} 끝 → 종료, 결과 {len(out)}행', i=i, j=j))


## @brief 조인 장면 실행. 결과 행 수를 먼저 계산해 결과 칸을 넘으면 그리기 전에 거부
def simulate_join(req: JoinRequest) -> Animation:
    total = sum(1 for r in req.left for s in req.right if r.key == s.key)
    if total > JOIN_MAX_RESULTS:
        raise ValueError(f'조인 결과 {total}행: 결과 칸 {JOIN_MAX_RESULTS}행을 넘습니다. 같은 key 행을 줄이세요')
    left = [(f'R{n}', row) for n, row in enumerate(req.left)]
    right = [(f'S{n}', row) for n, row in enumerate(req.right)]
    out = []
    name = '중첩 반복 조인' if req.algorithm == 'nested_loop' else '정렬-병합 조인'
    frames = [join_frame(left, right, out, f'{name}: R.key = S.key인 행 쌍 찾기')]
    (nested_loop if req.algorithm == 'nested_loop' else sort_merge)(left, right, out, frames)
    return build_animation('db_join', name, frames)


# --- B-트리 인덱스(db_btree): 검색·삽입(분할)·범위 검색 ---

# B-트리 키 범위: 노드 칸에 두 자리로 읽히는 0~99
BKey = Annotated[int, Field(ge=0, le=99)]


## @brief 키 하나를 찾거나 넣는 연산
class BTreeKeyOp(Strict):
    op: Literal['search', 'insert']
    key: BKey


## @brief low~high(양 끝 포함) 범위 검색 연산
class BTreeRangeOp(Strict):
    op: Literal['range']
    low: BKey
    high: BKey

    ## @brief 낮은 끝이 높은 끝보다 크면 거부
    @model_validator(mode='after')
    def validate_range(self):
        if self.low > self.high:
            raise ValueError(f'range: 낮은 끝 {self.low}이(가) 높은 끝 {self.high}보다 큽니다')
        return self


## @brief B-트리 장면. keys를 차례로 넣어 만든 트리(첫 화면)에서 연산 4개까지 실행
# - max_keys: 노드 하나에 담는 키 상한(2면 2-3 트리). 넘치면 가운데 키를 부모로 올려 분할
class BTreeRequest(Strict):
    kind: Literal['db_btree']
    max_keys: Literal[2, 3]
    keys: list[BKey] = Field(max_length=12)
    ops: list[Annotated[Union[BTreeKeyOp, BTreeRangeOp], Field(discriminator='op')]] = Field(min_length=1, max_length=4)

    ## @brief 초기 키 중복을 거부(B-트리 인덱스 키는 서로 다름)
    @model_validator(mode='after')
    def validate_keys(self):
        if len(set(self.keys)) != len(self.keys):
            raise ValueError('B-트리 초기 키가 중복되었습니다')
        return self


## @brief B-트리 노드(디스크 페이지 하나): 정렬된 키와 키 수 + 1개의 자식
@dataclass(eq=False)
class BNode:
    keys: list = field(default_factory=list)
    children: list = field(default_factory=list)


## @brief 트리 높이(루트에서 리프까지 노드 수)
def height(node: BNode) -> int:
    return 1 + (height(node.children[0]) if node.children else 0)


## @brief B-트리 화면: 깊이를 두 칸 간격(y=0,2,4,6)으로, 리프는 왼쪽부터 한 칸씩 띄우고 부모는 자식 묶음 가운데에 둠
# - 키 칸 id는 키 값(k25)이라 분할·승격으로 노드가 바뀌어도 같은 상자가 움직임
# - 자식 i는 키 i(마지막 자식은 마지막 키) 칸에서 화살표로 이음
# - result: 범위 검색으로 모은 키를 리프 줄 두 칸 아래에 정렬해 보여 줌(트리 바로 밑이라 빈 줄이 생기지 않음)
def btree_frame(root: BNode, caption, active=(), new=(), result=None) -> Frame:
    boxes, arrows, cursor = [], [], 0
    result_row = 2 * height(root) if root.keys else 0
    bottom = result_row - 2 if result is not None else GRID_HEIGHT - 1

    def place(node, depth):
        nonlocal cursor
        if node.children:
            spans = [place(child, depth + 1) for child in node.children]
            start, end = spans[0][0], spans[-1][1]
            x = start + (end - start + 1 - len(node.keys)) // 2
        else:
            start = x = cursor
            end = x + len(node.keys) - 1
            cursor += len(node.keys) + 1
        if x + len(node.keys) > GRID_WIDTH or 2 * depth > bottom:
            raise ValueError(f'B-트리가 {GRID_WIDTH}×{GRID_HEIGHT} 격자를 넘습니다. 키 수를 줄이세요')
        for n, key in enumerate(node.keys):
            tone = 'new' if key in new else 'active' if key in active else 'normal'
            boxes.append(Box(id=f'k{key}', label=str(key), x=x + n, y=2 * depth, shape='cell', tone=tone))
        for n, child in enumerate(node.children):
            arrows.append(Arrow(source=f'k{node.keys[min(n, len(node.keys) - 1)]}', target=f'k{child.keys[0]}'))
        return start, end

    if root.keys:
        place(root, 0)
    if result is not None and result_row > GRID_HEIGHT - 1:
        raise ValueError(f'B-트리가 {GRID_WIDTH}×{GRID_HEIGHT} 격자를 넘습니다. 키 수를 줄이세요')
    boxes += [Box(id=f'q{key}', label=str(key), x=n, y=result_row, shape='cell', tone='new')
              for n, key in enumerate(result or [])]
    return Frame(boxes=boxes, arrows=arrows, caption=caption)


## @brief 키를 리프에 넣고 넘친 노드를 위로 분할. emit(caption, active, new)로 단계마다 화면을 남김
# - 넘친 상태를 먼저 한 단계 보여 준 뒤 분할: 가운데 키가 부모로 올라가는 이유를 눈으로 따라가게 함
# - 루트가 넘치면 새 루트를 만들어 높이가 1 늘어남(모든 리프의 깊이가 함께 늘어 균형 유지)
def btree_insert(holder: dict, key: int, max_keys: int, emit):
    node, path = holder['root'], []
    while node.children:
        path.append(node)
        node = node.children[bisect_right(node.keys, key)]
    if key in node.keys or any(key in p.keys for p in path):
        raise ValueError(f'insert {key}: 이미 있는 키입니다')
    insort(node.keys, key)
    visited = [k for p in path for k in p.keys]
    over = f' → 키 {len(node.keys)}개로 넘침' if len(node.keys) > max_keys else ''
    emit(f'insert {key}: 리프 페이지에 정렬 위치로 넣음{over}', visited, [key])
    while len(node.keys) > max_keys:
        middle = len(node.keys) // 2
        up = node.keys[middle]
        right = BNode(node.keys[middle + 1:], node.children[middle + 1:])
        node.keys, node.children = node.keys[:middle], node.children[:middle + 1]
        if path:
            parent = path.pop()
            at = parent.children.index(node)
            parent.keys.insert(at, up)
            parent.children.insert(at + 1, right)
            over = ', 부모도 넘침' if len(parent.keys) > max_keys else ''
            emit(f'분할: 가운데 {up}{josa(up, "을", "를")} 부모로 올림{over}', [], [up])
            node = parent
        else:
            holder['root'] = BNode([up], [node, right])
            emit(f'루트 분할: {up}{josa(up, "이", "가")} 새 루트 → 높이 {height(holder["root"])}', [], [up])


## @brief 루트에서 리프 방향으로 페이지를 하나씩 읽으며 키 비교로 내려갈 자식을 고름
def btree_search(root: BNode, key: int, frames):
    node, pages = root, 0
    while True:
        pages += 1
        keys = ', '.join(map(str, node.keys))
        at = bisect_left(node.keys, key)
        if at < len(node.keys) and node.keys[at] == key:
            frames.append(btree_frame(root, f'페이지 {pages} [{keys}]: {key} 찾음 (페이지 {pages}개 읽음)',
                                      active=node.keys, new=[key]))
            return
        if not node.children:
            frames.append(btree_frame(root, f'페이지 {pages} 리프 [{keys}]에도 {key} 없음 → 실패 (페이지 {pages}개 읽음)',
                                      active=node.keys))
            return
        where = (f'{key} < {node.keys[0]}' if at == 0 else f'{key} > {node.keys[-1]}' if at == len(node.keys)
                 else f'{node.keys[at - 1]} < {key} < {node.keys[at]}')
        frames.append(btree_frame(root, f'페이지 {pages} [{keys}]: {where} → {at + 1}번째 자식', active=node.keys))
        node = node.children[at]


## @brief 범위와 겹칠 수 있는 자식만 방문(전위 순서)하며 범위 안 키를 모음
# - 자식 i는 키 i-1과 키 i 사이 값만 담으므로, 그 구간이 [low, high]와 겹칠 때만 읽음
def btree_range(root: BNode, low: int, high: int, frames):
    found, pages = [], 0

    def visit(node):
        nonlocal pages
        pages += 1
        hits = [k for k in node.keys if low <= k <= high]
        found.extend(hits)
        keys = ', '.join(map(str, node.keys))
        what = f'{", ".join(map(str, hits))} 수집' if hits else '범위 안 키 없음'
        frames.append(btree_frame(root, f'페이지 {pages} [{keys}]: {what}', active=node.keys, new=hits,
                                  result=sorted(found)))
        for n, child in enumerate(node.children):
            if (n == 0 or node.keys[n - 1] < high) and (n == len(node.keys) or node.keys[n] > low):
                visit(child)

    visit(root)
    frames.append(btree_frame(root, f'{low}~{high}: 키 {len(found)}개, 페이지 {pages}개 읽음', result=sorted(found)))


## @brief B-트리 장면 실행. 초기 키는 그림 없이 넣고, 연산마다 페이지 단위 단계를 그림
def simulate_btree(req: BTreeRequest) -> Animation:
    holder = {'root': BNode()}
    for key in req.keys:
        btree_insert(holder, key, req.max_keys, lambda *_: None)
    frames = [btree_frame(holder['root'], f'B-트리(노드당 키 최대 {req.max_keys}개): 키 {len(req.keys)}개, '
                                          f'높이 {height(holder["root"]) if req.keys else 0}')]
    for op in req.ops:
        if op.op == 'insert':
            btree_insert(holder, op.key, req.max_keys,
                         lambda caption, active, new: frames.append(btree_frame(holder['root'], caption, active, new)))
        elif op.op == 'search':
            btree_search(holder['root'], op.key, frames)
        else:
            btree_range(holder['root'], op.low, op.high, frames)
    return build_animation('db_btree', 'B-트리 인덱스', frames)


# --- 해시 인덱스(db_hash_index): 이미 만든 인덱스에서 동등 검색과 범위 검색 비교 ---

## @brief 키 하나를 같다(=) 조건으로 찾는 연산
class HashLookupOp(Strict):
    op: Literal['lookup']
    key: Value


## @brief low~high 범위 검색. 해시는 키 순서를 보존하지 않아 모든 버킷을 읽어야 함
class HashRangeOp(Strict):
    op: Literal['range']
    low: Value
    high: Value

    ## @brief 낮은 끝이 높은 끝보다 크면 거부
    @model_validator(mode='after')
    def validate_range(self):
        if self.low > self.high:
            raise ValueError(f'range: 낮은 끝 {self.low}이(가) 높은 끝 {self.high}보다 큽니다')
        return self


## @brief 해시 인덱스 장면. keys로 만든 체이닝 인덱스(h(k) = k mod buckets)에서 연산 3개까지 실행
# - 버킷 7개는 범위 검색이 버킷마다 한 단계를 써도 MAX_FRAMES 안에 드는 수
class HashIndexRequest(Strict):
    kind: Literal['db_hash_index']
    buckets: int = Field(ge=3, le=7)
    keys: list[Value] = Field(min_length=1, max_length=10)
    ops: list[Annotated[Union[HashLookupOp, HashRangeOp], Field(discriminator='op')]] = Field(min_length=1, max_length=3)

    ## @brief 인덱스 키 중복을 거부
    @model_validator(mode='after')
    def validate_keys(self):
        if len(set(self.keys)) != len(self.keys):
            raise ValueError('해시 인덱스 키가 중복되었습니다')
        return self


## @brief 해시 인덱스 장면 실행. 동등 검색은 버킷 하나만, 범위 검색은 버킷마다 한 단계씩 모두 읽음
# - 체인 그림은 기존 해시 테이블 장면(hash_frame)과 같게 그려 해시 테이블 개념과 바로 이어지게 함
def simulate_hash_index(req: HashIndexRequest) -> Animation:
    m = req.buckets
    chains = [[] for _ in range(m)]
    for n, key in enumerate(req.keys):
        if len(chains[key % m]) == CHAIN_MAX:
            raise ValueError(f'버킷 {key % m}: 체인은 {CHAIN_MAX}개까지만 그립니다')
        chains[key % m].append((f'k{n}', key))
    frames = [hash_frame(chains, f'해시 인덱스: h(k) = k mod {m}, 키 {len(req.keys)}개')]
    for op in req.ops:
        if op.op == 'lookup':
            bucket = op.key % m
            chain = chains[bucket]
            at = next((n for n, (_, key) in enumerate(chain) if key == op.key), None)
            compared = chain[:len(chain) if at is None else at + 1]
            result = '없음' if at is None else '찾음'
            frames.append(hash_frame(chains, f'= {op.key}: h({op.key}) = {bucket}, 버킷 {bucket}만 읽고 키 {len(compared)}개 '
                                     f'비교 → {result}', active=[f'b{bucket}', *(i for i, _ in compared)],
                                     new=[chain[at][0]] if at is not None else []))
            continue
        found = []
        for bucket, chain in enumerate(chains):
            hits = [i for i, key in chain if op.low <= key <= op.high]
            found += hits
            what = ', '.join(str(key) for i, key in chain if i in hits) or '범위 안 키 없음'
            frames.append(hash_frame(chains, f'{op.low}~{op.high} 범위: 버킷 {bucket} 읽음 → {what}',
                                     active=[f'b{bucket}', *(i for i, _ in chain)], new=found))
        keys = sorted(key for chain in chains for i, key in chain if i in found)
        frames.append(hash_frame(chains, f'{op.low}~{op.high}: 버킷 {m}개를 모두 읽어 {len(keys)}개 찾음 (키 순서가 흩어짐)',
                                 new=found))
    return build_animation('db_hash_index', '해시 인덱스 검색', frames)


# --- 트랜잭션(db_transaction): COMMIT·ROLLBACK·장애와 ACID ---

Item = Literal['A', 'B', 'C']
Tx = Literal['T1', 'T2']


## @brief 트랜잭션 시작·확정·취소
class TxOp(Strict):
    op: Literal['begin', 'commit', 'rollback']
    tx: Tx


## @brief 트랜잭션이 항목에 값을 씀(커밋 전에는 그 트랜잭션만 봄)
class TxWriteOp(Strict):
    op: Literal['write']
    tx: Tx
    item: Item
    value: Value


## @brief 트랜잭션이 항목을 읽음(자기가 쓴 값, 아니면 커밋된 값)
class TxReadOp(Strict):
    op: Literal['read']
    tx: Tx
    item: Item


## @brief 시스템 장애 후 재시작: 진행 중인 트랜잭션은 모두 취소되고 커밋된 값만 남음
class CrashOp(Strict):
    op: Literal['crash']


## @brief 트랜잭션 장면. 항목 3개·트랜잭션 2개·연산 10개는 한 화면에서 값 흐름을 따라갈 수 있는 분량
# - 같은 항목을 두 트랜잭션이 동시에 쓰는 경우(잠금 대기)는 그리지 않고 거부함
class TransactionRequest(Strict):
    kind: Literal['db_transaction']
    items: dict[Item, Value] = Field(min_length=1, max_length=3)
    ops: list[Annotated[Union[TxOp, TxWriteOp, TxReadOp, CrashOp], Field(discriminator='op')]] = Field(min_length=1,
                                                                                                       max_length=10)


# 트랜잭션 열 위치: DB(커밋된 값) x=1, T1 x=3, T2 x=5
TX_COLUMN = {'T1': 3, 'T2': 5}


## @brief 트랜잭션 화면: 행마다 항목 하나, DB 열은 커밋된 값, T 열은 그 트랜잭션이 읽거나 쓴 값, 맨 아래는 상태
# - tones: 상자 id → tone. views에 없는 상자도 removed로 남길 수 있게 dropped(트랜잭션 → 보기)를 따로 받음
def tx_frame(committed, views, status, caption, tones=None, dropped=None, arrows=()) -> Frame:
    tones = tones or {}
    names = sorted(committed)
    boxes = [Box(id='hDB', label='DB', x=1, y=0, shape='label')]
    boxes += [Box(id=f'h{tx}', label=tx, x=x, y=0, shape='label') for tx, x in TX_COLUMN.items() if tx in status]
    for y, item in enumerate(names, start=1):
        boxes.append(Box(id=f'i{item}', label=item, x=0, y=y, shape='label'))
        boxes.append(Box(id=f'db{item}', label=str(committed[item]), x=1, y=y, shape='cell', tone=tones.get(f'db{item}', 'normal')))
    for tx, view in list(views.items()) + list((dropped or {}).items()):
        for item, value in view.items():
            box_id = f'{tx}{item}'
            tone = 'removed' if dropped and tx in dropped else tones.get(box_id, 'normal')
            boxes.append(Box(id=box_id, label=str(value), x=TX_COLUMN[tx], y=1 + names.index(item), shape='cell', tone=tone))
    boxes += [Box(id=f's{tx}', label=state, x=TX_COLUMN[tx], y=len(names) + 2, shape='label') for tx, state in status.items()]
    return Frame(boxes=boxes, arrows=list(arrows), caption=caption)


## @brief 값 목록을 'A=70, B=80'처럼 표기
def assigns(values: dict) -> str:
    return ', '.join(f'{item}={value}' for item, value in sorted(values.items()))


## @brief 트랜잭션 장면 실행. 쓰기는 트랜잭션 열에만 남기고 commit 때 DB 열로 옮김(커밋 전 값은 다른 트랜잭션에 안 보임)
# - rollback·장애는 트랜잭션 열을 removed로 지우고 DB 열은 그대로 둠: 원자성(전부 아니면 전무)·지속성(커밋은 남음)
def simulate_transaction(req: TransactionRequest) -> Animation:
    committed = dict(req.items)
    views, writes, status = {}, {}, {}
    frames = [tx_frame(committed, views, status, f'DB 초기 값 {assigns(committed)}')]

    def frame(caption, **kwargs):
        frames.append(tx_frame(committed, views, status, caption, **kwargs))

    def require(tx, what):
        if tx not in views:
            raise ValueError(f'{what}: {tx}이(가) 시작(begin)되지 않았습니다')

    def check_item(item, what):
        if item not in committed:
            raise ValueError(f'{what}: 없는 항목입니다: {item}')

    for op in req.ops:
        if op.op == 'begin':
            if op.tx in views:
                raise ValueError(f'begin {op.tx}: 이미 진행 중입니다')
            views[op.tx], writes[op.tx], status[op.tx] = {}, {}, '진행'
            frame(f'{op.tx} 시작: 여러 연산을 한 작업 단위로 묶음')
        elif op.op == 'write':
            require(op.tx, 'write')
            check_item(op.item, 'write')
            other = next((tx for tx in writes if tx != op.tx and op.item in writes[tx]), None)
            if other:
                raise ValueError(f'write {op.item}: {other}이(가) 커밋하지 않은 항목이라 쓸 수 없습니다(잠금 대기는 그리지 않음)')
            views[op.tx][op.item] = writes[op.tx][op.item] = op.value
            frame(f'{op.tx} write {op.item}={op.value}: 커밋 전이라 DB 값은 그대로', tones={f'{op.tx}{op.item}': 'new'})
        elif op.op == 'read':
            require(op.tx, 'read')
            check_item(op.item, 'read')
            if op.item in writes[op.tx]:
                frame(f'{op.tx} read {op.item} = {views[op.tx][op.item]}: 자기가 쓴 값', tones={f'{op.tx}{op.item}': 'active'})
                continue
            views[op.tx][op.item] = committed[op.item]
            other = next((tx for tx in writes if tx != op.tx and op.item in writes[tx]), None)
            why = f'{other}의 커밋 전 값은 안 보임(격리성)' if other else '커밋된 값'
            frame(f'{op.tx} read {op.item} = {committed[op.item]}: {why}', tones={f'{op.tx}{op.item}': 'active'},
                  arrows=[Arrow(source=f'db{op.item}', target=f'{op.tx}{op.item}', label='read')])
        elif op.op in ('commit', 'rollback'):
            require(op.tx, op.op)
            view, changed = views.pop(op.tx), writes.pop(op.tx)
            if op.op == 'commit':
                committed.update(changed)
                status[op.tx] = '커밋'
                caption = (f'{op.tx} commit: {assigns(changed)} DB에 확정' if changed else f'{op.tx} commit: 바꾼 값 없음')
                frame(caption, tones={f'db{item}': 'new' for item in changed}, dropped={op.tx: view})
            else:
                status[op.tx] = '취소'
                caption = (f'{op.tx} rollback: {assigns(changed)} 모두 취소 → DB 그대로(원자성)' if changed
                           else f'{op.tx} rollback: 취소할 변경 없음')
                frame(caption, tones={f'db{item}': 'active' for item in changed}, dropped={op.tx: view})
        else:
            lost = sorted(views)
            dropped = {tx: views.pop(tx) for tx in lost}
            writes.clear()
            status.update({tx: '취소' for tx in lost})
            caption = '장애 → 재시작: 커밋된 값 유지(지속성)' + (f', {", ".join(lost)} 변경 취소(원자성)' if lost else '')
            frame(caption, tones={f'db{item}': 'active' for item in committed}, dropped=dropped)
    return build_animation('db_transaction', '트랜잭션과 ACID', frames)


# model.py의 요청 합집합에 더하는 데이터베이스 장면 요청(kind로 구분)
DB_REQUESTS = (TableRequest, ClosureRequest, JoinRequest, BTreeRequest, HashIndexRequest, TransactionRequest)

# 장면 종류(kind) → 시뮬레이터. __init__.py의 SIMULATORS에 그대로 펼쳐 넣음
DB_SIMULATORS = {
    'db_table': simulate_table,
    'db_closure': simulate_closure,
    'db_join': simulate_join,
    'db_btree': simulate_btree,
    'db_hash_index': simulate_hash_index,
    'db_transaction': simulate_transaction,
}

# 장면 종류별 입력 형식 예시(막힘 보충 장면 선택용). 짧고 평범한 값으로 둠
DB_SCENE_FORMATS = {
    'db_table': {'kind': 'db_table', 'tables': [{'name': 'T', 'columns': ['id', 'x'], 'key': ['id'], 'rows': [[1, 5], [2, 7]]}],
                 'ops': [{'op': 'where', 'table': 'T', 'column': 'x', 'cmp': '>', 'value': 6}]},
    'db_closure': {'kind': 'db_closure', 'attributes': ['A', 'B', 'C'], 'fds': [{'lhs': ['A'], 'rhs': ['B']}],
                   'starts': [['A']]},
    'db_join': {'kind': 'db_join', 'algorithm': 'nested_loop', 'left': [{'key': 1, 'val': 'a'}],
                'right': [{'key': 1, 'val': 'x'}]},
    'db_btree': {'kind': 'db_btree', 'max_keys': 2, 'keys': [10, 20, 30], 'ops': [{'op': 'search', 'key': 20}]},
    'db_hash_index': {'kind': 'db_hash_index', 'buckets': 3, 'keys': [1, 4], 'ops': [{'op': 'lookup', 'key': 4}]},
    'db_transaction': {'kind': 'db_transaction', 'items': {'A': 10},
                       'ops': [{'op': 'begin', 'tx': 'T1'}, {'op': 'write', 'tx': 'T1', 'item': 'A', 'value': 5},
                               {'op': 'commit', 'tx': 'T1'}]},
}
