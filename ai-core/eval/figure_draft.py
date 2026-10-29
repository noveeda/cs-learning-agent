## @file
# @brief 진단 문항 지문 도식 초안 생성기: 운영 26B(8000번)에 지문만 보내 figure 초안을 받고, 검사기 통과분만 문항 은행에 넣음
# - 정답 키·해설·선택지는 모델에 넘기지 않음: 모델이 답을 알면 결과 상태나 정답 쪽을 그릴 수 있기 때문
# - 초안은 Git 밖 ai-core/outputs/figures/drafts.json에 모아 두고(--draft), 검수 뒤 --apply로 items-*.jsonl에 씀
#   검사는 통과했지만 사람 검수에서 뺀 문항(정답 암시·정보 부족 등)은 --review 파일({ID: 이유})로 넘겨 dropped로 둠
# - 검사(serving.figure.check_figure)를 통과하지 못하면 오류 문장을 붙여 다시 요청하고, 끝내 실패하면 도식 없음으로 둠
#
# 실행 예(ai-core 폴더)
#   python -m eval.figure_draft --draft --out outputs/figures/drafts.json [--ids stack-d1,queue-d2]
#   python -m eval.figure_draft --apply --out outputs/figures/drafts.json --review outputs/figures/review.json
import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from itertools import permutations
from pathlib import Path

AI_CORE = Path(__file__).resolve().parents[1]
if str(AI_CORE) not in sys.path:
    sys.path.insert(0, str(AI_CORE))

from pydantic import ValidationError  # noqa: E402

from serving.figure import Figure, check_figure  # noqa: E402
from serving.item_bank import load_item_bank  # noqa: E402

# 검사 실패 뒤 오류를 알려 주고 다시 받는 횟수; 같은 실수를 반복하는 문항에 운영 서버 시간을 더 쓰지 않게 제한
RETRIES = 3
# 배치 보정에서 자리를 바꿔 볼 정점 수 상한: 7개면 경우의 수 5040개로 즉시 끝남, 더 크면 모델 재요청에 맡김
MAX_REPAIR_NODES = 7
# 자리 바꾸기로 고칠 수 있는 오류(배치 문제)를 가리키는 검사기 문구; 누설·라벨 폭 오류는 자리로 못 고침
LAYOUT_ERRORS = ('겹칩니다', '지나갑니다', '교차합니다')
# 운영 26B에 겹쳐 보내는 요청 수 상한: 학습자 요청과 다른 측정이 같은 서버를 쓰므로 2개 이하로 둠
PROD_SLOTS = 2

