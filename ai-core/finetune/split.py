## @file
# @brief 미세조정 학습 데이터와 측정 세트를 가르는 규칙: 제외 개념 선택, 측정 질문 정규화, 학습에 쓸 절 고르기
# - 문단은 학습과 측정이 공유하고, 측정 세트 질문과 정규화 후 같은 질문만 학습에서 버림(설계 5장)
# - 제외 개념(OS를 뺀 4과목에서 2개씩 8개)은 학습 예시 어디에도 나오지 않게 해, 합격 판정을 처음 보는 개념 기준으로 함
# - 제외 개념 목록(content/finetune/holdout.json)만 커밋하고, 학습 데이터는 Git 밖에 둠
import argparse
import json
import random
import unicodedata
from pathlib import Path

from serving.concept_map import Concept

HOLDOUT_PATH = Path(__file__).resolve().parents[1] / 'content' / 'finetune' / 'holdout.json'


# 제약을 만족하는 seed를 찾을 때 시도하는 최대 횟수(무한 반복 방지)
MAX_SEED_TRIES = 1000
# 제외 개념을 고르지 않는 과목과 이유(2026-10-29 사용자 결정 B): holdout.json에 그대로 남겨 측정 보고에서 밝힘
SKIPPED_SUBJECTS = {
    'os': '6개념이 절 8개를 나눠 써서, 어떤 개념을 빼도 다른 OS 개념의 학습 절이 3개 미만으로 줄어듦',
}


## @brief seed 하나로 과목마다 per_subject개씩 뽑아 개념 ID 순으로 반환(제약 검사 없음)
# - 과목을 이름순, 후보를 ID순으로 정렬한 뒤 seed 고정 난수로 뽑아, 개념 지도 파일 순서가 바뀌어도 같은 목록이 나옴
def _sample(concepts, passage_counts, per_subject, min_passages, seed, skip_subjects):
    rng = random.Random(seed)
    chosen = []
    for subject in sorted({c.subject for c in concepts} - set(skip_subjects)):
        candidates = sorted(c.id for c in concepts
                            if c.subject == subject and passage_counts.get(c.id, 0) >= min_passages)
        if len(candidates) < per_subject:
            raise ValueError(f'{subject}: 근거 문단 {min_passages}개 이상인 개념이 {len(candidates)}개뿐입니다.')
        chosen += rng.sample(candidates, per_subject)
    return sorted(chosen)


## @brief 제외 뒤에도 제외되지 않은 모든 개념이 학습 절을 충분히 갖는지 검사
# - 개념마다 학습 절 수가 min(min_passages, 원래 절 수) 이상이어야 함: 원래 3개 미만이던 개념은 원래 값보다 줄지 않아야 함
# - 제외 개념과 절을 공유한 개념이 학습 재료를 잃으면 사실상 학습되지 않는 개념이 생겨 학습/제외 구분이 흐려짐
def _keeps_others_trainable(concepts, section_map, holdout, min_passages):
    trainable = trainable_passages(section_map, set(holdout))
    for concept in concepts:
        if concept.id in holdout:
            continue
        mapped = [(e['book'], e['section_id']) for e in section_map['entries'] if concept.id in e['concepts']]
        if sum(1 for key in mapped if key in trainable) < min(min_passages, len(mapped)):
            return False
    return True


## @brief 과목마다 per_subject개씩 제외 개념을 골라 (개념 ID 순 목록, 쓴 seed)를 반환
# - passage_counts: 개념 ID → 근거 문단 수. min_passages 미만인 개념은 제외 개념으로 측정할 재료가 모자라 후보에서 뺌
# - section_map: content/rag/section-map.json을 읽은 dict. 제외 뒤 다른 개념의 학습 절 수를 검사하는 데 씀
# - skip_subjects: 제외 개념을 고르지 않는 과목. 그 과목 개념은 모두 학습 개념으로 남고 제약 검사도 받음
# - seed부터 1씩 올리며 제약을 만족하는 첫 결과를 고르고, 쓴 seed를 holdout.json에 남겨 같은 목록을 재현함
# - MAX_SEED_TRIES번 안에 찾지 못하면 ValueError
def choose_holdout(concepts: list[Concept], passage_counts: dict[str, int], section_map: dict, per_subject: int = 2,
                   min_passages: int = 3, seed: int = 0, skip_subjects: set[str] = frozenset()) -> tuple[list[str], int]:
    for tried in range(seed, seed + MAX_SEED_TRIES):
        chosen = _sample(concepts, passage_counts, per_subject, min_passages, tried, skip_subjects)
        if _keeps_others_trainable(concepts, section_map, chosen, min_passages):
            return chosen, tried
    raise ValueError(f'seed {seed}~{seed + MAX_SEED_TRIES - 1}에서 다른 개념의 학습 절을 지키는 제외 개념을 찾지 못했습니다.')


