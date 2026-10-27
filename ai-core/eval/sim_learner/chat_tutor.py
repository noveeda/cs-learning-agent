# 파일 역할: 같은 지식 프로필로 엔진(E)·Claude 기본(C1)·튜터 지시(C2)를 비교하는 오프라인 측정기
# - 기존 프로필·학습자 잡음·엔진 실행·M1/M2·Wilson 정의를 재사용. 결과·대화는 Git 밖에만 저장
# - C의 해결은 truth 선수 빈틈과 target을 모두 설명받음. 사람 이해도·설명 정확성을 뜻하지 않음
"""Paired engine/Claude tutoring comparison with simulated learners (no new dependencies)."""
import argparse
import json
import random
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean

AI_CORE = Path(__file__).resolve().parents[2]
if str(AI_CORE) not in sys.path:
    sys.path.insert(0, str(AI_CORE))

from eval.metrics import recall_precision, wilson_interval  # noqa: E402
from eval.sim_learner.client import StuckClient  # noqa: E402
from eval.sim_learner.learner import Learner, LearnerModel  # noqa: E402
from eval.sim_learner.profiles import generate_profiles, prerequisite_depths, select_targets, true_gaps  # noqa: E402
from eval.sim_learner.run import SEED, oracle_gaps, run_profile, summarize  # noqa: E402
from serving.concept_map import load_concept_map  # noqa: E402
from serving.item_bank import load_item_bank  # noqa: E402

RESULTS_ROOT = Path('/home/infoserver/cs-eval-data/results/claude-baseline')
CLAUDE_MODEL = 'claude-opus-5-5'
SYSTEM_PROMPTS = {
    'C1': 'You are Claude, an AI assistant made by Anthropic.',
    'C2': ('You are Claude, an AI assistant made by Anthropic.\n'
           '학생이 막힌 원인이 되는 선수 개념을 먼저 짧은 질문으로 확인하고, '
           '빈 개념만 짧게 보충한 뒤 원래 내용으로 돌아가라.'),
}
# 비교의 교육적 대화 예산. 초기 요청 포함 Claude에 보내는 학습자 메시지 10개, 마지막 이해 선언은 호출하지 않음
MAX_TURNS = 10
SELECTION_RULE = ('seed=7, 과목별 select_targets(count=10)·generate_profiles(per_target=5)를 재사용. '
                  'consistent이며 target을 뺀 true_gaps가 있는 후보만 사용. 과목별 seed:subject:chat-selection으로 '
                  '후보를 섞은 뒤 target 순환으로 고르게 뽑아 각 10명. os는 과목 경계 truth 후보를 먼저 순환하고 '
                  '10명을 채우지 못한 경우 나머지 후보를 사용.')


# 역할: 튜터 한 응답의 질문·실질 설명 개념. choices는 실제 선택지를 준 질문이라 guess를 허용하는 개념
@dataclass
class Judgment:
    asked: list[str]
    explained: list[str]
    choices: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    choice_corrections: int = 0


class ClaudeBudgetError(RuntimeError):
    """Stop before the next paid CLI call after reaching the shared batch budget."""


class ClaudeCallError(RuntimeError):
    # 역할: 실패한 호출도 이미 발생한 비용·토큰·원문을 상위 기록기에 전달
    def __init__(self, message, reply=None):
        super().__init__(message)
        self.reply = reply


class ClaudeLimitError(ClaudeCallError):
    """Subscription exhaustion: record the session and stop the entire batch without retrying."""


# 역할: 기존 생성 규칙에서 선수 빈틈이 있는 일관 프로필을 과목별 10명씩 결정적으로 선택
# - target을 먼저 순환해 특정 개념 하나가 전체 표본을 차지하는 것을 막음. os는 경계 빈틈을 최대한 포함
# - seed를 바꿔 실행하면 manifest에 실제 seed가 남음(위 SELECTION_RULE의 7은 기본값)
def select_profiles(concept_map, seed=SEED):
    subjects = {c.id: c.subject for c in concept_map.concepts}
    selected = []
    for subject in ('dsa', 'os'):
        targets = select_targets(concept_map, subject=subject)
        candidates = [p for p in generate_profiles(concept_map, targets, 5, seed)
                      if p.consistent and true_gaps(p, concept_map) - {p.target}]
        random.Random(f'{seed}:{subject}:chat-selection').shuffle(candidates)
        crossing = lambda p: any(subjects[c] != subject for c in true_gaps(p, concept_map) - {p.target})
        groups = ([p for p in candidates if crossing(p)], [p for p in candidates if not crossing(p)]) if subject == 'os' else (candidates,)
        chosen = []
        for group in groups:
            buckets = {target: [p for p in group if p.target == target] for target in targets}
            while len(chosen) < 10 and any(buckets.values()):
                for target in targets:
                    if buckets[target] and len(chosen) < 10:
                        chosen.append(buckets[target].pop(0))
        if len(chosen) != 10:
            raise ValueError(f'{subject}: 적합한 프로필이 10개 미만입니다.')
        selected.extend(chosen)
    return selected


