## @file
# @brief 막힘 보충(S4)의 장면 선택(choose_animation → simulate)을 실제 모델로 반복 실행해 성공률·실패 원인·지연을 측정
# - 장면 목록(CONCEPT_SCENES)의 개념마다 로드맵 진입 1개와 붙여넣기 진입 1개를 만들어 --runs 회 시도
# - 결과 원본은 git-ignored 폴더(ai-core/outputs/eval/)에만 쓰고, PR·발표에는 요약만 옮김
"""Measure how often the stuck-flow scene chooser yields a runnable animation."""
import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

AI_CORE = Path(__file__).resolve().parents[1]
# 저장소 루트에서 `python ai-core/eval/animation_choice.py`로 실행해도 serving 패키지를 찾게 함
if str(AI_CORE) not in sys.path:
    sys.path.insert(0, str(AI_CORE))

from openai import OpenAI, OpenAIError  # noqa: E402

from serving.animation import CONCEPT_SCENES, simulate  # noqa: E402
from serving.concept_map import load_concept_map  # noqa: E402
from serving.stuck import Entry, LLMUnavailable  # noqa: E402
from serving.stuck_llm import StuckModelGateway  # noqa: E402

# git-ignored 폴더; 측정 결과 원본은 이 아래에만 씀
EVAL_ROOT = AI_CORE / 'outputs' / 'eval'
# 결과 분류
# - success: 장면을 골랐고 코드 시뮬레이션까지 성공(화면에 애니메이션이 붙음)
# - null: 모델이 알맞은 장면이 없다고 고름(텍스트만 보여 줌)
# - schema_failure: 재시도까지 형식·허용 종류에 맞는 응답을 받지 못함(LLMUnavailable)
# - simulation_failure: 형식은 맞지만 실행할 수 없는 연산을 고름(빈 스택 pop 등)
OUTCOMES = ('success', 'null', 'schema_failure', 'simulation_failure')


## @brief 개념 하나의 측정용 진입 두 가지를 만듦
# - 로드맵: 학습자가 그 개념 화면에서 막힘을 누른 경우
# - 붙여넣기: 개념 설명 문장을 붙여넣은 경우(문항·정답은 쓰지 않음)
def entries_for(concept):
    return [('roadmap', Entry(kind='roadmap', concept=concept.id, return_to='/measure')),
            ('excerpt', Entry(kind='excerpt', excerpt=f'{concept.description} 이 부분이 이해가 안 돼요.', return_to='/measure'))]


## @brief 한 번의 장면 선택 동안 Gateway가 남기는 경고(재시도 원인)를 모음; 스키마 실패의 원인을 결과에 남기기 위함
class _Warnings(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage()[:300])


## @brief 장면 선택을 차례로 실행하고 시도마다 분류·지연·고른 요청·오류를 담은 결과 목록을 반환
# - 요청은 하나씩 보냄: 운영 서버(--max-num-seqs 1)와 GPU를 같이 쓰므로 동시에 보내지 않음
# - progress: 시도마다 한 줄을 받는 함수(명령행에서는 print)
def measure(gateway, runs, concepts=None, concept_map=None, progress=None):
    concept_map = concept_map or load_concept_map()
    by_id = {c.id: c for c in concept_map.concepts}
    results = []
    for run in range(1, runs + 1):
        for concept_id in concepts or list(CONCEPT_SCENES):
            kinds = CONCEPT_SCENES[concept_id]
            for entry_kind, entry in entries_for(by_id[concept_id]):
                record = {'run': run, 'concept': concept_id, 'entry': entry_kind, 'kinds': list(kinds),
                          'outcome': None, 'seconds': None, 'request': None, 'error': None, 'warnings': []}
                handler = _Warnings()
                logging.getLogger().addHandler(handler)
                started = time.perf_counter()
                try:
                    request = gateway.choose_animation(by_id[concept_id], entry, kinds)
                    if request is None:
                        record['outcome'] = 'null'
                    else:
                        record['request'] = request.model_dump()
                        simulate(request)
                        record['outcome'] = 'success'
                except LLMUnavailable as exc:
                    record['outcome'], record['error'] = 'schema_failure', str(exc)
                except ValueError as exc:
                    record['outcome'], record['error'] = 'simulation_failure', str(exc)[:300]
                finally:
                    record['seconds'] = round(time.perf_counter() - started, 3)
                    logging.getLogger().removeHandler(handler)
                    record['warnings'] = handler.messages
                results.append(record)
                if progress:
                    progress(f"{run} {concept_id} {entry_kind}: {record['outcome']} {record['seconds']}s")
    return results