PROMPT = '''너는 컴퓨터과학 진단 문항의 지문을 보고, 지문 옆에 놓을 정지 도식(JSON)을 설계한다.
목적: 학습자가 지문에 적힌 구조(그래프의 간선, 표의 행, 큐·스택·배열의 칸, 메모리 칸, 계층 등)를 글 대신 도형으로 한눈에 보게 한다.

규칙
1. 지문에 적힌 사실만 그린다. 지문을 읽고 계산하거나 추론해야 나오는 것(결과 상태, 최단 경로, 정렬 결과, 최종 큐, 정답)은 그리지 않는다.
2. 라벨은 지문에 있는 글자를 그대로 복사한 짧은 말이나 숫자만 쓴다. 지문에 없는 말(예: "결과", "정답", "거리 5")은 쓰지 않는다.
3. 도형을 우선하고 글은 최소로 한다. 문장을 라벨로 쓰지 않는다.
4. 그릴 구조가 없는 개념 문항(정의·이유를 묻는 문항, 나열된 항목의 순서를 묻는 문항 등)은 has_figure를 false로 하고 나머지는 빈 배열로 둔다. 억지로 만들지 않는다.
5. 배치가 답을 암시하면 안 된다. 순서·크기를 묻는 문항이면 지문에 적힌 순서대로만 두거나 도식을 만들지 않는다.
6. 관계는 선으로 그린다(간선, 포인터 next, 함수 종속 A→B, 부모-자식, 링크). 선도 없고 칸 배열도 아닌 상자만 늘어놓은 도식은 글과 다를 바 없으므로 만들지 않는다.
7. 지문의 값을 빠뜨리지 않는다. 주소와 값처럼 한 대상에 정보가 둘이면 둘 다 그린다.

좌표와 크기
- 격자는 가로 0~6, 세로 0~7칸이고 좌표는 0.5 단위다. 도형 중심은 칸 가운데에 온다.
- shape와 크기(폭×높이 px, 한 칸은 100×72px): circle 44×44(정점, 라벨 영숫자 3자·한글 2자 이하), box 88×40(라벨 영숫자 8자·한글 5자 이하),
  cylinder 72×48(DB·디스크), cell 50×36(배열·큐·스택 칸, x를 0.5씩 늘리면 옆 칸과 맞닿음, 라벨 영숫자 4자·한글 2자 이하), text 96×28(테두리 없는 이름표, 한글 6자 이하).
- 도형끼리 겹치지 않게, 선이 다른 도형 위를 지나가거나 다른 선과 엇갈리지 않게 배치한다. 그림은 왼쪽 위(0,0)부터 채워 빈 공간을 줄인다.
- 이름표(text)는 설명하는 도형 바로 왼쪽 칸이나 바로 위 칸에 붙여 둔다(예: "준비 큐" 이름표 오른쪽에 큐 칸들을 맞닿게 이어 둠).
- 한 대상에 값이 둘 이상이면(예: 메모리 칸의 주소와 값, 프로세스와 상태) 주소·이름은 text 이름표로 위에 두고 값은 그 아래 도형에 넣거나, 표로 그린다.
- edges: from·to는 노드나 표의 id, label은 지문의 가중치·비용 같은 짧은 값(없으면 ""), 방향이 없으면 directed false.
- tables: 관계·행 목록. x·y는 표 왼쪽 위 칸. 열은 5개, 행은 12개 이하(지문의 목록은 빠짐없이 옮긴다). 칸 값은 영숫자 9자·한글 5자 이하. keys는 지문이 키라고 밝힌 열만.

예시 1 지문: "간선이 A→B:4, A→C:1, C→B:2인 방향 그래프에서 A부터 B까지의 최단 거리는?"
{"facts":["A→B:4","A→C:1","C→B:2"],"has_figure":true,"nodes":[{"id":"a","label":"A","shape":"circle","x":0,"y":1},{"id":"b","label":"B","shape":"circle","x":2,"y":0},{"id":"c","label":"C","shape":"circle","x":2,"y":2}],"edges":[{"from":"a","to":"b","label":"4","directed":true},{"from":"a","to":"c","label":"1","directed":true},{"from":"c","to":"b","label":"2","directed":true}],"tables":[]}
예시 2 지문: "빈 큐에 10, 20을 차례로 넣은 뒤 30을 넣었다. 맨 앞 원소는?"
{"facts":["넣은 순서 10, 20, 30"],"has_figure":false,"nodes":[],"edges":[],"tables":[]}
(예시 2는 처음 큐가 비어 있고 칸에 원소를 채우면 결과 상태가 되므로 그리지 않는다.)
반대로 지문에 처음 입력이 적혀 있으면 그 입력만 그린다: 배열·목록 [..]은 맞닿은 cell로, 프로세스 목록·라우팅 표·행 목록·키 목록은 표로, 계층·트리 구조는 상자와 선으로.
예시 2-1 지문: "배열 [8,3,5,1]을 삽입 정렬로 두 단계 진행한 뒤의 배열은?"
{"facts":["배열 [8,3,5,1]"],"has_figure":true,"nodes":[{"id":"c0","label":"8","shape":"cell","x":0,"y":0},{"id":"c1","label":"3","shape":"cell","x":0.5,"y":0},{"id":"c2","label":"5","shape":"cell","x":1,"y":0},{"id":"c3","label":"1","shape":"cell","x":1.5,"y":0}],"edges":[],"tables":[]}
예시 2-2 지문: "FCFS. 프로세스(도착 시각, 실행 시간)가 P1(0, 5), P2(2, 3)이다. P2의 대기 시간은?"
{"facts":["P1(0, 5)","P2(2, 3)"],"has_figure":true,"nodes":[],"edges":[],"tables":[{"id":"p","name":"프로세스","columns":["프로세스","도착 시각","실행 시간"],"rows":[["P1","0","5"],["P2","2","3"]],"keys":[],"x":0,"y":0}]}
예시 3 지문: "Emp(id,dept)의 행은 (1,A),(2,B)이고 id가 기본키다. dept별 행 수는?"
{"facts":["Emp(id,dept)","(1,A),(2,B)","id가 기본키"],"has_figure":true,"nodes":[],"edges":[],"tables":[{"id":"emp","name":"Emp","columns":["id","dept"],"rows":[["1","A"],["2","B"]],"keys":["id"],"x":0,"y":0}]}
예시 4 지문: "메모리 칸 10에는 값 7과 next=30, 칸 30에는 값 4와 next=없음이 있다. head=10이다."
{"facts":["칸 10: 값 7, next=30","칸 30: 값 4, next=없음","head=10"],"has_figure":true,"nodes":[{"id":"h","label":"head","shape":"text","x":0,"y":1},{"id":"a10","label":"10","shape":"text","x":1,"y":0},{"id":"v10","label":"7","shape":"cell","x":1,"y":1},{"id":"a30","label":"30","shape":"text","x":3,"y":0},{"id":"v30","label":"4","shape":"cell","x":3,"y":1},{"id":"n","label":"없음","shape":"text","x":5,"y":1}],"edges":[{"from":"h","to":"v10","label":"","directed":true},{"from":"v10","to":"v30","label":"next","directed":true},{"from":"v30","to":"n","label":"next","directed":true}],"tables":[]}
예시 5 지문: "P1이 실행 중이고 준비 큐에는 P2, P3가 이 순서로 있다. P1이 입출력을 요청하면 준비 큐는?"
{"facts":["P1 실행 중","준비 큐: P2, P3"],"has_figure":true,"nodes":[{"id":"run","label":"실행","shape":"text","x":0,"y":0},{"id":"p1","label":"P1","shape":"box","x":1,"y":0},{"id":"rq","label":"준비 큐","shape":"text","x":0,"y":1},{"id":"p2","label":"P2","shape":"cell","x":1,"y":1},{"id":"p3","label":"P3","shape":"cell","x":1.5,"y":1}],"edges":[],"tables":[]}
예시 6 지문: "관계 R(A,B,C,D)의 함수 종속은 A→B, C→D이다. 후보 키는?"
{"facts":["R(A,B,C,D)","A→B","C→D"],"has_figure":true,"nodes":[{"id":"a","label":"A","shape":"circle","x":0,"y":0},{"id":"b","label":"B","shape":"circle","x":1,"y":0},{"id":"c","label":"C","shape":"circle","x":2,"y":0},{"id":"d","label":"D","shape":"circle","x":3,"y":0}],"edges":[{"from":"a","to":"b","label":"","directed":true},{"from":"c","to":"d","label":"","directed":true}],"tables":[]}

facts에는 그릴 사실을 지문에서 그대로 옮겨 적고, 그 사실만 도형으로 옮긴다. JSON 하나만 답한다.'''