# 역할: /learn?concept 화면의 초기 학습 본문(ChatSession.tsx의 제목·description). 선수 관계·정답은 노출하지 않음
def learning_screen(concept):
    return f'{concept.title}\n{concept.description}'


def initial_message(concept):
    return f'다음 글을 읽다가 막혔어요.\n\n{learning_screen(concept)}\n\n여기서 막혔어요. 이해가 안 돼요.'


# 역할: 질문이 없는 턴에는 정답을 지어내지 않고, 튜터 본문에서 처음 나온 미학습 이름을 되물음
# - 이름은 지도 title·괄호 앞 title·영문 ID의 공백 표기만 사용. 영어는 단어 경계·대소문자 무시
# - 같은 위치에서는 긴 이름 우선(우선순위 큐를 큐로 잘못 묻는 것 방지). 새로 배운 개념도 제외
# - 알려 주지 않은 truth를 직접 읽지 않아 Claude에게 숨은 빈틈 목록을 누설하지 않음
def clarification(text, known, concept_map):
    matches = []
    for concept in concept_map.concepts:
        if concept.id in known:
            continue
        names = {concept.title, concept.title.split('(')[0].strip(), concept.id.replace('-', ' ')}
        for name in names:
            pattern = re.escape(name)
            if name.isascii():
                pattern = r'(?<![a-zA-Z])' + pattern + r'(?![a-zA-Z])'
            found = re.search(pattern, text, re.I)
            if found:
                matches.append((found.start(), -len(name), concept.id, found.group()))
    if not matches:
        return '아직 잘 모르겠어요.', None
    _, _, cid, name = min(matches)
    final = ord(name[-1])
    particle = '이' if 0xAC00 <= final <= 0xD7A3 and (final - 0xAC00) % 28 else '가'
    return f'{name}{particle} 뭔지 잘 모르겠어요.', cid


# 역할: 비교 세션 공통 기록. truth는 기존 M1과 같이 target 제외, resolved에만 target을 다시 포함
# - initial_known은 보충 직전 상태가 아닌 프로필의 초기 지식: 기존 M2와 같은 과잉 보충 정의
def _record(profile, condition, concept_map, items):
    by_id = {c.id: c for c in concept_map.concepts}
    truth = true_gaps(profile, concept_map) - {profile.target}
    return {'condition': condition, 'profile': profile.id, 'target': profile.target,
            'subject': by_id[profile.target].subject, 'truth': sorted(truth), 'initial_known': sorted(profile.known),
            'cross_subject': sorted(c for c in prerequisite_depths(concept_map, profile.target)
                                    if by_id[c].subject != by_id[profile.target].subject),
            'oracle_gaps': sorted(oracle_gaps(profile, concept_map, items)), 'slip': profile.slip, 'guess': profile.guess,
            'turns': [], 'explained': [], 'explained_after_probe': [], 'diagnostic_questions': 0,
            'choice_corrections': 0, 'clarification_rules': {'unknown_term': 0, 'repeat_confusion': 0}, 'learner_turns': 0, 'resolved': False, 'closure': None, 'error': None, 'rate_limited': False, 'budget_exhausted': False,
            'initial_screen_chars': len(learning_screen(by_id[profile.target])), 'tutor_chars': 0,
            'reading_chars': len(learning_screen(by_id[profile.target])), 'usage': {}, 'total_cost_usd': 0.0}


# 역할: 성공·실패한 유료 응답의 원문·비용·토큰을 한 번 기록. 중첩 usage는 원본에 보존
# - 한도·CLI 오류도 사용량이 있을 수 있으므로 판정이나 오류 분류보다 먼저 합산
# - 학습자 턴 수는 실제 응답을 받은 요청 수. 호출 전에 막힌 예산 오류는 세지 않음
def record_reply(record, pending, reply):
    row = {'learner': pending, 'tutor': reply['text'], 'usage': reply.get('usage', {}),
           'total_cost_usd': reply.get('total_cost_usd', 0), 'session_id': reply.get('session_id'),
           'model_usage': reply.get('model_usage', {})}
    record['turns'].append(row)
    record['learner_turns'] = len(record['turns'])
    record['tutor_chars'] += len(reply['text'])
    record['total_cost_usd'] += row['total_cost_usd']
    tokens = {k: v for k, v in row['usage'].items() if k.endswith('_tokens') and isinstance(v, (int, float))}
    record['usage'] = dict(Counter(record['usage']) + Counter(tokens))
    return row


