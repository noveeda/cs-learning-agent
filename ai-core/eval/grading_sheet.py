# 파일 역할: 한국어 서술 답(MVP 2 답 시트)을 사람이 채점할 눈가림 시트와 채점 기준표로 바꿈
# - 사람 라벨은 M3(서술 채점 신뢰도)의 최종 근거라, 채점자가 작성자·시점·AI 판정을 보고 기울지 않게 하기 위함
# - 시트와 결과는 git-ignored 폴더(ai-core/outputs/) 아래에만 씀(학생 답 원문이 Git에 들어가지 않게)
"""Build a blinded human grading sheet (and criteria sheet) from collected Korean answers."""
import argparse
import csv
import random
import sys
import zlib
from pathlib import Path

AI_CORE = Path(__file__).resolve().parents[1]
# 저장소 루트에서 `python ai-core/eval/grading_sheet.py`로 실행해도 serving 패키지를 찾게 함
if str(AI_CORE) not in sys.path:
    sys.path.insert(0, str(AI_CORE))

from serving.item_bank import load_item_bank  # noqa: E402

OUTPUT_ROOT = AI_CORE / 'outputs'
# 서술 루브릭은 2~4개(문항 스키마)라 시트 칸을 4개로 고정
RUBRIC_SLOTS = 4
# 연습용 답 수; 채점 방법을 익히는 데 쓰고 결과 계산에서는 뺌
PRACTICE_COUNT = 5
# 시트 열 계약
# - 작성자(author)·시점(phase)·문항 ID·AI 판정은 넣지 않음(눈가림)
# - met_k·overall_pass: 채점자가 1(충족·통과) 또는 0을 적는 빈칸
SHEET_COLUMNS = ('answer_id', 'practice', 'question', 'answer',
                 *(f'rubric_{k}' for k in range(1, RUBRIC_SLOTS + 1)),
                 *(f'met_{k}' for k in range(1, RUBRIC_SLOTS + 1)), 'overall_pass', 'note')
CRITERIA_COLUMNS = ('item_id', 'question', 'rubric_no', 'rubric', 'met_example')


# 역할: 답 시트 CSV들을 읽어 빈 답을 뺀 행 목록 반환; 빈 답은 채점할 내용이 없어 사람·AI 모두 채점하지 않음
def read_answers(paths):
    rows = []
    for path in paths:
        with open(path, encoding='utf-8-sig', newline='') as file:
            rows += list(csv.DictReader(file))
    return [r for r in rows if r['answer'].strip()]


# 역할: 채점자·회차마다 고정된 섞기 seed; 같은 채점자가 시트를 다시 만들어도 순서가 같고, 채점자·재채점 회차끼리는 순서가 다름
def grader_seed(grader, round_no=1):
    return zlib.crc32(f'{grader}:{round_no}'.encode())


# 역할: 시트 한 행; 문항과 루브릭은 문항 은행 원문을 쓰고 판정 칸은 비워 둠
def sheet_row(answer, item, practice):
    row = {'answer_id': answer['answer_id'], 'practice': '1' if practice else '0', 'question': item.question,
           'answer': answer['answer'], 'overall_pass': '', 'note': ''}
    for k in range(1, RUBRIC_SLOTS + 1):
        row[f'rubric_{k}'] = item.rubric[k - 1] if k <= len(item.rubric) else ''
        row[f'met_{k}'] = ''
    return {column: row[column] for column in SHEET_COLUMNS}


# 역할: 채점자 시트 행 목록 반환
# - 연습 5개: 개발자 답에서 고정 seed로 골라 맨 앞에 둠(채점자 모두 같은 연습 답으로 기준을 맞추기 위함)
# - 나머지: 채점자 seed로 섞음(순서 효과가 채점자끼리 겹치지 않게 하기 위함)
def build_sheet(answers, items, grader, round_no=1):
    by_id = {i.id: i for i in items}
    answers = [a for a in answers if a['answer'].strip()]
    dev = sorted((a for a in answers if a['author'] == 'dev'), key=lambda a: a['answer_id'])
    practice = random.Random(0).sample(dev, min(PRACTICE_COUNT, len(dev)))
    chosen = {a['answer_id'] for a in practice}
    rest = sorted((a for a in answers if a['answer_id'] not in chosen), key=lambda a: a['answer_id'])
    random.Random(grader_seed(grader, round_no)).shuffle(rest)
    return ([sheet_row(a, by_id[a['item_id']], True) for a in practice]
            + [sheet_row(a, by_id[a['item_id']], False) for a in rest])


# 역할: 채점 기준표 행 목록; 시트에 나온 문항의 루브릭 요소마다 "충족" 예시를 적을 빈칸을 둠(채점자끼리 기준을 맞추는 메모)
def build_criteria(answers, items):
    used = {a['item_id'] for a in answers if a['answer'].strip()}
    return [{'item_id': item.id, 'question': item.question, 'rubric_no': str(n), 'rubric': rubric, 'met_example': ''}
            for item in items if item.id in used for n, rubric in enumerate(item.rubric, 1)]


# 역할: outputs 아래 새 CSV로 씀; 채점자가 채운 시트를 덮어쓰지 않도록 이미 있으면 거부(채운 판정은 되살릴 수 없음)
def write_csv(parser, path, columns, rows):
    path = Path(path).resolve()
    if not path.is_relative_to(OUTPUT_ROOT):
        parser.error(f'출력은 {OUTPUT_ROOT} 아래여야 합니다: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        file = open(path, 'x', encoding='utf-8-sig', newline='')
    except FileExistsError:
        parser.error(f'이미 있는 파일은 덮어쓰지 않습니다(채운 판정 보호): {path}')
    with file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    print(f'{path}: {len(rows)}줄')


# 역할: 명령행 실행; 채점 시트(--out)와 같은 폴더에 <이름>-criteria.csv 기준표를 함께 씀(엑셀용 UTF-8 BOM)
def main(argv=None):
    parser = argparse.ArgumentParser(description='한국어 서술 답 눈가림 채점 시트 만들기')
    parser.add_argument('--answers', nargs='+', required=True, type=Path, help='MVP 2 답 시트 CSV')
    parser.add_argument('--grader', required=True, help='채점자 코드(예: G1, G2)')
    parser.add_argument('--out', required=True, type=Path, help='ai-core/outputs/grading/<채점자>.csv')
    args = parser.parse_args(argv)
    answers, items = read_answers(args.answers), load_item_bank()
    write_csv(parser, args.out, SHEET_COLUMNS, build_sheet(answers, items, args.grader))
    write_csv(parser, args.out.with_name(f'{args.out.stem}-criteria.csv'), CRITERIA_COLUMNS, build_criteria(answers, items))


if __name__ == '__main__':
    main()
