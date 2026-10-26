# 파일 역할: 시뮬레이션 학습자 실행기. 프로필마다 로드맵 진입으로 막힘 세션을 공개 API로 끝까지 진행하고 M1·M2를 계산
# - M1(진단 먼저): 진단 결과 화면의 빈틈(gaps)과 프로필의 정답 선수 빈틈을 비교한 recall·precision, 문항 상한 대비 비율
# - M2(최소 보충): 보충 화면에 나온 개념 중 프로필이 이미 아는 개념의 비율(과잉 보충률)
# - 결과 원본은 git-ignored 폴더(ai-core/outputs/eval/sim/)에만 쓰고, PR·발표에는 요약만 옮김
"""Run simulated learners through the stuck flow and compute M1/M2."""
import argparse
import json
import random
import sys
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from statistics import mean, median

AI_CORE = Path(__file__).resolve().parents[2]
# 저장소 루트에서 `python ai-core/eval/sim_learner/run.py`로 실행해도 serving·eval 패키지를 찾게 함
if str(AI_CORE) not in sys.path:
    sys.path.insert(0, str(AI_CORE))

from eval.metrics import recall_precision, wilson_interval  # noqa: E402
from eval.sim_learner.client import StuckClient  # noqa: E402
from eval.sim_learner.learner import Learner, LearnerModel  # noqa: E402
from eval.sim_learner.profiles import (Profile, generate_profiles, recall_ceiling, select_targets,  # noqa: E402
                                       true_gaps)
from serving.concept_map import ConceptMap, load_concept_map  # noqa: E402
from serving.item_bank import Item, load_item_bank  # noqa: E402
from serving.stuck import MAX_DEPTH, Entry, StepInput, UnavailableGateway, step  # noqa: E402

# git-ignored 폴더; 결과 원본은 이 아래에만 씀
EVAL_ROOT = AI_CORE / 'outputs' / 'eval' / 'sim'
# 프로필 생성 seed 기본값: 같은 seed면 같은 프로필 묶음을 다시 만들 수 있음
SEED = 7
# 한 세션 요청 수 상한: 진단 5 + 보충 대상마다 (보충·확인·서술·결과) × 최대 3회 시도를 넉넉히 넘는 값. 엔진이 끝나지 않는 버그를 막는 보호 장치
MAX_STEPS = 150
END_STAGES = ('complete', 'out_of_scope')
# 놓친 빈틈 이유 → 보고용 묶음. 상한 탓(budget)과 찍기 탓(guess: 직접 찍음 + 찍기로 가지치기)을 나눠 엔진 개선 방향을 정하기 위함
# - 여기 없는 이유(answered_correct·no_items·not_probed_pruned)는 other
MISS_CAUSES = {'not_probed_budget': 'budget', 'guessed': 'guess', 'not_probed_guessed': 'guess'}


# 역할: 완벽한 응답자(slip·guess 0)가 같은 프로필로 진단만 받았을 때 엔진이 찾는 빈틈(엔진 오라클)
# - 진단 단계는 LLM을 쓰지 않으므로 실제 엔진을 이 프로세스에서 바로 돌림
# - 시스템 결과가 오라클보다 못하면 학습자 잡음(slip·guess), 오라클이 정답보다 못하면 엔진 규칙(가지치기·문항 상한) 탓으로 나눌 수 있음
def oracle_gaps(profile: Profile, concept_map: ConceptMap, items: list[Item]) -> set[str]:
    perfect = Learner(replace(profile, slip=0.0, guess=0.0), items, random.Random(0), lambda question, reference: '')
    gateway = UnavailableGateway()
    entry = Entry(kind='roadmap', concept=profile.target, return_to='/oracle')
    state = step(None, StepInput(type='start', entry=entry), gateway, concept_map, items, 0.0)
    while state.stage == 'diagnose':
        item = state.view['item']
        answer, _ = perfect.answer(item)
        state = step(state, StepInput(type='answer', item=item['id'], answer=answer), gateway, concept_map, items, 0.0)
    return {gap['id'] for gap in state.view['gaps']}