# 역할: 가짜 또는 실제 tutor·judge·responder로 C 조건을 실행. 유료 응답은 판정보다 먼저 보관
# - 판정은 질문과 설명을 각각 ID 집합으로 기록. 설명 뒤 learn()을 먼저 반영하고 질문에 답함
# - 같은 응답에서 이미 답을 설명한 개념의 질문은 진단 수에서 제외(엄격한 사전 확인 정의)
def run_chat(profile, condition, tutor, judge, responder, concept_map, items, seed=SEED):
    record = _record(profile, condition, concept_map, items)
    learner = Learner(profile, items, random.Random(f'{seed}:{profile.id}'), lambda q, r: '')
    concept = next(c for c in concept_map.concepts if c.id == profile.target)
    pending = initial_message(concept)
    explained, probed, after_probe = set(), set(), set()
    started = time.perf_counter()
    try:
        for turn in range(1, MAX_TURNS + 1):
            reply = tutor.reply(pending)
            row = record_reply(record, pending, reply)
            judgment = judge(reply['text'], pending)
            row['judgment'] = asdict(judgment)
            record['choice_corrections'] += judgment.choice_corrections
            current = set(judgment.explained)
            questions = set(judgment.asked) - explained - current
            record['diagnostic_questions'] += len(questions)
            after_probe |= current & probed
            probed |= questions
            explained |= current
            for cid in current:
                learner.learn(cid)
            record['explained'] = sorted(explained)
            record['explained_after_probe'] = sorted(after_probe)
            record['resolved'] = (set(record['truth']) | {profile.target}) <= explained
            if record['resolved']:
                record['closure'] = '이제 이해했어요'
                break
            if turn < MAX_TURNS:
                states = {cid: learner.answer_state(cid, multiple_choice=cid in judgment.choices)
                          for cid in judgment.asked}
                row['answer_states'] = states
                if states:
                    pending = responder(reply['text'], states)
                else:
                    pending, cid = clarification(reply['text'], learner.known, concept_map)
                    row['clarification_concept'] = cid
                    rule = 'unknown_term' if cid else 'repeat_confusion'
                    record['clarification_rules'][rule] += 1
                if not pending.strip():
                    raise ValueError('빈 학습자 답')
    except Exception as exc:  # noqa: BLE001 - 원문·부분 비용을 보존하고 batch가 오류 종류에 따라 멈추게 함
        if getattr(exc, 'reply', None):
            record_reply(record, pending, exc.reply)['call_error'] = str(exc)[:400]
        record['error'] = f'{type(exc).__name__}: {exc}'[:500]
        record['rate_limited'] = isinstance(exc, ClaudeLimitError)
        record['budget_exhausted'] = isinstance(exc, ClaudeBudgetError)
    record['reading_chars'] += record['tutor_chars']
    record['seconds'] = round(time.perf_counter() - started, 3)
    record['recall'], record['precision'] = recall_precision(set(record['explained']), set(record['truth']))
    return record