_STRINGS = {'type': 'array', 'items': {'type': 'string'}}
RESPONSE_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['facts', 'has_figure', 'nodes', 'edges', 'tables'],
    'properties': {
        'facts': _STRINGS,
        'has_figure': {'type': 'boolean'},
        'nodes': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['id', 'label', 'shape', 'x', 'y'],
            'properties': {'id': {'type': 'string'}, 'label': {'type': 'string'},
                           'shape': {'enum': ['box', 'circle', 'cylinder', 'cell', 'text']},
                           'x': {'type': 'number'}, 'y': {'type': 'number'}}}},
        'edges': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['from', 'to', 'label', 'directed'],
            'properties': {'from': {'type': 'string'}, 'to': {'type': 'string'}, 'label': {'type': 'string'},
                           'directed': {'type': 'boolean'}}}},
        'tables': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['id', 'name', 'columns', 'rows', 'keys', 'x', 'y'],
            'properties': {'id': {'type': 'string'}, 'name': {'type': 'string'}, 'columns': _STRINGS,
                           'rows': {'type': 'array', 'items': _STRINGS}, 'keys': _STRINGS,
                           'x': {'type': 'number'}, 'y': {'type': 'number'}}}},
    },
}


## @brief 모델에 보낼 메시지; 문항에서 지문(question)만 넣고 정답·해설·선택지는 넣지 않음
def build_messages(item, feedback=None):
    messages = [{'role': 'system', 'content': PROMPT}, {'role': 'user', 'content': f'지문: "{item.question}"'}]
    if feedback:
        messages += [{'role': 'assistant', 'content': feedback['raw']},
                     {'role': 'user', 'content': '검사기가 다음 문제를 찾았다. 고쳐서 JSON 전체를 다시 답하라. '
                      '고칠 수 없으면 has_figure를 false로 하라.\n- ' + '\n- '.join(feedback['errors'])}]
    return messages


