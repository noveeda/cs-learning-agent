## @file
# @brief 외부 검사 모델(Codex CLI의 gpt-6.1-sol·gpt-6-astra)로 "문장이 교재 문단과 맞는지"를 다시 판정하는 독립 검사(미세조정 계획 Task 4)
# - 노출 지표를 26B 판정기 하나로만 재면, 26B가 만든 학습 데이터로 배운 12B와 같은 쪽으로 틀릴 수 있어 다른 계열 모델로도 잼
# - 측정 검사에만 씀. 검사 결과는 학습 데이터에 넣지 않음(설계 2026-10-28 원칙)
# - 보내는 내용은 지시문과 공개 교재 문단·모델 문장뿐. codex는 빈 임시 폴더에서 읽기 전용으로 실행해 다른 파일을 보지 못하게 함
#
# 보정 시험(검사 모델 고르기, ai-core 폴더에서)
#   python -m eval.astra_audit calibrate --model gpt-6.1-sol --out outputs/finetune/astra-calibration
import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
from functools import partial
from pathlib import Path

AI_CORE = Path(__file__).resolve().parents[1]
if str(AI_CORE) not in sys.path:
    sys.path.insert(0, str(AI_CORE))

# 기본 검사 모델: 보정 시험(판정 세트 100행)에서 sol이 검출률·오경보율 모두 astra와 3%p 이내라 sol을 씀(docs/finetune.md 6절)
# - 이름은 계획 문구(astra 독립 검사)를 따라 astra_audit로 두고, 모델은 --model·--astra-model로 바꿀 수 있음
DEFAULT_MODEL = 'gpt-6.1-sol'
# codex 한 번 호출의 제한 시간(초): 40문장 배치 응답을 기다리되, 멈춘 호출이 측정 전체를 붙잡지 않게 함
TIMEOUT_S = 900
SETS_DIR = Path(os.environ.get('HARNESS_SETS_DIR') or AI_CORE / 'outputs' / 'harness-sets')

SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['results'],
          'properties': {'results': {'type': 'array', 'items': {
              'type': 'object', 'additionalProperties': False, 'required': ['id', 'supported'],
              'properties': {'id': {'type': 'integer'}, 'supported': {'type': 'boolean'}}}}}}

INSTRUCTION = (
    '너는 교재 근거 검사기다. 아래 항목마다 한국어 문장(sentence)이 영어 교재 문단(passage)으로 뒷받침되는지 판정한다.\n'
    '- 문장의 모든 주장이 문단에 직접 나오거나 문단에서 바로 따라 나오면 supported=true.\n'
    '- 문단에 없는 내용이 덧붙었거나, 숫자·부정·개념이 문단과 다르면 supported=false.\n'
    '- 파일을 읽거나 명령을 실행하지 말고, 아래 내용만 보고 판정한다.\n'
    '- 항목마다 {"id": 항목 번호, "supported": true|false}를 results에 넣어 출력 스키마대로 응답한다.\n')


## @brief 검사 지시문 + 항목 목록(번호, 문장, 문단)으로 프롬프트를 만듦. items의 다른 필드는 보내지 않음
def build_prompt(items: list[dict]) -> str:
    body = [{'id': n, 'sentence': item['sentence'], 'passage': item['passage']} for n, item in enumerate(items)]
    return INSTRUCTION + '\n' + json.dumps(body, ensure_ascii=False, indent=1)


## @brief codex exec를 subprocess로 불러 출력 스키마에 맞는 마지막 응답(JSON)을 돌려줌
# - 빈 임시 폴더를 작업 폴더(-C)로 두고 읽기 전용 샌드박스·세션 미저장·사용자 설정 무시로 실행
#   - 저장소·비밀정보 파일이 작업 폴더에 없게 하고, 사용자 설정의 MCP·훅이 끼어들지 않게 함
# - 프롬프트는 stdin으로 넘김(명령줄 길이 제한과 프로세스 목록 노출을 피함)
# - 실패(종료 코드, 시간 초과, JSON 아님)는 예외로 올려 audit가 그 배치를 None으로 처리하게 함
def codex_runner(prompt: str, schema: dict, model: str = DEFAULT_MODEL) -> dict:
    with tempfile.TemporaryDirectory(prefix='astra-audit-') as tmp:
        work = Path(tmp) / 'work'
        work.mkdir()
        schema_path, out_path = Path(tmp) / 'schema.json', Path(tmp) / 'last.json'
        schema_path.write_text(json.dumps(schema), encoding='utf-8')
        done = subprocess.run(
            ['codex', 'exec', '-m', model, '-s', 'read-only', '-C', str(work), '--skip-git-repo-check', '--ephemeral',
             '--ignore-user-config', '--color', 'never', '--output-schema', str(schema_path), '-o', str(out_path), '-'],
            input=prompt, capture_output=True, text=True, timeout=TIMEOUT_S, cwd=work)
        if done.returncode != 0:
            raise RuntimeError(f'codex exec 실패({done.returncode}): {done.stderr.strip()[-500:]}')
        return json.loads(out_path.read_text(encoding='utf-8'))