# 역할: 구독 CLI만 사용해 하나의 Claude 세션을 이어 감. 도구·설정·훅·MCP를 차단하고 scratch에서 실행
class ClaudeTutor:
    def __init__(self, condition, scratch, budget=None):
        self.condition, self.scratch, self.budget = condition, scratch, budget
        self.session_id = None
        self.created_ids = {str(uuid.uuid4())}
        scratch.mkdir(parents=True, exist_ok=True)
        if any(scratch.iterdir()):
            raise ValueError('Claude cwd는 빈 scratch 폴더여야 합니다.')

    def reply(self, message):
        if self.budget is not None and self.budget['spent'] >= self.budget['limit']:
            raise ClaudeBudgetError('배치 Claude 비용 예산에 도달했습니다.')
        command = ['claude', '-p', '--model', CLAUDE_MODEL, '--system-prompt', SYSTEM_PROMPTS[self.condition],
                   '--tools', '', '--setting-sources', '', '--strict-mcp-config', '--settings', '{"disableAllHooks":true}',
                   '--output-format', 'json']
        command += ['--resume', self.session_id] if self.session_id else ['--session-id', next(iter(self.created_ids))]
        # stdin으로 프롬프트를 전달해 쉘 인용·프로세스 인자 길이 문제를 피함. 구독 오류 재시도 없음
        result = subprocess.run(command, input=message, text=True, capture_output=True, cwd=self.scratch, timeout=360)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            payload = {'result': result.stderr or result.stdout, 'is_error': True}
        if self.budget is not None:
            self.budget['spent'] += payload.get('total_cost_usd', 0)
        text = payload.get('result', '')
        reply = {'text': text, 'usage': payload.get('usage', {}), 'total_cost_usd': payload.get('total_cost_usd', 0),
                 'session_id': payload.get('session_id'), 'model_usage': payload.get('modelUsage', {})}
        if re.search(r'(hit your limit|usage limit|rate.?limit|limit reached|quota.{0,30}exceed|out of extra usage)', text, re.I):
            raise ClaudeLimitError(text[:400], reply)
        if result.returncode or payload.get('is_error') or not text.strip():
            raise ClaudeCallError(f'Claude CLI: {text[:400]}', reply)
        self.session_id = payload['session_id']
        self.created_ids.add(self.session_id)
        return reply

    # 역할: 이 객체가 만든 UUID의 Claude 세션 파일만 삭제. 구독 인증·다른 대화 파일은 건드리지 않음
    def close(self):
        root = Path.home() / '.claude' / 'projects'
        removed = []
        for sid in self.created_ids:
            uuid.UUID(sid)
            if root.exists():
                for path in root.glob(f'*/{sid}.jsonl'):
                    path.unlink()
                    removed.append(str(path))
                for path in root.glob(f'*/{sid}'):
                    if path.is_dir():
                        shutil.rmtree(path)
                        removed.append(str(path))
        self.scratch.rmdir()
        return removed


# 역할: 판정의 개념 ID·행 번호를 검증하고 실제 원문을 근거에 붙임(복사·이스케이프 오류 방지)
# - choice인데 A/B·가/나·동그라미 번호처럼 실제 선택지가 둘 이상 없으면 open으로 보정
# - 번호가 붙은 질문 목록을 선택형 보기로 착각하지 않게 숫자 목록만으로 choice를 허용하지 않음
# - 보정 횟수는 응답·세션별로 보존. 이 규칙은 코디네이터가 첫 파일럿 오류 뒤 승인
def parse_judgment(payload, text, concept_map):
    valid, lines = {c.id for c in concept_map.concepts}, text.splitlines()
    labels = re.findall(r'^\s*(?:[-*]\s*)?\(?([A-Da-d가나다라])[).:：]\s*\S', text, re.M)
    circled = set(re.findall(r'[①②③④⑤]', text))
    has_choices = len(set(labels)) >= 2 or len(circled) >= 2
    evidence, corrected = {}, 0
    for key in ('questions', 'explanations'):
        if not isinstance(payload.get(key), list):
            raise ValueError(f'{key}: 목록 필요')
        evidence[key] = []
        for raw in payload[key]:
            row = dict(raw)
            line = row.get('line')
            if row.get('concept') not in valid or type(line) is not int or not 1 <= line <= len(lines):
                raise ValueError(f'{key}: 유효한 ID와 행 번호 필요: {row}')
            row['source'] = lines[line - 1]
            if key == 'questions' and row.get('format') == 'choice' and not has_choices:
                row['format'] = 'open'
                corrected += 1
            evidence[key].append(row)
    return Judgment(sorted({r['concept'] for r in evidence['questions']}),
                    sorted({r['concept'] for r in evidence['explanations']}),
                    sorted({r['concept'] for r in evidence['questions'] if r.get('format') == 'choice'}),
                    evidence, corrected)