## @brief 모델 응답 JSON을 figure로 바꾸고 지문 글(stem: 질문 + 보기)과 대조해 검사; (figure 또는 None, 오류 목록) 반환
# - has_figure가 거짓이면 도식 없음(None, 오류 없음)
# - 빈 선 라벨("")은 라벨 없음으로 바꿔 검사기가 빈 글을 지문 대조하지 않게 함
def parse_response(raw: str, stem: str):
    data = json.loads(raw)
    if not data.get('has_figure'):
        return None, []
    figure = {
        'nodes': data['nodes'],
        'edges': [{**e, 'label': e['label'] or None} for e in data['edges']],
        'tables': data['tables'],
    }
    try:
        parsed = Figure.model_validate(figure)
    except ValidationError as exc:
        return figure, [_short(exc)]
    return parsed.model_dump(by_alias=True, exclude_defaults=True), check_figure(parsed, stem)


## @brief pydantic 검증 오류를 모델에 다시 알려 줄 짧은 문장들로 줄임
def _short(exc) -> str:
    return '; '.join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:6])


# 이름표(text)를 옮겨 볼 방향(격자 칸 단위): 위·왼쪽을 먼저 봄(이름표는 설명하는 도형의 위나 왼쪽에 두는 것이 원칙)
NUDGES = ((0, -0.5), (-1, 0), (0, -1), (-0.5, 0), (0, 0.5), (1, 0))


## @brief figure의 nodes만 바꿔 검사한 오류 목록; 격자 밖 등 구조 오류면 None
def _errors_with(figure, nodes, question):
    try:
        return check_figure(Figure.model_validate({**figure, 'nodes': nodes}), question)
    except ValidationError:
        return None


