## @file
# @brief 문항 은행 질문과 로컬 CS-300 질문의 문장 유사도를 비교해 겹치는 쌍을 찾음
# - CS-300은 평가 전용 자료라 학습용 문항과 문장이 겹치면 평가가 오염됨
# - CS-300은 Git 밖 로컬 자료이므로 경로는 인자로만 받고 CI에서는 돌리지 않음
"""Report item-bank questions that nearly duplicate a local CS-300 question (local use only)."""
import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

# 정규화한 문장의 SequenceMatcher 비율이 이 값 이상이면 같은 문항을 옮겨 적은 것으로 봄
THRESHOLD = 0.8


## @brief 공백·문장부호를 지우고 소문자로 바꿔, 표기만 다른 문장을 같은 문장으로 비교하게 함
def normalize(text):
    return re.sub(r'[\W_]+', '', text).lower()


## @brief 문항·CS-300 질문 목록({'id', 'question'})을 모두 짝지어 비율이 기준 이상인 (비율, 문항 ID, CS-300 ID)를 높은 순으로 반환
def find_overlaps(items, cs300, threshold=THRESHOLD):
    others = [(other['id'], normalize(other['question'])) for other in cs300]
    pairs = []
    for item in items:
        mine = normalize(item['question'])
        for other_id, theirs in others:
            ratio = SequenceMatcher(None, mine, theirs).ratio()
            if ratio >= threshold:
                pairs.append((ratio, item['id'], other_id))
    return sorted(pairs, reverse=True)


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


## @brief 명령행 인자로 받은 두 위치를 읽어 겹치는 쌍을 출력; 하나라도 있으면 종료 코드 1로 알림
def main(argv=None):
    parser = argparse.ArgumentParser(description='문항 은행과 CS-300의 유사 문항 검사(로컬 전용)')
    parser.add_argument('--cs300', required=True, type=Path, help='questions.jsonl이 있는 로컬 CS-300 폴더')
    parser.add_argument('--items', required=True, type=Path, help='items-*.jsonl이 있는 문항 은행 폴더')
    args = parser.parse_args(argv)

    cs300 = read_jsonl(args.cs300 / 'questions.jsonl')
    items = [item for path in sorted(args.items.glob('items-*.jsonl')) for item in read_jsonl(path)]
    pairs = find_overlaps(items, cs300)
    for ratio, item_id, other_id in pairs:
        print(f'{ratio:.3f}\t{item_id}\t{other_id}')
    print(f'문항 {len(items)}개 × CS-300 {len(cs300)}개, 유사 문항 {len(pairs)}건(기준 {THRESHOLD})')
    return 1 if pairs else 0


if __name__ == '__main__':
    sys.exit(main())