# 역할: Qwen 학습자 문장 생성과 JSON 판정을 분리. 기본 Qwen, 파일럿 오류 시 승인된 대체 판정기 사용
# - 튜터에게 profile·지도는 전달하지 않음. 판정기에 학습자 정답 빈틈을 전달하지 않음
class QwenRoles:
    def __init__(self, url, model, concept_map, items, judge_url=None, judge_model=None):
        self.model = LearnerModel(url, model)
        self.judge_model = LearnerModel(judge_url, judge_model) if judge_url else self.model
        self.concept_map = concept_map
        self.by_id = {c.id: c for c in concept_map.concepts}
        self.references = {i.concept: i.rationale for i in items if i.format == 'explanation'}
        self.taxonomy = '\n'.join(f'{c.id}: {c.title} — {c.description}' for c in concept_map.concepts)
        concept = {'type': 'string', 'enum': list(self.by_id)}
        line_number = {'type': 'integer', 'minimum': 1}
        def rows(question):
            properties = {'concept': concept, 'line': line_number}
            if question:
                properties['format'] = {'type': 'string', 'enum': ['open', 'choice']}
            return {'type': 'array', 'items': {'type': 'object', 'properties': properties,
                                              'required': list(properties), 'additionalProperties': False}}
        self.schema = {'type': 'object', 'properties': {'questions': rows(True), 'explanations': rows(False)},
                       'required': ['questions', 'explanations'], 'additionalProperties': False}

    def _complete(self, system, user, schema=None):
        extra = {'response_format': {'type': 'json_schema', 'json_schema': {'name': 'concept_judgment', 'strict': True,
                                                                         'schema': schema}}} if schema else {}
        selected = self.judge_model if schema else self.model
        response = selected.client.chat.completions.create(
            model=selected.model, temperature=0.0, max_tokens=2400 if schema else 300,
            presence_penalty=0.0, messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            extra_body={'chat_template_kwargs': {'enable_thinking': False}}, **extra)
        if response.choices[0].finish_reason == 'length':
            raise ValueError('Qwen 응답 길이 초과')
        return (response.choices[0].message.content or '').strip()

    def judge(self, text, previous):
        system = ('너는 CS 튜터 발화의 개념 분류 판정기다. JSON만 출력한다. '
                  'questions에는 학생 지식을 확인하려는 실제 질문의 개념만 넣는다. 이해됐냐·어디가 어렵냐·'
                  '계속할까 같은 진행 질문은 제외한다. A/B처럼 답의 선택지를 제시한 경우만 format=choice다. '
                  '질문 여러 개를 1,2,3으로 나열한 목록은 선택형이 아니라 각각 open이다. '
                  'explanations에는 원리·동작·이유·예시를 실제 가르친 개념만 넣는다. 단순 이름 언급·목차·'
                  '앞으로 설명하겠다는 약속·질문 속 용어는 설명이 아니다. 선수 개념을 암묵적으로 알아야 한다는 '
                  '이유로 그 개념도 설명했다고 추론하지 마라. 하나의 발화는 여러 개념을 설명할 수 있다. '
                  '질문만 한 문장은 explanations에 넣지 않는다. line에는 해당 근거가 있는 행 번호를 쓴다. '
                  '각 개념은 목록마다 한 번만, 가장 분명한 근거 행 하나로 적는다. '
                  '프로필의 정답 빈틈은 알 수 없다.\n'
                  'ID 경계: graph-representation은 인접 행렬·인접 리스트 같은 저장 표현이다. '
                  '가중치·무방향의 뜻은 graph다. scheduling-metrics는 대기·반환·응답 시간의 뜻이나 계산이다. '
                  '스케줄링이 왜 필요한지만 물으면 scheduling-metrics가 아니다. '
                  '기아라는 말이 어려운지 묻는 진행 질문은 지식을 실제 답하게 하는 질문이 아니다. '
                  '정렬한다는 말만으로 merge-sort·quick-sort·heap-sort를 설명한 것이 아니다. '
                  'merge-sort는 반으로 나눠 정렬된 부분들을 병합하는 과정, quick-sort는 피벗 기준 분할, '
                  'heap-sort는 힙으로 최댓값/최솟값을 반복 추출하는 과정을 실제 설명해야 한다. '
                  '크루스칼의 싼 간선부터 고르기는 mst이며 병합·퀵 정렬이 아니다. '
                  '유니온-파인드는 목록에 없으므로 억지로 다른 ID를 붙이지 않는다.\n'
                  '진행 질문의 강한 제외 규칙: 이해됐나요·어느 부분이 헷갈리나요·번호를 골라 주세요·'
                  '이어서 설명할까요는 학습 내용을 답하게 하는 지식 질문이 아니다. 어떤 개념 이름이 '
                  '들어 있어도 제외한다. 제목·목차·학생이 고를 어려움 목록도 questions에 넣지 마라. '
                  '튜터가 예시 안에서 스스로 묻고 바로 답한 수사적 질문도 제외한다. '
                  '반대로 큐가 무엇인지 알고 있나요·다음에 꺼낼 값은 무엇인가요처럼 지식 내용을 '
                  '직접 확인하는 질문은 포함한다.\n'
                  '우선순위≠greedy: 중요한 프로세스부터 CPU를 주는 설명은 priority-scheduling이다. '
                  '일반적인 문제 해결 전략으로서 매 단계 국소 최선의 선택을 누적하는 원리를 '
                  '따로 가르치거나 명시적으로 탐욕 원리를 정의한 경우에만 greedy도 붙인다. '
                  '짧은 일부터 한다는 SJF 설명 자체도 greedy 설명으로 확장하지 마라.\n'
                  '반례A: [1] ## 이게 바로 최소 신장 트리예요 [2] 여기까지 이해되셨나요? '
                  '-> questions=[] (제목과 진행 확인).\n'
                  '반례B: [1] 어느 부분이 헷갈리나요? [2] 1. 프림 과정 [3] 2. 크루스칼과 프림의 차이 '
                  '[4] 3. 왜 최소인지 [5] 번호만 골라 주세요. -> questions=[] (어려움 선택 요청).\n'
                  '반례C: [1] 스케줄링이 왜 필요한지 감이 오나요? '
                  '-> scheduling-metrics를 붙이지 않는다. 대기·반환·응답 시간 지식 질문이 없다.\n'
                  '반례D: [1] 준비 상태는 CPU를 받으면 바로 실행할 수 있지만 기다리는 상태예요. '
                  '[2] 우선순위가 가장 높은 프로세스부터 CPU를 줍니다. '
                  '-> questions=[], explanations=[process-state,priority-scheduling], greedy 제외.\n'
                  '출력 형식: {"questions":[{"concept":"개념ID","line":정수,"format":"open 또는 choice"}],'
                  '"explanations":[{"concept":"개념ID","line":정수}]}\n'
                  '예시1: [1] 큐가 무엇인지 알고 있나요? -> '
                  '{"questions":[{"concept":"queue","line":1,"format":"open"}],"explanations":[]}\n'
                  '예시2: [1] 큐는 먼저 넣은 항목을 먼저 꺼내는 FIFO 구조입니다. -> '
                  '{"questions":[],"explanations":[{"concept":"queue","line":1}]}\n'
                  '예시3: [1] 다음에는 큐에 대해 설명할게요. -> {"questions":[],"explanations":[]}\n'
                  '예시4: [1] 어느 부분이 어렵나요? -> {"questions":[],"explanations":[]}\n\n'
                  '개념 목록:\n' + self.taxonomy)
        numbered = '\n'.join(f'[{i}] {line}' for i, line in enumerate(text.splitlines(), 1))
        raw = self._complete(system, f'직전 학습자 발화(참고만):\n{previous}\n\n분류할 튜터 발화:\n{numbered}', self.schema)
        return parse_judgment(json.loads(raw), text, self.concept_map)

    def respond(self, text, states):
        descriptions = []
        for cid, state in states.items():
            title = self.by_id[cid].title
            reference = self.references.get(cid, self.by_id[cid].description) if state in ('known', 'guess') else ''
            descriptions.append({'concept': title, 'state': state, 'reference': reference})
        system = ('너는 CS 수업을 듣는 대학생 역할이다. 한국어 1~3문장으로 튜터의 질문에만 짧게 답한다. '
                  'known은 아는 상태이므로 참고 지식을 이용해 정확히 답한다. unknown은 모르는 상태라 '
                  '모르겠다고 하거나 핵심을 빠뜨린 답을 한다. slip은 알지만 이번에는 실수로 틀린 답을 한다. '
                  'guess는 모르는 선택형을 운 좋게 맞힌 상황이라 올바른 보기만 선택한다. '
                  'unknown·slip인 개념을 모델의 실제 지식으로 정확히 설명하지 마라. '
                  '지식 상태표·참고·역할·시뮬레이션을 언급하지 않는다. 질문이 없으면 아직 잘 모르겠다고만 한다. '
                  '원하는 특정 선수 개념을 먼저 알려 달라고 요구하거나 튜터를 유도하지 않는다.')
        return self._complete(system, f'내 질문별 상태:\n{json.dumps(descriptions, ensure_ascii=False)}\n\n튜터:\n{text}')