# 역할: 프로필 하나로 세션을 끝까지 진행하고 기록 한 줄(dict)을 반환. 실패해도 예외를 던지지 않고 error에 남김
# - 입력은 stage로 정함: 문항이 있는 단계(diagnose·check·explain)는 답, 나머지(remediate·decide·return)는 계속
# - 보충 화면을 받은 개념은 학습자가 "알게 됨"으로 바꾼 뒤 그 화면의 확인 문항에 답함
def run_profile(profile: Profile, api, learner: Learner, concept_map: ConceptMap, items: list[Item]) -> dict:
    truth = true_gaps(profile, concept_map) - {profile.target}
    oracle = oracle_gaps(profile, concept_map, items)
    record = {'profile': profile.id, 'target': profile.target, 'consistent': profile.consistent,
              'slip': profile.slip, 'guess': profile.guess, 'truth': sorted(truth), 'ceiling': recall_ceiling(profile, concept_map),
              'oracle_gaps': sorted(oracle), 'oracle_recall': recall_precision(oracle, truth)[0],
              'gaps': None, 'evidence': [], 'remediated': [], 'outcomes': {}, 'answers': [],
              'steps': 0, 'seconds': None, 'stage': None, 'error': None}
    started = time.perf_counter()

    def observe(session):
        record['steps'] += 1
        record['stage'] = session['stage']
        view = session['view']
        if view.get('kind') == 'diagnosis_result':
            record['gaps'] = [gap['id'] for gap in view['gaps']]
            record['evidence'] = [{'concept': e['concept'], 'result': e['result']} for e in view['evidence']]
        elif view.get('kind') == 'remediation':
            concept = view['concept']['id']
            learner.learn(concept)
            if concept not in record['remediated']:
                record['remediated'].append(concept)
        elif view.get('kind') == 'summary':
            record['outcomes'] = {r['concept']['id']: r['outcome'] for r in view['results']}
        return session

    try:
        session = observe(api.start({'kind': 'roadmap', 'concept': profile.target, 'return_to': f'/learn?concept={profile.target}'}))
        while session['stage'] not in END_STAGES:
            if record['steps'] >= MAX_STEPS:
                raise RuntimeError(f'요청 {MAX_STEPS}번 안에 세션이 끝나지 않았습니다.')
            stage, view = session['stage'], session['view']
            if stage in ('diagnose', 'check', 'explain'):
                answer, note = learner.answer(view['item'])
                record['answers'].append({'stage': stage, 'concept': view['concept']['id'], 'item': view['item']['id'], 'note': note})
                data = {'type': 'answer', 'item': view['item']['id'], 'answer': answer}
            else:
                data = {'type': 'continue'}
            session = observe(api.step(session, data))
    except Exception as exc:  # noqa: BLE001 - API·학습자 모델·엔진 오류 모두 이 프로필의 실패로 기록하고 다음 프로필을 계속 진행
        record['error'] = f'{type(exc).__name__}: {exc}'[:300]
    record['seconds'] = round(time.perf_counter() - started, 3)
    record.update(_session_metrics(record, profile, truth, concept_map, items))
    return record


# 역할: 진단 문항 상한이 없을 때 엔진이 같은 판정으로 빈틈을 찾게 되는 개념 집합
# - 엔진(_next_probe·_diagnose)과 같은 규칙: 직접 선수 개념(깊이 1)부터 내려가고, 빈틈 판정이면서 깊이가 MAX_DEPTH 미만일 때만
#   그 개념의 선수 개념으로 더 내려감. 진단 문항이 없는 개념은 판정하지 않고 내려가지도 않음
# - 상한이 없으면 묻는 순서는 찾는 집합을 바꾸지 않음. 너비 우선으로 돌아 개념마다 가장 얕은 깊이로 판단함
# - is_gap(개념): 그 개념을 물었다면 빈틈으로 판정되는지
def _uncapped_gaps(target: str, is_gap, concept_map: ConceptMap, diagnosable: set[str]) -> set[str]:
    prerequisites = {c.id: [p.id for p in c.prerequisites] for c in concept_map.concepts}
    found, seen, frontier = set(), set(), [(p, 1) for p in prerequisites[target]]
    while frontier:
        concept, depth = frontier.pop(0)
        if concept in seen:
            continue
        seen.add(concept)
        if concept not in diagnosable or not is_gap(concept):
            continue
        found.add(concept)
        if depth < MAX_DEPTH:
            frontier += [(p, depth + 1) for p in prerequisites[concept]]
    return found