## @brief 배치 오류만 남은 초안을 자동으로 고침; 고친 figure 또는 None 반환
# - 1단계: 다른 도형과 겹친 이름표(text)만 위·왼쪽 등으로 옮겨 봄(26B가 이름표를 값 칸과 같은 자리에 두는 일이 잦음)
# - 2단계: 선에 이어진 정점끼리 모델이 고른 격자 자리만 바꿈; 통과하는 배치 중 옮긴 정점이 가장 적은 것을 고름
#   26B가 정점 5~7개 그래프에서 선 교차 없는 배치를 자주 못 찾아, 재요청보다 빠르고 매번 같은 결과를 냄
# - 라벨·선·표(지문의 사실)는 바꾸지 않고 좌표만 바꿈
def repair_layout(figure, question):
    nodes = [dict(node) for node in figure.get('nodes', [])]
    errors = _errors_with(figure, nodes, question)
    if not errors or not all(any(k in e for k in LAYOUT_ERRORS) for e in errors):
        return None  # 통과했거나, 구조·누설·라벨 폭 오류는 자리로 못 고치므로 모델 재요청에 맡김
    for node in (n for n in nodes if n['shape'] == 'text' and any(n['id'] in e and '겹칩니다' in e for e in errors)):
        origin = (node['x'], node['y'])
        best = (len(errors), origin)
        for dx, dy in NUDGES:
            node['x'], node['y'] = origin[0] + dx, origin[1] + dy
            found = _errors_with(figure, nodes, question)
            if found is not None and len(found) < best[0]:
                best = (len(found), (node['x'], node['y']))
        node['x'], node['y'] = best[1]
        errors = _errors_with(figure, nodes, question)
    if not errors:
        return {**figure, 'nodes': nodes}

    edges = figure.get('edges', [])  # 저장 형식은 기본값(빈 목록)을 빼므로 없을 수 있음
    linked = {e['from'] for e in edges} | {e['to'] for e in edges}
    movable = [i for i, node in enumerate(nodes) if node['id'] in linked]
    if not movable or len(movable) > MAX_REPAIR_NODES:
        return None
    slots = [(nodes[i]['x'], nodes[i]['y']) for i in movable]
    best = None
    for order in permutations(range(len(movable))):
        moved = sum(k != slot for k, slot in enumerate(order))
        if best and moved >= best[0]:
            continue
        candidate = [dict(node) for node in nodes]
        for k, slot in zip(movable, order):
            candidate[k]['x'], candidate[k]['y'] = slots[slot]
        if _errors_with(figure, candidate, question) == []:
            best = (moved, candidate)
    return {**figure, 'nodes': best[1]} if best else None


## @brief 문항 하나의 초안 생성: 요청 → 검사 → 실패 시 오류를 알려 재요청, 끝까지 실패하면 rejected
# - complete(messages)는 모델 응답 글을 돌려주는 함수; 테스트에서는 가짜 응답으로 바꿈
def draft_item(item, complete):
    feedback, attempts = None, []
    for _ in range(1 + RETRIES):
        raw = complete(build_messages(item, feedback))
        try:
            figure, errors = parse_response(raw, item.stem)
        except (ValueError, KeyError) as exc:
            figure, errors = None, [f'JSON 형식 오류: {exc}']
        if errors and figure and (repaired := repair_layout(figure, item.stem)):
            attempts.append({'figure': figure, 'errors': errors})
            figure, errors = repaired, []
        attempts.append({'figure': figure, 'errors': errors})
        if not errors:
            return {'id': item.id, 'status': 'ok' if figure else 'none', 'figure': figure, 'attempts': attempts}
        feedback = {'raw': raw, 'errors': errors}
    leak = any('지문에 없습니다' in e for e in attempts[-1]['errors'])
    return {'id': item.id, 'status': 'leak' if leak else 'rejected', 'figure': None, 'attempts': attempts}


## @brief 운영 26B 호출 함수 생성; 호출마다 프로세스 사이 공유 잠금 칸 하나를 잡아 동시 요청 수를 PROD_SLOTS 이하로 둠
def model_complete(base_url):
    from openai import OpenAI

    from eval.harness_measure import _prod_slot
    client = OpenAI(base_url=base_url, api_key='not-needed', max_retries=2, timeout=300)
    model = client.models.list().data[0].id

    def complete(messages):
        with _prod_slot(PROD_SLOTS):
            response = client.chat.completions.create(
                model=model, messages=messages, temperature=0, max_tokens=3000,
                response_format={'type': 'json_schema',
                                 'json_schema': {'name': 'figure', 'strict': True, 'schema': RESPONSE_SCHEMA}})
        return response.choices[0].message.content
    return complete