# 역할: 엔진의 학습 본문만 모음(질문·보기·보충·요약·다시 표시한 근거). 버튼·상태 라벨·애니메이션은 제외
# - C도 마크다운 원문 길이로 세므로 렌더링 픽셀·읽기 시간 대신 문자 수의 근사치임
def screen_text(view):
    parts = []
    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'animation':
                    continue
                if key in ('text', 'question', 'title', 'note') and isinstance(item, str):
                    parts.append(item)
                elif key == 'options' and isinstance(item, dict):
                    parts.extend(f'{label}. {text}' for label, text in item.items())
                elif isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(view)
    return '\n'.join(parts)


# 역할: 기존 run_profile을 수정 없이 실행하면서 공개 화면과 제출 답을 수집
class RecordingEngine:
    def __init__(self, api):
        self.api, self.turns = api, []

    def _observe(self, session, request):
        self.turns.append({'learner': request, 'tutor': screen_text(session['view']),
                           'stage': session['stage'], 'view': session['view']})
        return session

    def start(self, entry):
        return self._observe(self.api.start(entry), {'entry': entry})

    def step(self, session, data):
        return self._observe(self.api.step(session, data), data)


def run_engine(profile, api, explainer, concept_map, items, seed=SEED):
    recorder = RecordingEngine(api)
    learner = Learner(profile, items, random.Random(f'{seed}:{profile.id}'), explainer)
    engine = run_profile(profile, recorder, learner, concept_map, items)
    record = _record(profile, 'E', concept_map, items)
    record.update(turns=recorder.turns, engine=engine, error=engine['error'], seconds=engine['seconds'],
                  explained=engine['remediated'],
                  learner_turns=len(engine['answers']),
                  diagnostic_questions=sum(a['stage'] == 'diagnose' for a in engine['answers']))
    record['tutor_chars'] = sum(len(t['tutor']) for t in recorder.turns)
    record['reading_chars'] += record['tutor_chars']
    record['resolved'] = engine['error'] is None and (set(record['truth']) | {profile.target}) <= set(record['explained'])
    return record


