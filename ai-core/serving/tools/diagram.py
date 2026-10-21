# 파일 역할: 모델이 제안한 그래프 JSON을 검증하고 서버가 직접 안전한 SVG를 작성하는 도구
"""Validated graph data -> SVG; never execute model-supplied code."""
from html import escape
import base64
from io import BytesIO
from functools import lru_cache
from fontTools import subset
from fontTools.ttLib import TTFont
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


# 역할: 격자 위치와 짧은 라벨을 가진 노드
class Node(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,31}$')
    label: str = Field(min_length=1, max_length=32)
    column: int = Field(ge=0, le=3)
    row: int = Field(ge=0, le=7)


# 역할: 기존 노드 두 개를 잇는 방향 간선
class Edge(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    source: str
    target: str


# 역할: 제목·노드·간선과 전체 그래프 무결성 검증
class Diagram(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    title: str = Field(min_length=1, max_length=60)
    nodes: list[Node] = Field(min_length=1, max_length=16)
    edges: list[Edge] = Field(max_length=24)

    # 역할: 노드 ID·배치 중복과 존재하지 않는 연결을 거부해 렌더링 가능한 그래프 보장
    @model_validator(mode='after')
    def validate_graph(self):
        ids = [node.id for node in self.nodes]
        if len(set(ids)) != len(ids):
            raise ValueError('Node IDs must be unique')
        cells = [(node.column, node.row) for node in self.nodes]
        if len(set(cells)) != len(cells):
            raise ValueError('Nodes must occupy distinct grid cells')
        for edge in self.edges:
            if edge.source not in ids or edge.target not in ids:
                raise ValueError('Edges must reference existing node IDs')
            if edge.source == edge.target:
                raise ValueError('Self-loops are not supported')
        return self


TOOL = {'type': 'function', 'function': {
    'name': 'create_diagram',
    'description': ('Create and save a CS concept diagram as SVG. Use when the user '
                    'requests a diagram. Place nodes in columns left to right and rows '
                    'top to bottom. Keep labels short. Edges are directed arrows.'),
    'parameters': Diagram.model_json_schema(),
}}


# 역할: 도식에 필요한 글자만 폰트에 포함해 한글 표시를 보장하고 SVG 크기 절감; 같은 문자열은 캐시 재사용
@lru_cache(maxsize=32)
def embedded_font(text: str) -> str:
    font = TTFont(Path(__file__).resolve().parents[1] / 'assets' / 'NotoSansKR.woff2')
    options = subset.Options()
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(text=text)
    subsetter.subset(font)
    font.flavor = 'woff2'
    output = BytesIO()
    font.save(output)
    font.close()
    data = base64.b64encode(output.getvalue()).decode('ascii')
    return f"<style>@font-face{{font-family:DiagramKR;src:url(data:font/woff2;base64,{data}) format('woff2')}}text{{font-family:DiagramKR,sans-serif}}</style>"


# 역할: 검증된 노드·간선을 격자 좌표로 렌더링하고 SVG 경로와 재참조용 구조 데이터 반환
def create_diagram(arguments: str, output_dir: Path) -> dict:
    diagram = Diagram.model_validate_json(arguments)
    width = (max(n.column for n in diagram.nodes) + 1) * 300 + 40
    height = (max(n.row for n in diagram.nodes) + 1) * 110 + 100
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
             f'<title>{escape(diagram.title)}</title>',
             embedded_font(diagram.title + ''.join(n.label for n in diagram.nodes)),
             '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#64748b"/></marker></defs>',
             '<rect width="100%" height="100%" fill="#fafbfc"/>',
             f'<text x="30" y="40" font-family="sans-serif" font-size="22">{escape(diagram.title)}</text>']
    positions = {n.id: (30 + n.column * 300, 80 + n.row * 110) for n in diagram.nodes}
    # 간선을 노드보다 먼저 그려 선이 상자·텍스트 위를 덮지 않도록 배치
    for edge in diagram.edges:
        x1, y1 = positions[edge.source]
        x2, y2 = positions[edge.target]
        if x1 != x2:
            start = (x1 + (240 if x2 > x1 else 0), y1 + 30)
            end = (x2 + (0 if x2 > x1 else 240), y2 + 30)
        else:
            start = (x1 + 120, y1 + (60 if y2 > y1 else 0))
            end = (x2 + 120, y2 + (0 if y2 > y1 else 60))
        parts.append(f'<path d="M{start[0]},{start[1]} L{end[0]},{end[1]}" stroke="#64748b" stroke-width="2" fill="none" marker-end="url(#arrow)"/>')
    for node in diagram.nodes:
        x, y = positions[node.id]
        parts.append(f'<rect x="{x}" y="{y}" width="240" height="60" rx="10" fill="#eef2ff" stroke="#a5b4fc"/>')
        parts.append(f'<text x="{x+120}" y="{y+36}" text-anchor="middle" font-family="sans-serif" font-size="14">{escape(node.label)}</text>')
    parts.append('</svg>')
    # UUID 파일명으로 결과 덮어쓰기를 피하고 구조 데이터도 반환해 후속 수정 요청에 활용
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_id = uuid4().hex
    path = (output_dir / f'{artifact_id}.svg').resolve()
    path.write_text('\n'.join(parts), encoding='utf-8')
    return {'ok': True, 'artifact_id': artifact_id, 'path': str(path),
            'mime_type': 'image/svg+xml', 'diagram': diagram.model_dump()}