## @brief 사람 검수에서 뺀 문항을 dropped로 표시하고 이유를 남김; 검사 통과(ok) 초안만 대상
def mark_dropped(drafts, reasons):
    for draft in drafts:
        if draft['id'] in reasons and draft['status'] == 'ok':
            draft.update(status='dropped', reason=reasons[draft['id']])
    return drafts


## @brief 초안 결과를 상태별로 셈; PR 본문의 통과율·도식 없음·누설로 버린 개수·검수 제외 개수에 씀
def summarize(drafts):
    counts = Counter(d['status'] for d in drafts)
    return {'total': len(drafts), **{k: counts.get(k, 0) for k in ('ok', 'none', 'leak', 'rejected', 'dropped')}}


## @brief 통과한 초안(status ok)의 figure를 해당 문항 줄에 넣어 items-*.jsonl을 다시 씀
# - 다른 문항 줄과 필드 순서는 그대로 두고, 대상 문항 줄만 figure를 붙이거나 바꿈(ok가 아니면 figure를 지움)
def apply_drafts(drafts, folders):
    by_id = {d['id']: d for d in drafts}
    changed = 0
    for path in (p for folder in folders for p in sorted(folder.glob('items-*.jsonl'))):
        lines = path.read_text(encoding='utf-8').splitlines()
        out = []
        for line in lines:
            item = json.loads(line) if line.strip() else None
            draft = by_id.get(item['id']) if item else None
            if draft:
                item.pop('figure', None)
                if draft['status'] == 'ok':
                    item['figure'] = draft['figure']
                    changed += 1
                line = json.dumps(item, ensure_ascii=False)
            out.append(line)
        path.write_text('\n'.join(out) + '\n', encoding='utf-8')
    return changed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--draft', action='store_true', help='진단 문항 도식 초안을 생성해 --out에 저장')
    mode.add_argument('--apply', action='store_true', help='--out의 통과 초안을 문항 은행 파일에 씀')
    parser.add_argument('--out', type=Path, default=AI_CORE / 'outputs' / 'figures' / 'drafts.json')
    parser.add_argument('--review', type=Path, help='--apply 때 검수에서 뺄 문항 {ID: 이유} JSON 파일')
    parser.add_argument('--ids', default='', help='쉼표로 구분한 문항 ID만 생성(기존 초안 파일의 다른 항목은 유지)')
    parser.add_argument('--base-url', default='http://127.0.0.1:8000/v1', help='운영 26B 주소(읽기 호출만)')
    args = parser.parse_args(argv)

    if args.apply:
        from serving.concept_map import subject_dirs
        drafts = json.loads(args.out.read_text(encoding='utf-8'))
        if args.review:
            mark_dropped(drafts, json.loads(args.review.read_text(encoding='utf-8')))
            args.out.write_text(json.dumps(drafts, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'figure를 넣은 문항 {apply_drafts(drafts, subject_dirs())}개', json.dumps(summarize(drafts), ensure_ascii=False))
        load_item_bank()  # 쓴 결과가 문항 모델 검사를 모두 통과하는지 바로 확인
        return

    wanted = set(filter(None, args.ids.split(',')))
    items = [i for i in load_item_bank() if i.purpose == 'diagnosis' and (not wanted or i.id in wanted)]
    previous = json.loads(args.out.read_text(encoding='utf-8')) if args.out.exists() else []
    complete = model_complete(args.base_url)
    with ThreadPoolExecutor(PROD_SLOTS) as pool:
        fresh = {d['id']: d for d in pool.map(lambda item: draft_item(item, complete), items)}
    drafts = [fresh.pop(d['id'], d) for d in previous] + list(fresh.values())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(drafts, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(summarize(drafts), ensure_ascii=False))


if __name__ == '__main__':
    main()