# 역할: 세션·개념 단위 합산 비율과 Wilson 구간. 분모 0은 측정 불가(None)로 표시
# - Wilson의 독립 베르누이 가정은 같은 프로필의 개념끼리 상관이 있어 근사치라는 한계를 보고함
def _ratio(k, n):
    return {'numerator': k, 'denominator': n, 'rate': k / n if n else None,
            'ci95': list(wilson_interval(k, n)) if n else None}


def summarize_condition(records):
    found = gaps = cross_found = cross_gaps = over = total = prereq_over = prereq_total = 0
    for r in records:
        explained, truth, known = set(r['explained']), set(r['truth']), set(r['initial_known'])
        crossing = truth & set(r['cross_subject'])
        found += len(explained & truth)
        gaps += len(truth)
        cross_found += len(explained & crossing)
        cross_gaps += len(crossing)
        over += len(explained & known)
        total += len(explained)
        prereq_over += len((explained - {r['target']}) & known)
        prereq_total += len(explained - {r['target']})
    resolved = [r for r in records if r['resolved']]
    result = {'sessions': len(records), 'errors': sum(bool(r['error']) for r in records),
              'recall': _ratio(found, gaps), 'cross_subject_recall': _ratio(cross_found, cross_gaps),
              'over_remediation': _ratio(over, total), 'prerequisite_over_remediation': _ratio(prereq_over, prereq_total),
              'resolution': _ratio(len(resolved), len(records)),
              'resolved_turns_mean': mean(r['learner_turns'] for r in resolved) if resolved else None,
              'all_turns_mean': mean(r['learner_turns'] for r in records) if records else None,
              'reading_chars_total': sum(r['reading_chars'] for r in records),
              'tutor_chars_total': sum(r['tutor_chars'] for r in records),
              'diagnostic_questions_total': sum(r['diagnostic_questions'] for r in records),
              'choice_corrections': sum(r.get('choice_corrections', 0) for r in records),
              'clarification_rules': dict(sum((Counter(r.get('clarification_rules', {})) for r in records), Counter())),
              'sessions_with_diagnosis': _ratio(sum(r['diagnostic_questions'] > 0 for r in records), len(records)),
              'usage': dict(sum((Counter(r['usage']) for r in records), Counter())),
              'total_cost_usd': sum(r['total_cost_usd'] for r in records)}
    engines = [r['engine'] for r in records if 'engine' in r]
    if engines:
        result['engine_m1_m2'] = summarize(engines)['consistent']
    return result


