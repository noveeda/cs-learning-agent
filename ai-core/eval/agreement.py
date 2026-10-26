# 파일 역할: 사람 채점 시트와 AI 판정을 모아 채점 일치도 보고서(M3)를 만듦
# - 사람 라벨: 채점자 2명이면 사람 간 kappa와 합의 라벨, 1명이면 재채점으로 채점자 내 kappa
# - AI 대 최종 라벨: kappa, 거짓 통과율(사람 미통과 중 AI 통과)과 Wilson 95% 구간을 학생 답·개발자 답·전체로 나눠 냄(설계 2-4)
# - 지표 정의는 eval/metrics.py 하나만 씀(Mohler 개발 검증과 같은 계산)
"""Compute human-human and AI-human agreement for Korean explanation grading."""
import argparse
import csv
import json
import random
import sys
from pathlib import Path

AI_CORE = Path(__file__).resolve().parents[1]
if str(AI_CORE) not in sys.path:
    sys.path.insert(0, str(AI_CORE))

from eval.grading_sheet import OUTPUT_ROOT, RUBRIC_SLOTS, SHEET_COLUMNS, grader_seed, read_answers, write_csv  # noqa: E402
from eval.metrics import cohen_kappa, wilson_interval  # noqa: E402

# 거짓 통과율 10% 이하를 의미 있게 말하려면 미통과 답이 이만큼 필요함(설계 2-4 목표 "미통과 100개 이상")
MIN_FAILS = 100
# 채점자 1명일 때 재채점할 답 수(설계 2-4: 2주 뒤 같은 답 50개)
REGRADE_SIZE = 50
FINAL_COLUMNS = ('answer_id', 'question', 'answer', 'grader_a', 'grader_b', 'final_pass')


def _flag(value):
    value = (value or '').strip()
    return True if value == '1' else False if value == '0' else None


# 역할: 시트 행에서 채점이 끝난 답의 판정만 모음 {answer_id: {'overall': bool, 'met': [bool|None]*4}}
# - 연습 답과 전체 판정(overall_pass)이 빈칸인 답은 빼서, 덜 채운 시트도 계산을 깨지 않게 함
def labels(rows):
    found = {}
    for row in rows:
        overall = _flag(row.get('overall_pass'))
        if row.get('practice') == '1' or overall is None:
            continue
        found[row['answer_id']] = {'overall': overall,
                                   'met': [_flag(row.get(f'met_{k}')) for k in range(1, RUBRIC_SLOTS + 1)]}
    return found


# 역할: 두 판정 묶음의 공통 답에서 전체 판정 kappa·일치율과 요소 단위 kappa(두 쪽 모두 적은 칸만)를 계산
# - 일치율을 함께 내는 이유: 판정이 한 범주로 쏠리면 kappa가 부풀거나 0으로 떨어져 kappa만으로는 읽기 어려움
def _compare(first, second):
    common = sorted(set(first) & set(second))
    result = {'compared': len(common), 'kappa': None, 'agreement': None, 'element_kappa': None}
    if common:
        result['kappa'] = cohen_kappa([first[i]['overall'] for i in common], [second[i]['overall'] for i in common])
        result['agreement'] = sum(first[i]['overall'] == second[i]['overall'] for i in common) / len(common)
        pairs = [(a, b) for i in common for a, b in zip(first[i]['met'], second[i]['met']) if a is not None and b is not None]
        if pairs:
            result['element_kappa'] = cohen_kappa([a for a, _ in pairs], [b for _, b in pairs])
    return result, common


# 역할: 한 묶음(학생·개발자·전체)의 AI 대 최종 라벨 지표
# - 거짓 통과율: 사람이 미통과로 본 답 중 AI가 통과시킨 비율; 학습자가 이해하지 못했는데 넘어가는 위험을 잼
# - 민감도(사람 통과 중 AI 통과)·특이도(사람 미통과 중 AI 미통과)·일치율·표본 수를 함께 냄
#   한 범주만 있는 하위 집단(예: 모두 미통과)에서는 kappa가 부풀거나 0이 되어 그것만으로 판단하면 안 되기 때문
#   해당 범주 답이 없으면 비율은 None
def _ai_group(ids, final, ai):
    ids = [i for i in ids if i in ai]
    passes = [i for i in ids if final[i]]
    fails = [i for i in ids if not final[i]]
    false_passes = sum(ai[i] for i in fails)
    group = {'n': len(ids), 'passes': len(passes), 'fails': len(fails), 'false_passes': false_passes,
             'kappa': cohen_kappa([final[i] for i in ids], [ai[i] for i in ids]) if ids else None,
             'agreement': sum(final[i] == ai[i] for i in ids) / len(ids) if ids else None,
             'sensitivity': sum(ai[i] for i in passes) / len(passes) if passes else None,
             'specificity': (len(fails) - false_passes) / len(fails) if fails else None,
             'false_pass_rate': false_passes / len(fails) if fails else None,
             'false_pass_interval': list(wilson_interval(false_passes, len(fails)))}
    return group


