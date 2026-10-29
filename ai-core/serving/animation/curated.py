## @file
# @brief 사람이 미리 짠 개념별 장면(content/<과목>/scenes.json)을 읽어 개념 ID → 장면 요청 목록 표를 만듦
# - 막힘 보충(stuck._animation)과 학습하기 채팅 애니메이션(harness_chat._animation_event)이 이 표를 먼저 봄
#   표에 있는 개념은 모델 장면 선택(choose_animation)을 부르지 않고 이 장면 중 하나를 씀
# - 이점: 모델 호출 1회 절약(작은 모델에서 장면 선택 실패·형식 오류가 사라짐),
#   사람이 짠 장면은 개념마다 서로 다르게 짜 두어 재설명 때 확실히 다른 장면을 보여 줌
# - 표에 없는 개념은 지금처럼 CONCEPT_SCENES로 모델이 장면을 고름(fallback)
"""Hand-written per-concept animation scenes."""
import json
import logging
from pathlib import Path

from serving.concept_map import CONTENT_DIR

from . import simulate
from .model import AnimationRequest, AnimationRequestModel


## @brief content_dir 아래 과목 폴더의 scenes.json을 모두 읽어 {개념 ID: (장면 요청, ...)}를 반환
# - 파일 형식: {"<개념 ID>": [장면 요청 dict, ...]}. 파일이 없는 과목은 건너뜀
# - 장면마다 strict 스키마 검증 + simulate 실행을 해 보고, 실패한 장면은 버리고 경고 로그를 남김
#   한 장면이 깨져도 서버 시작이나 나머지 장면을 막지 않기 위함. 읽을 수 없는 파일도 같은 이유로 경고 후 건너뜀
# - CONCEPT_SCENES·CONCEPT_SCENE_LIMITS는 적용하지 않음: 그 표는 모델이 장면을 고를 때의 제약이고,
#   scenes.json은 사람이 개념에 맞게 짜서 과목별 테스트로 검증한 장면이라 실행 가능 여부만 확인함
# - 장면이 하나도 남지 않은 개념은 표에 넣지 않아 모델 장면 선택으로 돌아가게 함
def load_curated_scenes(content_dir: Path = CONTENT_DIR) -> dict[str, tuple[AnimationRequest, ...]]:
    table = {}
    for path in sorted(Path(content_dir).glob('*/scenes.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            logging.warning('Curated scenes skipped %s: %s', path, exc)
            continue
        for concept_id, raws in data.items():
            requests = []
            for index, raw in enumerate(raws):
                try:
                    request = AnimationRequestModel.model_validate(raw).root
                    simulate(request)
                except (ValueError, TypeError) as exc:
                    logging.warning('Curated scene dropped %s %s[%d]: %s', path, concept_id, index, str(exc)[:300])
                    continue
                requests.append(request)
            if requests:
                table[concept_id] = tuple(requests)
    return table


# 서버가 이 모듈을 불러올 때(시작 시) 한 번 읽고 검증한 표; 장면 데이터를 바꾸면 서버를 다시 띄워야 반영됨
CURATED_SCENES = load_curated_scenes()