## @brief 항목({sentence, passage})을 batch개씩 runner로 검사해 항목 순서대로 결과를 돌려줌
# - True=맞음, False=불일치, None=검사 실패(그 배치 호출이 실패했거나 응답에 그 번호가 없음)
# - None은 불일치로 세지 않음: 검사기 실패를 모델 실수로 세면 노출 지표가 부풀기 때문(exposure가 따로 셈)
def audit(items: list[dict], runner=codex_runner, batch: int = 40) -> list[bool | None]:
    results = []
    for start in range(0, len(items), batch):
        chunk = items[start:start + batch]
        try:
            answer = runner(build_prompt(chunk), SCHEMA)
            verdicts = {r['id']: r['supported'] for r in answer['results'] if isinstance(r.get('supported'), bool)}
        except Exception as exc:  # 한 배치 실패로 측정 전체를 멈추지 않고 그 배치만 None으로 남김
            print(f'검사 실패({start}~{start + len(chunk) - 1}): {exc}', file=sys.stderr)
            verdicts = {}
        results += [verdicts.get(n) for n in range(len(chunk))]
    return results


## @brief 검사 결과로 노출 지표: 불일치 비율(검사 실패 None은 분모·분자에서 빼고 errors로 셈)
def exposure(results: list[bool | None]) -> dict:
    audited = [r for r in results if r is not None]
    mismatch = sum(not r for r in audited)
    return {'audited': len(audited), 'mismatch': mismatch, 'errors': len(results) - len(audited),
            'rate': round(mismatch / len(audited), 4) if audited else None}


## @brief 보정 시험 표본: 판정 세트에서 맞음·틀림 반반으로 n행(seed 고정)
def calibration_sample(rows: list[dict], n: int = 100, seed: int = 0) -> list[dict]:
    rng = random.Random(seed)
    picked = []
    for label in ('supported', 'unsupported'):
        picked += rng.sample([r for r in rows if r['label'] == label], n // 2)
    return sorted(picked, key=lambda r: r['id'])


## @brief 보정 시험 지표: 틀린 문장 검출률(틀림 중 False)·오경보율(맞음 중 False). None은 빼고 errors로 셈
def calibration_metrics(rows: list[dict], results: list[bool | None]) -> dict:
    pairs = [(r['label'], v) for r, v in zip(rows, results) if v is not None]
    wrong = [v for label, v in pairs if label == 'unsupported']
    right = [v for label, v in pairs if label == 'supported']
    rate = lambda k, n: round(k / n, 4) if n else None  # noqa: E731
    return {'n': len(rows), 'errors': len(rows) - len(pairs),
            'detection': rate(sum(not v for v in wrong), len(wrong)),
            'false_alarm': rate(sum(not v for v in right), len(right))}


## @brief 명령 진입점: calibrate — 판정 세트 표본을 검사 모델 하나로 판정해 <out>/<모델>.jsonl·.metrics.json에 씀
def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m eval.astra_audit', description='외부 검사 모델 독립 검사')
    commands = parser.add_subparsers(dest='command', required=True)
    calibrate = commands.add_parser('calibrate', help='판정 세트 표본으로 검사 모델의 검출률·오경보율을 잼')
    calibrate.add_argument('--model', required=True)
    calibrate.add_argument('--n', type=int, default=100)
    calibrate.add_argument('--seed', type=int, default=0)
    calibrate.add_argument('--batch', type=int, default=40)
    calibrate.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)

    rows = [json.loads(line) for line in (SETS_DIR / 'judge.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    sample = calibration_sample(rows, args.n, args.seed)
    results = audit(sample, runner=partial(codex_runner, model=args.model), batch=args.batch)
    args.out.mkdir(parents=True, exist_ok=True)
    with open(args.out / f'{args.model}.jsonl', 'w', encoding='utf-8') as file:
        for row, verdict in zip(sample, results):
            file.write(json.dumps({'id': row['id'], 'label': row['label'], 'perturbation': row['perturbation'],
                                   'verdict': verdict}, ensure_ascii=False) + '\n')
    result = {'model': args.model, 'seed': args.seed, 'batch': args.batch, **calibration_metrics(sample, results)}
    (args.out / f'{args.model}.metrics.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