# 역할: 파일럿은 과목별 1명, 전체는 20명. 동일 프로필·조건의 기존 완성 기록만 명시적으로 재사용
# - 세션 단위로 flush, 한도 오류면 즉시 배치 중단. 재사용 파일은 manifest로 seed·프롬프트·모델 확인
# - 결과의 Git 유입·덮어쓰기를 실행 전에 차단
def main(argv=None):
    parser = argparse.ArgumentParser(description='E·Claude 단독·튜터 지시의 짝지은 시뮬레이션 비교')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--base-url', default='http://127.0.0.1:21041')
    parser.add_argument('--learner-url', default='http://127.0.0.1:20900/v1')
    parser.add_argument('--learner-model', default='qwen3.5-9b-awq')
    parser.add_argument('--judge-url', help='대체 판정기 OpenAI 호환 주소(기본: 학습자 모델)')
    parser.add_argument('--judge-model', help='대체 판정기 이름')
    parser.add_argument('--conditions', nargs='+', choices=['E', 'C1', 'C2'], default=['E', 'C1', 'C2'])
    parser.add_argument('--pilot', action='store_true')
    parser.add_argument('--reuse', nargs='*', type=Path, default=[])
    parser.add_argument('--seed', type=int, default=SEED)
    parser.add_argument('--max-cost-usd', type=float, help='이 배치의 CLI 비용 상한. 도달 뒤 다음 유료 호출을 중단')
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if not out.is_relative_to(RESULTS_ROOT) or out.exists():
        parser.error(f'결과는 {RESULTS_ROOT}의 새로운 경로여야 합니다.')
    cm, items = load_concept_map(), load_item_bank()
    profiles = select_profiles(cm, args.seed)
    if args.pilot:
        profiles = [profiles[0], profiles[10]]
    reusable = {}
    for path in args.reuse:
        metadata = json.loads(path.with_suffix('.manifest.json').read_text())
        if metadata['seed'] != args.seed or metadata['system_prompts'] != SYSTEM_PROMPTS or metadata['claude_model'] != CLAUDE_MODEL:
            parser.error('재사용 기록의 seed·프롬프트·모델이 다릅니다.')
        for line in path.read_text().splitlines():
            r = json.loads(line)
            if not r['error']:
                if r['condition'] != 'E' and (metadata.get('judge_version') != 'line-id-v4-progress-boundaries'
                        or metadata['args'].get('judge_url') != str(args.judge_url)
                        or metadata['args'].get('judge_model') != str(args.judge_model)):
                    parser.error('재사용 기록의 판정기·학습자 규칙이 다릅니다.')
                reusable[(r['condition'], r['profile'])] = r
    out.parent.mkdir(parents=True, exist_ok=True)
    manifest = {'seed': args.seed, 'selection_rule': SELECTION_RULE, 'claude_model': CLAUDE_MODEL,
                'system_prompts': SYSTEM_PROMPTS, 'judge_version': 'line-id-v4-progress-boundaries', 'max_turns': MAX_TURNS, 'args': {k: str(v) for k, v in vars(args).items()},
                'base_commit': subprocess.run(['git', 'rev-parse', 'HEAD'], text=True, capture_output=True).stdout.strip(),
                'profiles': [{**asdict(p), 'known': sorted(p.known), 'truth': sorted(true_gaps(p, cm) - {p.target})} for p in profiles]}
    out.with_suffix('.manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    roles = QwenRoles(args.learner_url, args.learner_model, cm, items, args.judge_url, args.judge_model)
    api = StuckClient(args.base_url)
    records, stopped = [], False
    budget = {'spent': 0.0, 'limit': args.max_cost_usd} if args.max_cost_usd is not None else None
    with out.open('x') as file:
        for profile in profiles:
            for condition in args.conditions:
                if (condition, profile.id) in reusable:
                    record = reusable[(condition, profile.id)]
                    if record['initial_known'] != sorted(profile.known):
                        raise ValueError('재사용 프로필의 지식 상태 불일치')
                elif condition == 'E':
                    record = run_engine(profile, api, roles.model, cm, items, args.seed)
                else:
                    tutor = ClaudeTutor(condition, out.parent / 'scratch' / str(uuid.uuid4()), budget)
                    try:
                        record = run_chat(profile, condition, tutor, roles.judge, roles.respond, cm, items, args.seed)
                    finally:
                        removed = tutor.close()
                    record['session_files_removed'] = removed
                records.append(record)
                file.write(json.dumps(record, ensure_ascii=False) + '\n')
                file.flush()
                print(json.dumps({k: record[k] for k in ('condition', 'profile', 'resolved', 'learner_turns', 'error', 'total_cost_usd')}, ensure_ascii=False), flush=True)
                if record['rate_limited'] or record.get('budget_exhausted') or (budget and budget['spent'] >= budget['limit']):
                    stopped = True
                    break
            if stopped:
                break
    summary = {c: summarize_condition([r for r in records if r['condition'] == c]) for c in args.conditions}
    out.with_suffix('.summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return 2 if stopped else int(any(r['error'] for r in records))


if __name__ == '__main__':
    raise SystemExit(main())
