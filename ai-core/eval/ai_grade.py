# 파일 역할: 한국어 서술 답을 운영 채점 경로(StuckModelGateway.grade_explanation)로 채점해 사람 라벨과 비교할 AI 판정을 만듦
# - 판정 규칙은 막힘 흐름 확인 단계와 같음: 루브릭 요소가 모두 충족이어야 통과
# - 운영 모델(8000)에 하나씩 보내고, 결과를 한 줄씩 이어 써서 중단돼도 다음 실행이 남은 답만 채점
"""Grade collected Korean answers with the production rubric grader (resumable)."""
import argparse
import csv
import os
import sys
import threading
from pathlib import Path

AI_CORE = Path(__file__).resolve().parents[1]
if str(AI_CORE) not in sys.path:
    sys.path.insert(0, str(AI_CORE))

from openai import OpenAI  # noqa: E402

from eval.grading_sheet import OUTPUT_ROOT, RUBRIC_SLOTS, read_answers  # noqa: E402
from serving.concept_map import load_concept_map  # noqa: E402
from serving.item_bank import load_item_bank  # noqa: E402
from serving.stuck import LLMUnavailable  # noqa: E402
from serving.stuck_llm import StuckModelGateway  # noqa: E402

AI_COLUMNS = ('answer_id', *(f'met_{k}' for k in range(1, RUBRIC_SLOTS + 1)), 'overall_pass')


# 역할: 답들을 하나씩 채점해 (결과 행 목록, 모델 실패 answer_id 목록) 반환
# - done: 이미 결과에 있는 answer_id; 다시 부르지 않음(재개)
# - 모델 실패(LLMUnavailable) 답은 결과에 쓰지 않고 따로 돌려줌; 다음 실행에서 다시 채점되게 하기 위함
# - met_k는 1·0, 루브릭이 없는 칸은 빈칸
def grade_answers(answers, items, gateway, done=frozenset()):
    by_id = {i.id: i for i in items}
    rows, failed = [], []
    for answer in answers:
        if answer['answer_id'] in done or not answer['answer'].strip():
            continue
        item = by_id[answer['item_id']]
        try:
            met = gateway.grade_explanation(item, answer['answer'])
        except LLMUnavailable:
            failed.append(answer['answer_id'])
            continue
        row = {'answer_id': answer['answer_id'], 'overall_pass': '1' if all(met) else '0'}
        for k in range(1, RUBRIC_SLOTS + 1):
            row[f'met_{k}'] = ('1' if met[k - 1] else '0') if k <= len(met) else ''
        rows.append(row)
    return rows, failed


# 역할: 명령행 실행; 결과 CSV가 있으면 이어서 채점하고, 새 결과는 다 모은 뒤가 아니라 한 줄씩 덧붙임
def main(argv=None):
    parser = argparse.ArgumentParser(description='한국어 서술 답 AI 채점(운영 채점 경로, 순차)')
    parser.add_argument('--answers', nargs='+', required=True, type=Path, help='MVP 2 답 시트 CSV')
    parser.add_argument('--out', required=True, type=Path, help='ai-core/outputs/grading/ai.csv')
    parser.add_argument('--base-url', default='http://127.0.0.1:8000/v1')
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if not out.is_relative_to(OUTPUT_ROOT):
        parser.error(f'--out은 {OUTPUT_ROOT} 아래여야 합니다: {out}')
    done = set()
    if out.exists():
        with open(out, encoding='utf-8-sig', newline='') as file:
            done = {r['answer_id'] for r in csv.DictReader(file)}

    def client():
        return OpenAI(base_url=args.base_url, api_key=os.getenv('VLLM_API_KEY', 'not-needed'), max_retries=0)

    gateway = StuckModelGateway(load_concept_map(), threading.Lock(), client_factory=client)
    items, answers = load_item_bank(), read_answers(args.answers)
    out.parent.mkdir(parents=True, exist_ok=True)
    new_file = not out.exists()
    failed = []
    with open(out, 'a', encoding='utf-8-sig' if new_file else 'utf-8', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=AI_COLUMNS)
        if new_file:
            writer.writeheader()
        for answer in answers:  # 한 답씩 채점하고 바로 써서 중단돼도 그때까지의 결과를 남김
            rows, missed = grade_answers([answer], items, gateway, done)
            writer.writerows(rows)
            file.flush()
            failed += missed
            print(answer['answer_id'], rows[0]['overall_pass'] if rows else ('실패' if missed else '건너뜀'), flush=True)
    print(f'{out}: 새로 채점 {len(answers) - len(done & {a["answer_id"] for a in answers}) - len(failed)}개, 모델 실패 {len(failed)}개')
    if failed:
        print('모델 실패(다시 실행하면 이어서 채점):', ', '.join(failed))


if __name__ == '__main__':
    main()
