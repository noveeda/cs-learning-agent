# 파일 역할: 비교 실험(계획 Task 6 비교 실험 모드) 결과 계산 — 내보낸 실험 기록과 실험 자료의 정답 키로 O1~O3를 냄
# - grading-sheet: 사후 서술 답을 눈가림 채점 시트로 만듦(MVP 3 채점 시트와 같은 열; 참가자·조건·개념을 가림)
# - report: 학생별 표(사전·사후 정답 수, 변화, 서술 충족 비율, O2, O3)와 조건별 차이(system − chat) 요약
# - 참가자가 4~5명이라 유의성 검정은 하지 않음(설계 2-4): 학생별 표, 차이의 중앙값·범위, 사례로 보고
# - 입력·출력은 git-ignored 폴더(ai-core/outputs/experiment/)에만 둠; 학생 답 원문이 Git에 들어가지 않게 하기 위함
#
# 입력 형식(계획 Task 1·2 Interfaces, 2026-10-26 코디네이터 확정)
# - materials.json: {version, concepts: [{id, title, passage, pre_post: {A: [item, item, explanation], B: [...]}}]}
# - export JSON: {exported_at, sessions: [{id, participant, grade, dsa, exclude, stage, pretest_form, posttest_form,
#   pretest: {answers, submitted_at}, pretest_high, learn: [{slot, concept, condition, started_at, deadline,
#   events: [{type, ref?, at}]}], posttest: {answers, submitted_at} | null, survey: {answers} | null}]}
"""Compute comparison-experiment outcomes (O1–O3) from the exported sessions and the answer keys."""
import argparse
import hashlib
import json
import random
import statistics
import sys
from datetime import datetime
from pathlib import Path

AI_CORE = Path(__file__).resolve().parents[1]
# 저장소 루트에서 `python ai-core/eval/experiment_report.py`로 실행해도 serving·eval 패키지를 찾게 함
if str(AI_CORE) not in sys.path:
    sys.path.insert(0, str(AI_CORE))

from eval.agreement import labels  # noqa: E402
from eval.grading_sheet import OUTPUT_ROOT, RUBRIC_SLOTS, SHEET_COLUMNS, grader_seed, read_csv, write_csv  # noqa: E402
from eval.metrics import cohen_kappa  # noqa: E402
from serving.grading import grade  # noqa: E402
from serving.item_bank import Item  # noqa: E402

CONDITIONS = ('system', 'chat')
# 조건 차이를 내는 지표; o2는 복귀 1·미복귀 0으로 바꿔 뺌
METRICS = ('change', 'explanation', 'o2', 'o3_seconds')


# 역할: 눈가림 답 ID; 세션 id와 문항 id로 결정적으로 만들어 별도 대응표 없이 report에서 다시 계산함
# - 문자 접두 'X': 16진수만 있으면 엑셀이 '1e5' 같은 값을 숫자로 바꿔 ID가 깨짐(PR #69와 같은 이유)
def answer_id(session_id, item_id):
    return 'X' + hashlib.sha256(f'{session_id}{item_id}'.encode()).hexdigest()[:8]


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


# 역할: 개념 id → 형(A/B) → 문항 목록; 자동 채점 문항과 서술 문항은 format으로 나눔
def forms(materials):
    return {c['id']: c['pre_post'] for c in materials['concepts']}


# 역할: 자동 채점 문항(선택형·단답) 중 맞힌 개수; 운영 채점기(serving.grading.grade)와 같은 규칙으로 채점
# - 실험 문항 ID(exp-<개념>-<형><번호>)는 문항 은행 ID 규칙과 달라 Item 검증을 건너뛰고 채점에 쓰는 필드만 채움
def auto_correct(items, answers):
    auto = [i for i in items if i['format'] != 'explanation']
    return sum(i['id'] in answers and grade(Item.model_construct(**i), answers[i['id']]) for i in auto)


def _explanation(items):
    return next(i for i in items if i['format'] == 'explanation')