## @brief 질문을 비교용으로 정규화: 공백·문장부호(유니코드 P 범주)를 지우고 소문자로 바꿈
# - 띄어쓰기·물음표만 다른 측정 질문을 같은 질문으로 보아 학습에서 버리기 위함
def normalize_question(text: str) -> str:
    return ''.join(ch for ch in text if not ch.isspace() and not unicodedata.category(ch).startswith('P')).lower()


## @brief 측정 세트 jsonl들의 question 필드를 정규화한 집합
# - paths: scope.jsonl, retrieval.jsonl, 제외 개념 추가 세트 등. 학습 질문 생성기가 이 집합에 든 질문을 버림
def measured_questions(paths: list[Path]) -> set[str]:
    questions = set()
    for path in paths:
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            if line.strip():
                questions.add(normalize_question(json.loads(line)['question']))
    return questions


## @brief 학습에 쓸 (책, 절 ID) 집합: 대응 개념에 제외 개념이 하나라도 있는 절은 뺌
# - section_map: content/rag/section-map.json을 읽은 dict. 여러 개념에 대응된 절도 제외 개념이 섞이면 통째로 빼야 근거가 새지 않음
def trainable_passages(section_map: dict, holdout: set[str]) -> set[tuple[str, str]]:
    return {(e['book'], e['section_id']) for e in section_map['entries'] if not holdout & set(e['concepts'])}


## @brief 커밋된 제외 개념 목록을 읽어 개념 ID 집합으로 반환
def load_holdout(path: Path = HOLDOUT_PATH) -> set[str]:
    return set(json.loads(Path(path).read_text(encoding='utf-8'))['concepts'])


## @brief 제외 개념 목록 생성 명령: python -m finetune.split --write-holdout
# - 운영 개념 지도와 절 대응표로 개념별 근거 문단(대응 절) 수를 세어 --seed(기본 0)부터 고르고, 쓴 seed·건너뛴 과목과 함께 holdout.json에 씀
# - 고른 개념의 과목·문단 수와 학습에 남는 절 수를 출력해 PR·문서에 그대로 옮길 수 있게 함
def main(argv=None):
    from rag.section_map import MAP_PATH
    from serving.concept_map import load_concept_map

    parser = argparse.ArgumentParser(prog='python -m finetune.split', description='제외 개념 목록을 만듭니다.')
    parser.add_argument('--write-holdout', type=Path, nargs='?', const=HOLDOUT_PATH, required=True)
    parser.add_argument('--seed', type=int, default=0)
    args = parser.parse_args(argv)
    concepts = load_concept_map().concepts
    raw_map = json.loads(MAP_PATH.read_text(encoding='utf-8'))
    counts = {c.id: sum(1 for e in raw_map['entries'] if c.id in e['concepts']) for c in concepts}
    chosen, seed = choose_holdout(concepts, counts, raw_map, seed=args.seed, skip_subjects=set(SKIPPED_SUBJECTS))
    args.write_holdout.parent.mkdir(parents=True, exist_ok=True)
    args.write_holdout.write_text(
        json.dumps({'version': 1, 'seed': seed, 'skipped_subjects': SKIPPED_SUBJECTS, 'concepts': chosen},
                   ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8')
    print(f'seed {seed}, 학습 절 {len(trainable_passages(raw_map, set(chosen)))}/{len(raw_map["entries"])}')
    by_id = {c.id: c for c in concepts}
    for concept_id in chosen:
        print(f'{by_id[concept_id].subject}\t{concept_id}\t{by_id[concept_id].title}\t{counts[concept_id]}')


if __name__ == '__main__':
    main()