# 역할: 기록 한 줄의 M1·M2 값과 실패 사례 이유를 계산(진단 결과 화면을 받지 못했으면 M1은 None)
# - 놓친 빈틈 이유
#   - no_items: 진단 문항이 없어 확인하지 못함
#   - answered_correct·guessed: 진단 문항을 맞힘(guessed는 학습자가 찍어서 맞힘)
#   - not_probed_budget: 묻지 않음. 상한이 없었다면 같은 판정으로 찾았음(상한에 걸릴 때 대기열에 남았거나 그보다 더 깊음)
#   - not_probed_guessed: 묻지 않음. 위 개념을 찍어 맞혀 가지치기됨(찍은 문항을 틀렸다면 찾았음)
#   - not_probed_pruned: 묻지 않음. 위 개념을 정말 알아 가지치기됐거나 깊이 상한 밖(엔진 가정과 어긋난 프로필에서 생김)
# - 묻지 않은 개념은 학습자가 지식 상태대로 답한다고 보고 판단함(정답 빈틈이면 빈틈 판정)
#   예전에는 "문항 5개를 다 씀"만 보고 상한 탓으로 셌음. 2026-10-26 기록을 다시 재생하니 찍기 탓이 섞여 있어 나눔
# - 틀린 빈틈 이유: slip(아는 개념을 실수로 틀림) 또는 other
def _session_metrics(record: dict, profile: Profile, truth: set[str], concept_map: ConceptMap,
                     items: list[Item]) -> dict:
    over = [c for c in record['remediated'] if c in profile.known]
    if record['gaps'] is None:
        return {'recall': None, 'precision': None, 'over_remediated': over, 'miss_reasons': {}, 'false_gap_reasons': {}}
    predicted = set(record['gaps'])
    recall, precision = recall_precision(predicted, truth)
    results = {e['concept']: e['result'] for e in record['evidence']}
    notes = {a['concept']: a['note'] for a in record['answers'] if a['stage'] == 'diagnose'}
    guesses = {c for c, result in results.items() if result == 'known' and notes.get(c) == 'guess'}
    diagnosable = {item.concept for item in items if item.purpose == 'diagnosis'}

    # 물은 개념은 기록된 판정(flipped에 든 찍기는 틀렸다고 봄), 묻지 않은 개념은 정답 빈틈 여부로 판정
    def is_gap(flipped):
        return lambda c: (results[c] == 'gap' or c in flipped) if c in results else c in truth

    reachable = _uncapped_gaps(record['target'], is_gap(set()), concept_map, diagnosable)
    without_guesses = _uncapped_gaps(record['target'], is_gap(guesses), concept_map, diagnosable)
    misses = Counter()
    for concept in truth - predicted:
        result = results.get(concept)
        if result == 'no_items':
            misses['no_items'] += 1
        elif result is not None:
            misses['guessed' if notes.get(concept) == 'guess' else 'answered_correct'] += 1
        elif concept in reachable:
            misses['not_probed_budget'] += 1
        elif concept in without_guesses:
            misses['not_probed_guessed'] += 1
        else:
            misses['not_probed_pruned'] += 1
    false_gaps = Counter('slip' if notes.get(c) == 'slip' else 'other' for c in predicted - truth)
    return {'recall': recall, 'precision': precision, 'over_remediated': over,
            'miss_reasons': dict(misses), 'false_gap_reasons': dict(false_gaps)}


# 역할: 기록 목록을 기본 프로필(consistent)과 어긋난 프로필(inconsistent) 묶음으로 나눠 M1·M2를 집계
# - 어긋난 묶음은 엔진 가정을 일부러 어긴 것이라 M1 목표(≥0.8) 판정에 섞지 않음(Review Focus 1)
def summarize(results: list[dict]) -> dict:
    return {'consistent': _group([r for r in results if r.get('consistent', True)]),
            'inconsistent': _group([r for r in results if not r.get('consistent', True)])}


