## @file
# @brief 학습하기 채팅을 근거 검사 하네스(harness.answer)에 연결하는 부분: 스위치, 이력의 근거 기록 복원, 교재 그림·애니메이션 artifact
# - 스위치 CS_HARNESS=1일 때만 /chat이 이 모듈을 씀. 꺼져 있으면 기존 에이전트(run_agent) 그대로임(실험 보호, spec 9절)
# - 하네스 경로에는 모델 도구가 없으므로 자유 도식(create_diagram)은 쓰이지 않음. 그림은 교재 그림이나 애니메이션 하나뿐
# - 웹으로 나가는 이벤트에는 서버 파일 경로를 넣지 않음. 교재 그림은 공개 폴더로 복사하고 주소(/api/artifacts/{id})만 줌
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Iterator

from rag.embed import Embedder
from rag.fetch import DATA_DIR
from rag.search import Retriever
from serving.animation import CONCEPT_SCENES, create_animation
from serving.harness import Grounding, answer
from serving.stuck import Entry, LLMUnavailable


## @brief 하네스 스위치가 켜졌는지; 기본은 꺼짐이며 운영에서 켜는 시점은 사용자가 정함
def harness_enabled() -> bool:
    return os.getenv('CS_HARNESS') == '1'


## @brief 근거 문단 유사도 하한 기본값: bge-m3 정답 점수 분포의 하위 5%(docs/harness-measurement.md 2-3, 확정 대응표 기준)
# - 이 값보다 질문과 덜 비슷한 문단은 근거로 쓰지 않아 엉뚱한 근거 대신 근거 없음으로 끝남
# - 점수 척도가 임베딩 모델마다 달라 rag.search의 기본값(0.0)이 아니라 운영 검색기를 만드는 여기에 둠. 모델을 바꾸면 다시 잼
DEFAULT_MIN_SCORE = 0.50


## @brief 공용 색인(RAG_DATA_DIR/index.sqlite)과 환경 변수 임베더로 검색기를 만듦; 색인이 없으면 검색 결과가 비어 근거 없음으로 끝남
# - 하한은 RAG_MIN_SCORE, 없으면 DEFAULT_MIN_SCORE
def build_retriever() -> Retriever:
    return Retriever(DATA_DIR / 'index.sqlite', Embedder.from_env(),
                     float(os.getenv('RAG_MIN_SCORE', DEFAULT_MIN_SCORE)))


## @brief 마지막 assistant 턴에 저장된 grounding artifact를 Grounding으로 되살림; 없으면 None
# - 마지막 턴만 봄: 그 사이에 근거 없이 끝난 답(스위치를 켜기 전 에이전트 답 등)이 있으면 더 이전 기록을 이어 쓰지 않게 함
def previous_grounding(history: list[dict]) -> Grounding | None:
    last = next((turn for turn in reversed(history) if turn.get('role') == 'assistant'), None)
    for artifact in (last or {}).get('artifacts') or []:
        if artifact.get('kind') == 'grounding':
            data = artifact['grounding']
            return Grounding(tuple(data['concepts']), tuple(data['passage_ids']), tuple(data['verified']))
    return None


## @brief 교재 그림 artifact의 PNG를 output_dir/{artifact_id}.png로 복사하고 경로 대신 공개 주소를 붙인 이벤트를 돌려줌
# - 복사에 실패하면(원본 PNG가 지워짐 등) 깨진 그림을 보이지 않도록 None. 이유는 로그에만 남김
def _published_figure(event: dict, output_dir: Path) -> dict | None:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(event['path'], output_dir / f"{event['artifact_id']}.png")
    except OSError as exc:
        logging.warning('Harness figure copy failed: %s', exc)
        return None
    public = {k: v for k, v in event.items() if k != 'path'}
    return {**public, 'url': f"/api/artifacts/{event['artifact_id']}"}


## @brief 장면이 있는 첫 개념으로 장면 선택(모델 1회) + 시뮬레이션을 해 애니메이션 artifact를 만듦; 못 만들면 None
# - 장면 단계는 시뮬레이터가 계산하므로 근거 검사 없이 보여 줘도 됨(spec 7절: 애니메이션은 코드가 내용을 정함)
# - 학생 질문을 막힌 위치(excerpt)로 넘겨 장면 값이 질문의 예시를 따르게 함
# - 장면 선택·실행 실패는 부가 자료 실패라 답을 멈추지 않음(막힘 흐름 _animation과 같은 처리)
def _animation_event(question: str, concepts: list[str], gw, concept_map) -> dict | None:
    concept_id = next((c for c in concepts if c in CONCEPT_SCENES), None)
    if concept_id is None:
        return None
    concept = next(c for c in concept_map.concepts if c.id == concept_id)
    try:
        entry = Entry(kind='excerpt', excerpt=question[:2000], return_to='chat')
        request = gw.choose_animation(concept, entry, CONCEPT_SCENES[concept_id])
        if request is None:
            return None
        return {'type': 'artifact', **create_animation(json.dumps(request.model_dump()))}
    except (LLMUnavailable, ValueError) as exc:
        logging.warning('Harness animation failed for %s: %s', concept_id, exc)
        return None


## @brief 하네스 답 이벤트를 웹에 보낼 형태로 바꿔 내보냄: 직전 근거 기록 이어 받기, 그림 하나(교재 그림 또는 애니메이션) 붙이기
# - 그림 선택 기준: 교재 그림이 검색되면 교재 그림, 없으면 고른 개념에 애니메이션 장면이 있을 때 애니메이션
#   - 그림은 grounding artifact 바로 앞에 둠. 개념(grounding.concepts)을 알아야 장면을 고를 수 있기 때문
# - 차단 답에는 grounding이 없으므로 그림도 붙지 않음
def chat_events(question: str, history: list[dict], gw, retriever, concept_map, log,
                output_dir: Path) -> Iterator[dict]:
    figure = None
    for event in answer(question, previous_grounding(history), gw, retriever, concept_map, log):
        if event.get('kind') == 'figure':
            figure = _published_figure(event, Path(output_dir))
            continue
        if event.get('kind') == 'grounding':
            extra = figure or _animation_event(question, event['grounding']['concepts'], gw, concept_map)
            if extra:
                yield extra
        yield event