# 역할: 사후 서술 답 목록 [{answer_id, item, answer, session, concept}]; 빈 답은 채점할 내용이 없어 뺌
# - 사전 시험 전 세션은 learn이 없고, 사후 시험 전 세션은 posttest가 없어 자연히 빠짐(export는 모든 세션을 내보냄)
def explanation_answers(export, materials):
    by_concept = forms(materials)
    found = []
    for session in export['sessions']:
        answers = (session.get('posttest') or {}).get('answers') or {}
        if not answers:
            continue
        for learn in session.get('learn') or []:
            item = _explanation(by_concept[learn['concept']][session['posttest_form']])
            text = str(answers.get(item['id']) or '')
            if text.strip():
                found.append({'answer_id': answer_id(session['id'], item['id']), 'item': item, 'answer': text,
                              'session': session['id'], 'concept': learn['concept']})
    return found


# 역할: 채점자 시트 행 목록; 열은 MVP 3 채점 시트(SHEET_COLUMNS)와 같고 판정 칸은 비워 둠
# - 참가자·조건·개념·문항 ID는 넣지 않음(눈가림); 순서는 answer_id로 정렬한 뒤 채점자 seed로 섞음
# - 실험에는 개발자 답이 없어 연습 답(practice=1)은 만들지 않음; 연습은 MVP 3 채점 시트로 이미 함
def build_grading_sheet(export, materials, grader):
    answers = sorted(explanation_answers(export, materials), key=lambda a: a['answer_id'])
    random.Random(grader_seed(grader)).shuffle(answers)
    rows = []
    for a in answers:
        rubric = a['item']['rubric']
        row = {'answer_id': a['answer_id'], 'practice': '0', 'question': a['item']['question'], 'answer': a['answer'],
               'overall_pass': '', 'note': ''}
        for k in range(1, RUBRIC_SLOTS + 1):
            row[f'rubric_{k}'] = rubric[k - 1] if k <= len(rubric) else ''
            row[f'met_{k}'] = ''
        rows.append({column: row[column] for column in SHEET_COLUMNS})
    return rows


# 역할: 채점된 시트들에서 답 하나의 서술 점수(루브릭 충족 비율, 채점자 평균)
# - 전체 판정(overall_pass)을 채운 행만 채점이 끝난 답으로 봄(agreement.labels, MVP 3 일치도 계산과 같은 규칙)
# - 루브릭 개수만큼의 칸 중 채운 칸만 셈; 한 칸도 안 채운 채점자는 그 답에서 빠짐
# - 아무도 채점하지 않았으면 None
def _explanation_score(aid, rubric_len, sheet_labels):
    ratios = []
    for found in sheet_labels:
        met = [m for m in found.get(aid, {}).get('met', [])[:rubric_len] if m is not None]
        if met:
            ratios.append(sum(met) / len(met))
    return sum(ratios) / len(ratios) if ratios else None