# 역할: 한 묶음의 집계
# - M1: 세션별 recall·precision 평균과 분포, 문항 상한(ceiling) 대비 비율(Review Focus 2), 엔진 오라클 recall
# - M2: 과잉 보충률 = 모든 세션의 (이미 아는데 보충한 개념 수) / (보충한 개념 수). 대상 개념 포함·제외 두 값과 Wilson 95% 구간
# - 놓친 빈틈: 이유별 개수(miss_reasons)와 상한·찍기·나머지 묶음(miss_causes, MISS_CAUSES)
def _group(records: list[dict]) -> dict:
    measured = [r for r in records if r.get('recall') is not None]
    remediated = [r for r in measured if r.get('remediated')]
    over = sum(len(r['over_remediated']) for r in remediated)
    total = sum(len(r['remediated']) for r in remediated)
    prereq_over = sum(len([c for c in r['over_remediated'] if c != r['target']]) for r in remediated)
    prereq_total = sum(len([c for c in r['remediated'] if c != r['target']]) for r in remediated)
    recalls = [r['recall'] for r in measured]
    misses = sum((Counter(r.get('miss_reasons', {})) for r in records), Counter())
    summary = {
        'sessions': len(records),
        'completed': sum(1 for r in records if r.get('stage') == 'complete' and r.get('error') is None),
        'failed': sum(1 for r in records if r.get('error') is not None),
        'failures': [{'profile': r.get('profile'), 'stage': r.get('stage'), 'error': r['error']} for r in records if r.get('error')],
        'm1': None, 'm2': None,
        'miss_reasons': dict(misses),
        'miss_causes': {cause: sum(n for reason, n in misses.items() if MISS_CAUSES.get(reason, 'other') == cause)
                        for cause in ('budget', 'guess', 'other')},
        'false_gap_reasons': dict(sum((Counter(r.get('false_gap_reasons', {})) for r in records), Counter())),
    }
    if measured:
        summary['m1'] = {
            'measured': len(measured),
            'recall_mean': round(mean(recalls), 4),
            'precision_mean': round(mean(r['precision'] for r in measured), 4),
            'recall_min_median_max': [round(min(recalls), 4), round(median(recalls), 4), round(max(recalls), 4)],
            'recall_perfect_sessions': sum(1 for x in recalls if x == 1.0),
            'ceiling_mean': round(mean(r['ceiling'] for r in measured), 4),
            'recall_over_ceiling_mean': round(mean(r['recall'] / r['ceiling'] for r in measured), 4),
            'sessions_below_ceiling': sum(1 for r in measured if r['recall'] < r['ceiling'] - 1e-9),
            'oracle_recall_mean': round(mean(r['oracle_recall'] for r in measured), 4),
        }
    if total:
        low, high = wilson_interval(over, total)
        summary['m2'] = {
            'remediated_concepts': total, 'over_remediated': over,
            'over_remediation_rate': round(over / total, 4), 'over_remediation_ci95': [round(low, 4), round(high, 4)],
            'prerequisite_over_remediation_rate': round(prereq_over / prereq_total, 4) if prereq_total else None,
        }
    finished = [r for r in records if r.get('seconds') is not None and r.get('error') is None]
    if finished:
        summary['seconds_mean'] = round(mean(r['seconds'] for r in finished), 3)
        summary['steps_mean'] = round(mean(r['steps'] for r in finished), 2)
    return summary


# 역할: 명령행 인자를 읽어 실행하고 기록(JSONL)·요약(JSON)을 씀
# - --out이 ai-core/outputs/eval/sim/ 밖이거나 이미 있으면 API·모델을 부르기 전에 종료(결과 원본 보호, Git 유입 방지)
# - 기록은 세션이 끝날 때마다 한 줄씩 써서 중간에 멈춰도 끝난 세션은 남음
def main(argv=None):
    parser = argparse.ArgumentParser(description='시뮬레이션 학습자로 M1·M2 측정')
    parser.add_argument('--base-url', required=True, help='Spring 주소(예: http://127.0.0.1:21101)')
    parser.add_argument('--learner-url', required=True, help='학습자 모델 OpenAI 호환 주소(예: http://127.0.0.1:20900/v1)')
    parser.add_argument('--learner-model', help='학습자 모델 이름(없으면 서버의 첫 모델)')
    parser.add_argument('--out', required=True, type=Path, help='ai-core/outputs/eval/sim/ 아래 JSONL 경로')
    parser.add_argument('--per-target', type=int, default=5, help='대상마다 기본 프로필 수(3~5)')
    parser.add_argument('--seed', type=int, default=SEED)
    parser.add_argument('--profiles', type=int, help='앞에서부터 이 수만큼만 실행(시험용)')
    args = parser.parse_args(argv)

    out = args.out.resolve()
    if not out.is_relative_to(EVAL_ROOT):
        parser.error(f'--out은 {EVAL_ROOT} 아래여야 합니다: {out}')
    if out.exists():
        parser.error(f'이미 있는 결과는 덮어쓰지 않습니다: {out}')

    concept_map, items = load_concept_map(), load_item_bank()
    profiles = generate_profiles(concept_map, select_targets(concept_map), args.per_target, args.seed)[:args.profiles]
    try:
        explainer = LearnerModel(args.learner_url, args.learner_model)
    except Exception as exc:  # noqa: BLE001 - 연결 실패를 세션 실패로 잘못 세지 않도록 시작 전에 종료
        parser.error(f'학습자 모델 서버에 연결하지 못했습니다: {exc}')
    api = StuckClient(args.base_url)

    out.parent.mkdir(parents=True, exist_ok=True)
    results = []
    with open(out, 'x', encoding='utf-8') as file:
        for n, profile in enumerate(profiles, start=1):
            learner = Learner(profile, items, random.Random(f'{args.seed}:{profile.id}'), explainer)
            record = run_profile(profile, api, learner, concept_map, items)
            results.append(record)
            file.write(json.dumps(record, ensure_ascii=False) + '\n')
            file.flush()
            print(f"{n}/{len(profiles)} {profile.id}: {record['stage']} recall={record['recall']} "
                  f"over={record['over_remediated']} {record['seconds']}s {record['error'] or ''}", flush=True)
    summary = {'seed': args.seed, 'per_target': args.per_target, 'learner_model': explainer.model, **summarize(results)}
    out.with_suffix('.summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
