# 파일 역할: 시뮬레이션 학습자의 문항 응답. 지식 상태(Profile)대로 답하는 "지식 상태가 정해진 응답자"
# - 사람 학습자를 흉내 내는 것이 아니라, 엔진이 지식 상태를 얼마나 맞게 찾는지 재는 시험 장치
# - 그래서 문항 은행(정답 키·근거·루브릭)을 읽을 수 있음. 엔진·화면에는 학습자가 보낸 답만 감
# - 자동 채점 문항은 규칙으로 답하고, 서술 문항만 학습자 모델(GPU 1번, 서빙과 다른 계열)에 맡김
"""Simulated learner that answers stuck-flow items according to a knowledge profile."""
import random
from collections.abc import Callable

from openai import OpenAI

from serving.grading import grade
from serving.item_bank import Item

from .profiles import Profile

# 모르는 단답형 문항에 내는 답: 어떤 정답 키(숫자·기호 목록)와도 정규화 후 같아지지 않는 문장
UNKNOWN_SHORT_ANSWER = '모르겠습니다'

# 서술 답 생성 함수 형식: (질문, 참고 답 또는 None) → 답. 참고 답이 있으면 "아는" 상태로 정확하게, 없으면 불완전하게 씀
Explainer = Callable[[str, dict | None], str]


# 역할: 프로필 하나로 문항에 답하는 응답자. 보충을 받은 개념은 "알게 됨"으로 바뀜
# - 엔진의 "확인을 통과해야 다음으로" 흐름을 그대로 겪게 하기 위해, 보충 화면을 본 개념은 그 뒤 아는 개념처럼 답함
class Learner:
    def __init__(self, profile: Profile, items: list[Item], rng: random.Random, explainer: Explainer):
        self.profile, self.rng, self.explainer = profile, rng, explainer
        self.known = set(profile.known)
        self.items = {item.id: item for item in items}

    # 역할: 보충을 받은 개념을 아는 개념으로 바꿈
    def learn(self, concept: str) -> None:
        self.known.add(concept)

    # 역할: 화면 문항(view['item'])에 대한 (답, 이유)를 반환. 이유는 known·unknown·slip·guess 중 하나(실패 사례 분류에 씀)
    # - 아는 개념: 정답 키로 답하되 확률 slip으로 일부러 틀림
    # - 모르는 개념: 선택형은 확률 guess로 찍어 맞히고, 아니면 틀린 보기. 단답형은 틀린 값
    # - 서술형: 아는 개념이면 근거·루브릭을 참고 답으로 주고, 모르거나 slip이면 참고 답 없이 학습자 모델에 맡김
    def answer(self, view: dict) -> tuple[str, str]:
        item = self.items[view['id']]
        knows = item.concept in self.known
        slipped = knows and self.rng.random() < self.profile.slip
        if item.format == 'explanation':
            if knows and not slipped:
                return self.explainer(item.question, {'rationale': item.rationale, 'rubric': item.rubric}), 'known'
            return self.explainer(item.question, None), 'slip' if slipped else 'unknown'
        if knows:
            return (self._wrong(item), 'slip') if slipped else (item.answer, 'known')
        if item.format == 'multiple_choice' and self.rng.random() < self.profile.guess:
            return item.answer, 'guess'
        return self._wrong(item), 'unknown'

    # 역할: 확실히 틀리는 답. 선택형은 정답이 아닌 보기 중 하나, 단답형은 정해 둔 모름 답
    def _wrong(self, item: Item) -> str:
        if item.format == 'multiple_choice':
            answer = self.rng.choice(sorted(label for label in item.options if label != item.answer))
        else:
            answer = UNKNOWN_SHORT_ANSWER
        if grade(item, answer):
            raise ValueError(f'{item.id}: 틀린 답을 만들지 못했습니다.')
        return answer


SYSTEM = ('너는 자료구조 수업을 듣는 대학생이다. 질문에 한국어로 3~5문장으로 답한다. 한자·영어 문장은 쓰지 않고, '
          '필요한 용어만 영어 원어를 괄호로 쓴다. 마크다운 제목이나 목록 없이 이어지는 문장으로 쓴다.')
KNOWN = ('너는 이 개념을 이해했다. 아래 참고 내용은 네가 알고 있는 지식이다. 문장을 그대로 베끼지 말고 네 말로, '
         '핵심 요소를 빠짐없이 정확하게 담아 답한다.')
UNKNOWN = ('너는 이 개념을 아직 잘 모른다. 질문의 용어를 되풀이하거나 막연한 추측으로 짧게 답하고, '
           '핵심 원리나 정확한 이유는 쓰지 않는다. 아는 척하며 정답을 지어내지 않는다.')


# 역할: OpenAI 호환 학습자 모델(vLLM, GPU 1번)로 서술 답을 만드는 Explainer 구현
# - 추론 끔·temperature 0.7: 짧은 답을 빠르게 만들고, 같은 질문에도 답이 조금씩 달라지게 함
# - presence_penalty 0: Qwen 계열 한국어 답의 한자 섞임이 권장값(1.5)보다 줄었음(docs/model-selection.md)
class LearnerModel:
    def __init__(self, base_url: str, model: str | None = None, timeout: float = 60.0):
        self.client = OpenAI(base_url=base_url, api_key='not-needed', max_retries=1, timeout=timeout)
        self.model = model or self.client.models.list().data[0].id

    def __call__(self, question: str, reference: dict | None) -> str:
        if reference:
            rubric = '\n'.join(f'- {r}' for r in reference['rubric'])
            user = f'{KNOWN}\n\n참고 내용: {reference["rationale"]}\n답에 담을 핵심:\n{rubric}\n\n질문: {question}'
        else:
            user = f'{UNKNOWN}\n\n질문: {question}'
        response = self.client.chat.completions.create(
            model=self.model, temperature=0.7, max_tokens=400, presence_penalty=0.0,
            messages=[{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user}],
            extra_body={'chat_template_kwargs': {'enable_thinking': False}})
        return (response.choices[0].message.content or '').strip()