## @brief 결과 목록을 네 분류 개수·전체 성공률·장면 종류별 성공률·평균·최대 지연으로 집계
def summarize(results):
    total = len(results)
    counts = {outcome: sum(1 for r in results if r['outcome'] == outcome) for outcome in OUTCOMES}
    by_kind = {}
    for r in results:
        for kind in r['kinds']:
            row = by_kind.setdefault(kind, {'total': 0, 'success': 0})
            row['total'] += 1
            row['success'] += r['outcome'] == 'success'
    for row in by_kind.values():
        row['success_rate'] = round(row['success'] / row['total'], 3)
    seconds = [r['seconds'] for r in results]
    return {'total': total, 'counts': counts, 'success_rate': round(counts['success'] / total, 3) if total else 0.0,
            'by_kind': by_kind,
            'seconds_mean': round(sum(seconds) / total, 3) if total else 0.0,
            'seconds_max': max(seconds) if seconds else 0.0}


## @brief 명령행 인자를 읽어 측정하고 결과 JSON을 씀
# - --out이 ai-core/outputs/eval/ 밖이거나 이미 있으면 모델을 부르기 전에 종료(결과 원본 보호, Git 유입 방지)
# - 시작 전에 모델 목록을 한 번 조회해 서버가 없으면 종료: 연결 실패를 스키마 실패로 잘못 세지 않기 위함
def main(argv=None):
    parser = argparse.ArgumentParser(description='막힘 보충 장면 선택 성공률 측정')
    parser.add_argument('--base-url', required=True, help='OpenAI 호환 모델 서버 주소(예: http://127.0.0.1:8000/v1)')
    parser.add_argument('--runs', type=int, default=3, help='개념·진입마다 반복 횟수')
    parser.add_argument('--out', required=True, type=Path, help='ai-core/outputs/eval/ 아래 JSON 경로')
    args = parser.parse_args(argv)

    out = args.out.resolve()
    if not out.is_relative_to(EVAL_ROOT):
        parser.error(f'--out은 {EVAL_ROOT} 아래여야 합니다: {out}')
    if out.exists():
        parser.error(f'이미 있는 결과는 덮어쓰지 않습니다: {out}')
    if args.runs < 1:
        parser.error('--runs는 1 이상이어야 합니다.')

    def client():
        return OpenAI(base_url=args.base_url, api_key=os.getenv('VLLM_API_KEY', 'not-needed'), max_retries=0)

    try:
        with client() as probe:
            model = os.getenv('VLLM_MODEL') or probe.models.list(timeout=10).data[0].id
    except (OpenAIError, IndexError) as exc:
        parser.error(f'모델 서버에 연결하지 못했습니다: {exc}')

    concept_map = load_concept_map()
    gateway = StuckModelGateway(concept_map, threading.Lock(), client_factory=client)
    results = measure(gateway, args.runs, concept_map=concept_map, progress=print)
    summary = summarize(results)
    report = {'measured_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'base_url': args.base_url,
              'model': model, 'runs': args.runs, 'summary': summary, 'results': results}
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'x', encoding='utf-8') as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f'{out}: {len(results)}건')


if __name__ == '__main__':
    main()