# 역할: 일치도 보고서 dict 반환(맨 앞 키는 warnings)
# - sheets: {채점자 코드: 시트 행}; 1명 또는 2명
# - answers: 원래 답 시트 행(작성자 구분에만 씀), ai_rows: ai_grade 결과 행
# - final_rows: 갈린 답의 합의 라벨(answer_id, final_pass), regrade_rows: 채점자 1명일 때 재채점 시트
# - 채점자 2명이면 최종 라벨은 두 사람이 모두 채점한 답만 씀; 한쪽만 채점한 답은 개수(only_one_grader)만 보고
def analyze(sheets, answers, ai_rows=None, final_rows=None, regrade_rows=None):
    if not 1 <= len(sheets) <= 2:
        raise ValueError('채점 시트는 1개 또는 2개여야 합니다.')
    graders = sorted(sheets)
    marked = {g: labels(sheets[g]) for g in graders}
    warnings, report = [], {}
    if len(graders) == 2:
        a, b = marked[graders[0]], marked[graders[1]]
        human, common = _compare(a, b)
        human = {'kind': 'inter', 'graders': graders, **human, 'only_one_grader': len(set(a) ^ set(b))}
        disagreements = [i for i in common if a[i]['overall'] != b[i]['overall']]
        settled = {r['answer_id']: _flag(r.get('final_pass')) for r in final_rows or []}
        final = {i: a[i]['overall'] for i in common if i not in disagreements}
        final.update({i: settled[i] for i in disagreements if settled.get(i) is not None})
        report['disagreements'] = disagreements
        pending = len(disagreements) - sum(i in final for i in disagreements)
        if pending:
            warnings.append(f'판정이 갈린 답 {pending}개에 합의 라벨이 없어 최종 라벨에서 뺐습니다.')
    else:
        first = marked[graders[0]]
        human = {'kind': 'intra', 'graders': graders}
        if regrade_rows is None:
            human.update(status='pending', regrade_size=min(REGRADE_SIZE, len(first)))
        else:
            compared, _ = _compare(first, labels(regrade_rows))
            human.update(status='done', **compared)
        final, pending = {i: v['overall'] for i, v in first.items()}, 0
    report['human'] = human
    report['final'] = {'labeled': len(final), 'pending': pending, 'fails': sum(not v for v in final.values())}
    if report['final']['fails'] < MIN_FAILS:
        warnings.insert(0, f'최종 라벨 미통과 답이 {report["final"]["fails"]}개로 {MIN_FAILS}개 미만입니다: 거짓 통과율 상한 기준 미달 가능')
    if ai_rows is not None:
        ai = {r['answer_id']: _flag(r.get('overall_pass')) for r in ai_rows}
        ai = {i: v for i, v in ai.items() if v is not None}
        author = {r['answer_id']: r['author'] for r in answers}
        ids = sorted(final)
        report['ai'] = {'student': _ai_group([i for i in ids if author.get(i, '').startswith('S')], final, ai),
                        'dev': _ai_group([i for i in ids if author.get(i) == 'dev'], final, ai),
                        'all': _ai_group(ids, final, ai),
                        'missing_ai': sum(i not in ai for i in ids)}
    return {'warnings': warnings, **report}


# 역할: 채점자 1명일 때 재채점 시트; 채점이 끝난 답 중 최대 size개를 다른 seed로 골라 섞고 판정 칸을 비움
def regrade_sheet(rows, grader, size=REGRADE_SIZE):
    done = labels(rows)
    picked = [r for r in rows if r['answer_id'] in done]
    rng = random.Random(grader_seed(grader, 2))
    picked = rng.sample(picked, min(size, len(picked)))
    blank = {f'met_{k}': '' for k in range(1, RUBRIC_SLOTS + 1)} | {'overall_pass': '', 'note': ''}
    return [{column: ({**row, **blank}[column]) for column in SHEET_COLUMNS} for row in picked]


def _read(path):
    with open(path, encoding='utf-8-sig', newline='') as file:
        return list(csv.DictReader(file))


# 역할: 명령행 실행; 보고서를 쓰고, 다음 사람 작업에 필요한 파일(재채점 시트, 합의 라벨 칸)이 없으면 함께 만듦
def main(argv=None):
    parser = argparse.ArgumentParser(description='한국어 서술 채점 일치도 보고서')
    parser.add_argument('--sheets', nargs='+', required=True, type=Path, help='채운 채점 시트(G1.csv [G2.csv]); 파일 이름이 채점자 코드')
    parser.add_argument('--answers', nargs='+', required=True, type=Path, help='MVP 2 답 시트 CSV(작성자 구분용)')
    parser.add_argument('--ai', type=Path, help='ai_grade.py 결과')
    parser.add_argument('--final', type=Path, help='갈린 답의 합의 라벨(final.csv)')
    parser.add_argument('--regrade', type=Path, help='채점자 1명일 때 채운 재채점 시트')
    parser.add_argument('--out', required=True, type=Path, help='ai-core/outputs/grading/report.json')
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if not out.is_relative_to(OUTPUT_ROOT) or out.exists():
        parser.error(f'--out은 {OUTPUT_ROOT} 아래의 새 파일이어야 합니다: {out}')
    sheets = {path.stem: _read(path) for path in args.sheets}
    answers = read_answers(args.answers)
    report = analyze(sheets, answers, ai_rows=_read(args.ai) if args.ai else None,
                     final_rows=_read(args.final) if args.final else None,
                     regrade_rows=_read(args.regrade) if args.regrade else None)
    if len(sheets) == 1 and not args.regrade:
        grader = next(iter(sheets))
        target = args.sheets[0].with_name(f'{grader}-regrade.csv')
        if not target.exists():
            write_csv(parser, target, SHEET_COLUMNS, regrade_sheet(sheets[grader], grader))
    if report.get('disagreements') and not args.final:
        a, b = (labels(sheets[g]) for g in sorted(sheets))
        by_id = {r['answer_id']: r for r in next(iter(sheets.values()))}
        rows = [{'answer_id': i, 'question': by_id[i]['question'], 'answer': by_id[i]['answer'],
                 'grader_a': int(a[i]['overall']), 'grader_b': int(b[i]['overall']), 'final_pass': ''}
                for i in report['disagreements']]
        target = out.with_name('final.csv')
        if not target.exists():
            write_csv(parser, target, FINAL_COLUMNS, rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'x', encoding='utf-8') as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
