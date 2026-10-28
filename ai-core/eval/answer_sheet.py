## @file
# @brief 문항 은행의 서술 문항으로 한국어 답 수집 시트(CSV)를 만듦; 사람이 쓴 답은 교차 채점(MVP 3)과 M3 근거로 쓰임
"""Export a blank Korean answer-collection sheet for explanation items (no rubric, no rationale)."""
import argparse
import csv
import secrets
import sys
from pathlib import Path

AI_CORE = Path(__file__).resolve().parents[1]
# 저장소 루트에서 `python ai-core/eval/answer_sheet.py`로 실행해도 serving 패키지를 찾게 함
if str(AI_CORE) not in sys.path:
    sys.path.insert(0, str(AI_CORE))

from serving.item_bank import load_item_bank  # noqa: E402

# git-ignored 폴더; 답 원문이 Git에 들어가지 않도록 시트는 이 아래에만 씀
OUTPUT_ROOT = AI_CORE / 'outputs'
# 시트 열 계약
# - author·answer: 답 쓰는 사람이 채우는 빈칸
# - 루브릭·모범 근거는 일부러 뺌; 채점 기준을 보고 답을 쓰면 실제 답과 달라지기 때문
COLUMNS = ('answer_id', 'item_id', 'concept', 'question', 'phase', 'author', 'answer')
# before: 학습 전 자기 말 설명, after: 보충 학습 뒤 같은 질문에 다시 쓴 설명
PHASES = ('before', 'after')


## @brief 시트 안에서 겹치지 않는 answer_id('A' + 무작위 숫자 7자리)를 만들어 used에 넣고 반환
# - 무작위인 이유: 채점자가 문항 순서나 작성자를 짐작하지 못하게 하기 위함
# - 'A'로 시작하는 이유: 엑셀이 숫자처럼 보이는 id(예전 16진수 id의 1e5c3a20 → 지수 표기, 앞자리 0)를 바꾸지 못하게 하기 위함
# - 예전 16진수 id 시트도 채점 도구가 그대로 읽음(문자열로만 비교)
def new_answer_id(used):
    while True:
        answer_id = f'A{secrets.randbelow(10 ** 7):07d}'
        if answer_id not in used:
            used.add(answer_id)
            return answer_id


## @brief 서술 문항마다 before·after 두 줄을 만들어 반환
# - concepts가 주어지면 그 개념의 문항만 고름
def build_rows(items, concepts=None):
    chosen = [item for item in items if item.purpose == 'explanation' and (concepts is None or item.concept in concepts)]
    used, rows = set(), []
    for item in chosen:
        for phase in PHASES:
            rows.append({'answer_id': new_answer_id(used), 'item_id': item.id, 'concept': item.concept,
                         'question': item.question, 'phase': phase, 'author': '', 'answer': ''})
    return rows


## @brief 명령행 인자를 읽어 시트를 씀
# - --out이 ai-core/outputs/ 밖이면(../ 우회 포함) 파일을 만들지 않고 종료
# - --concepts에 서술 문항이 없는 개념이 있으면 오타로 보고 종료; 조용히 빈 줄이 빠지는 일을 막음
# - 이미 있는 파일은 덮어쓰지 않음; 채운 답 원문 보호(Git 밖이라 되살릴 수 없음)
#   - 'x' 모드로 열어 확인과 생성을 한 번에 함; 확인 뒤 다른 프로세스가 같은 파일을 만드는 틈도 막음
# - 엑셀에서 한글이 깨지지 않게 UTF-8 BOM(utf-8-sig)으로 씀
def main(argv=None):
    parser = argparse.ArgumentParser(description='서술 문항 한국어 답 수집 시트 내보내기')
    parser.add_argument('--out', required=True, type=Path, help='ai-core/outputs/ 아래 CSV 경로')
    parser.add_argument('--concepts', nargs='+', help='시트에 넣을 개념 ID(생략하면 전체)')
    args = parser.parse_args(argv)

    out = args.out.resolve()
    if not out.is_relative_to(OUTPUT_ROOT):
        parser.error(f'--out은 {OUTPUT_ROOT} 아래여야 합니다: {out}')
    items = load_item_bank()
    if args.concepts:
        known = {item.concept for item in items if item.purpose == 'explanation'}
        unknown = sorted(set(args.concepts) - known)
        if unknown:
            parser.error(f'서술 문항이 없는 개념입니다: {", ".join(unknown)}')
    rows = build_rows(items, set(args.concepts) if args.concepts else None)

    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        file = open(out, 'x', encoding='utf-8-sig', newline='')
    except FileExistsError:
        parser.error(f'이미 있는 파일은 덮어쓰지 않습니다(채운 답 보호). 다른 경로를 쓰세요: {out}')
    with file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f'{out}: {len(rows)}줄')


if __name__ == '__main__':
    main()