# 역할: 서버 시각 문자열(ISO-8601, 끝 'Z')을 시각 값으로 바꿈
# - 문자열로 비교하지 않는 이유: 서버 Instant.toString()은 0밀리초면 '…:05Z', 아니면 '…:05.120Z'라
#   같은 초 안에서 문자열 순서('.' < 'Z')와 시간 순서가 어긋남(PR #73 검수)
def _parse(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


# 역할: 시작한 학습 단계 하나의 O2·O3와 도움 사용 여부
# - O2: 계속 읽기를 눌렀는가(마감 뒤 사건은 서버가 거부하므로 기록에 있으면 시간 안)
# - O3: 첫 계속 읽기보다 앞선 첫 도움 요청(help_open)부터 그 계속 읽기까지 초
#   - 서버는 계속 읽기 뒤에도 마감 전이면 help_open을 받으므로, 그 뒤의 도움 요청은 O3에 쓰지 않음(음수 O3 방지)
#   - 계속 읽기 전에 도움을 연 적이 없거나 복귀하지 못했으면 None; 0초로 두면 "빨리 복귀"로 잘못 읽히기 때문
# - helped: 이 단계에서 도움을 한 번이라도 열었는가(계속 읽기 뒤 포함)
def _o2_o3(events):
    timed = sorted(((_parse(e['at']), e['type']) for e in events), key=lambda pair: pair[0])
    back = next((t for t, kind in timed if kind == 'continue_reading'), None)
    helps = [t for t, kind in timed if kind == 'help_open']
    before = next((t for t in helps if back is not None and t < back), None)
    o3 = round((back - before).total_seconds()) if before is not None else None
    return back is not None, bool(helps), o3


# 역할: 끝나지 않은 세션·단계 목록 [{participant, slot, reason}]
# - pretest_not_submitted: 사전 시험 전이라 개념·조건 배정이 없음 → 학생별 표에서 뺌
# - learn_not_started: 배정은 됐지만 그 학습 단계를 시작하지 않음(events·started_at 없음) → 표에는 started=False로 남김
# - 실험 중 중단·재시작이나 진행자의 연습 세션이 export에 함께 나오므로, 멈추지 않고 따로 보고하기 위함
def incomplete_entries(export):
    found = []
    for session in export['sessions']:
        if not session.get('pretest'):
            found.append({'participant': session['participant'], 'slot': None, 'reason': 'pretest_not_submitted'})
            continue
        for learn in sorted(session.get('learn') or [], key=lambda l: l['slot']):
            if not _started(learn):
                found.append({'participant': session['participant'], 'slot': learn['slot'], 'reason': 'learn_not_started'})
    return found


def _started(learn):
    return bool(learn.get('started_at')) and 'events' in learn


# 역할: 학생 × 개념 행 목록(학생별 표)
# - pre: 사전 형 자동 채점 2문항 정답 수, post: 사후 형 자동 채점 2문항 정답 수, change: post − pre
# - explanation: 사후 서술 충족 비율(채점자 평균); 빈 답은 채점 없이 0(모른 답이 계산에서 빠져 점수가 부풀지 않게)
# - 사후 시험 전이면 post·change·explanation은 None
# - 사전 시험 전 세션은 뺌; 시작 전 학습 단계는 started=False이고 O2·O3·helped는 None(미복귀 False로 세지 않음)
def student_rows(export, materials, sheets):
    by_concept = forms(materials)
    sheet_labels = [labels(rows) for rows in sheets]
    rows = []
    for session in export['sessions']:
        if not session.get('pretest'):
            continue
        pre_answers = session['pretest']['answers']
        post = session.get('posttest')
        for learn in sorted(session['learn'], key=lambda l: l['slot']):
            concept = by_concept[learn['concept']]
            row = {'participant': session['participant'], 'slot': learn['slot'], 'concept': learn['concept'],
                   'condition': learn['condition'], 'pretest_high': bool(session.get('pretest_high')),
                   'pre': auto_correct(concept[session['pretest_form']], pre_answers),
                   'post': None, 'change': None, 'explanation': None}
            if post is not None:
                items = concept[session['posttest_form']]
                row['post'] = auto_correct(items, post['answers'])
                row['change'] = row['post'] - row['pre']
                item = _explanation(items)
                if str(post['answers'].get(item['id']) or '').strip():
                    row['explanation'] = _explanation_score(answer_id(session['id'], item['id']), len(item['rubric']), sheet_labels)
                else:
                    row['explanation'] = 0.0
            row['started'] = _started(learn)
            row['o2'], row['helped'], row['o3_seconds'] = _o2_o3(learn['events']) if row['started'] else (None, None, None)
            rows.append(row)
    return rows


# 역할: 지표 하나의 조건 차이 요약; 학생마다 system 값 − chat 값, 두 값이 모두 있는 학생만
def _differences(rows, metric):
    by_student = {}
    for participant in dict.fromkeys(r['participant'] for r in rows):
        values = {r['condition']: r[metric] for r in rows if r['participant'] == participant}
        if all(values.get(c) is not None for c in CONDITIONS):
            by_student[participant] = (int(values['system']) if metric == 'o2' else values['system']) - \
                                      (int(values['chat']) if metric == 'o2' else values['chat'])
    diffs = list(by_student.values())
    return {'n': len(diffs), 'median': statistics.median(diffs) if diffs else None,
            'min': min(diffs) if diffs else None, 'max': max(diffs) if diffs else None, 'by_student': by_student}


# 역할: 두 채점자 시트의 전체 판정(overall_pass) 일치도; 채점자가 둘이 아니면 None
# - 일치율을 함께 내는 이유: 판정이 한 범주로 쏠리면 kappa만으로는 읽기 어려움
def _grader_agreement(sheets):
    if len(sheets) != 2:
        return None
    first, second = (labels(rows) for rows in sheets)
    common = sorted(set(first) & set(second))
    if not common:
        return {'compared': 0, 'kappa': None, 'agreement': None}
    a, b = [first[i]['overall'] for i in common], [second[i]['overall'] for i in common]
    return {'compared': len(common), 'kappa': cohen_kappa(a, b), 'agreement': sum(x == y for x, y in zip(a, b)) / len(common)}


# 역할: 보고서 사전; 학생별 표, 조건별 차이 요약(전체 / 사전 점수 높음 제외), 사전 점수 높음 학생, 미완료 목록, 채점자 일치도
# - students는 export의 세션 수(미완료 포함); 미완료 학생은 한쪽 조건 값이 없으면 조건 차이에서 자연히 빠짐
def build_report(export, materials, sheets):
    rows = student_rows(export, materials, sheets)
    without_high = [r for r in rows if not r['pretest_high']]
    return {'exported_at': export.get('exported_at'), 'students': len(export['sessions']),
            'pretest_high': sorted({r['participant'] for r in rows if r['pretest_high']}),
            'incomplete': incomplete_entries(export),
            'rows': rows,
            'summary': {'all': {m: _differences(rows, m) for m in METRICS},
                        'excluding_pretest_high': {m: _differences(without_high, m) for m in METRICS}},
            'grader_agreement': _grader_agreement(sheets),
            'note': '참가자 수가 적어 유의성 검정은 하지 않음. 차이는 system − chat.'}


# 역할: 명령행 진입점(grading-sheet, report); 출력은 ai-core/outputs 아래만 허용하고 이미 있으면 덮어쓰지 않음
def main(argv=None):
    parser = argparse.ArgumentParser(description='비교 실험 결과 계산(O1~O3)')
    commands = parser.add_subparsers(dest='command', required=True)
    sheet_cmd = commands.add_parser('grading-sheet', help='사후 서술 답 눈가림 채점 시트')
    report_cmd = commands.add_parser('report', help='학생별 표와 조건별 차이')
    for cmd in (sheet_cmd, report_cmd):
        cmd.add_argument('--export', required=True, type=Path, help='GET /api/experiments/export 결과 JSON')
        cmd.add_argument('--materials', required=True, type=Path, help='backend/src/main/resources/experiment/materials.json')
        cmd.add_argument('--out', required=True, type=Path, help='ai-core/outputs/experiment/ 아래 경로')
    sheet_cmd.add_argument('--grader', required=True, help='채점자 코드(예: G1, G2)')
    report_cmd.add_argument('--grades', nargs='*', default=[], type=Path, help='채점자가 채운 시트 CSV(0~2개)')
    args = parser.parse_args(argv)

    export, materials = load_json(args.export), load_json(args.materials)
    if args.command == 'grading-sheet':
        write_csv(parser, args.out, SHEET_COLUMNS, build_grading_sheet(export, materials, args.grader))
        return
    out = args.out.resolve()
    if not out.is_relative_to(OUTPUT_ROOT):
        parser.error(f'출력은 {OUTPUT_ROOT} 아래여야 합니다: {out}')
    try:
        sheets = [read_csv(path) for path in args.grades]
    except ValueError as exc:
        parser.error(str(exc))
    result = build_report(export, materials, sheets)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(out, 'x', encoding='utf-8') as file:
            json.dump(result, file, ensure_ascii=False, indent=2)
    except FileExistsError:
        parser.error(f'이미 있는 보고서는 덮어쓰지 않습니다: {out}')
    print(json.dumps(result['summary'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
