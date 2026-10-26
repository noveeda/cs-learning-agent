# 파일 역할: FastAPI로 채팅 스트림·도식 파일·커리큘럼 단계·개념 지도·막힘 세션 단계를 제공하는 로컬 전용 AI 서비스
"""Local web bridge for the diagram agent (no authentication; bind to loopback)."""
import json
import logging
import re
import os
import threading
import time
import httpx
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from openai import OpenAI
from pydantic import BaseModel, Field, model_validator

from serving.concept_map import load_concept_map
from serving.curriculum import JobState, ModelGateway, step
from serving.item_bank import load_item_bank
from serving.stuck import LLMUnavailable, StepInput, StuckState, step as stuck_step
from serving.stuck_llm import StuckModelGateway
from serving.agent import run_agent
from serving.context import token_counter
from serving.tools.diagram import Diagram

app = FastAPI(title='CS learning agent')
OUTPUT_DIR = Path(__file__).resolve().parents[1] / 'outputs' / 'diagrams'
BUSY = threading.Lock()


# 역할: 대화 문맥에 다시 전달할 도식 ID와 그래프 구조
class Artifact(BaseModel):
    artifact_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    diagram: Diagram


# 역할: 완료 이력으로 전달되는 문답 한 항목
class Turn(BaseModel):
    role: str = Field(pattern='^(user|assistant)$')
    content: str = Field(min_length=1)
    artifacts: list[Artifact] = Field(default_factory=list)


# 역할: 최신 질문·완료 이력·활성 도식을 묶는 요청 계약
class Question(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    history: list[Turn] = Field(default_factory=list)
    active_artifact: Artifact | None = None

    # 역할: 공백만 있는 질문을 거부해 의미 없는 모델 요청 방지
    @model_validator(mode='after')
    def validate_size(self):
        if not self.question.strip():
            raise ValueError('질문을 입력해 주세요.')
        return self


# 역할: AI core HTTP 서버의 응답 여부 확인; 모델 추론 성공 여부와는 별개
@app.get('/health')
def health():
    return {'status': 'ok'}


# 역할: 그래프 규칙 검증을 통과한 자료구조 개념 지도와 막힘 지원 개념 목록 반환; 진단 엔진과 로드맵 화면이 같은 지도를 쓰게 함
# - stuck_supported: 확인(check)·서술(explanation) 문항이 모두 있어 엔진이 이해 확인까지 할 수 있는 개념 ID(지도 순서)
# - 화면이 목록을 따로 들고 있지 않고 문항 은행에서 계산하므로, 문항 묶음이 추가되면 [막혔어요]도 자동으로 따라옴
@app.get('/concept-map')
def concept_map():
    try:
        concept_map = load_concept_map()
        purposes = {(item.concept, item.purpose) for item in load_item_bank(concept_map=concept_map)}
    except (OSError, ValueError):
        # 손상되거나 규칙을 어긴 지도·문항은 진단 기준이 될 수 없으므로 일부라도 응답하지 않고, 경로·오류 내용은 로그에만 남김
        # - pydantic ValidationError도 ValueError의 하위 클래스라 함께 잡힘
        logging.exception('Concept map failed to load')
        raise HTTPException(500, '개념 지도를 불러오지 못했습니다.') from None
    supported = [c.id for c in concept_map.concepts if {(c.id, 'check'), (c.id, 'explanation')} <= purposes]
    return {**concept_map.model_dump(), 'stuck_supported': supported}


# 역할: 막힘 세션 단계 요청; state가 없으면 시작 입력으로 새 세션을 만듦
class StuckRequest(BaseModel):
    state: StuckState | None = None
    input: StepInput


# 역할: 막힘 세션을 한 단계 진행한 새 상태 반환; 저장·버전 관리는 호출한 Spring이 담당
# - LLM이 필요한 단계는 채팅·커리큘럼과 같은 모델 잠금(BUSY)을 그 호출 동안만 잡음; 바쁘거나 모델이 실패하면 503, 상태는 바뀌지 않음
# - 현재 단계와 맞지 않는 입력(오래된 화면의 답 등)은 409로 거부해 상태가 꼬이지 않게 함
@app.post('/stuck/step', response_model=StuckState)
def stuck_session_step(body: StuckRequest):
    concept_map, items = load_concept_map(), load_item_bank()
    try:
        return stuck_step(body.state, body.input, StuckModelGateway(concept_map, BUSY), concept_map, items, time.time())
    except LLMUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


# 역할: 질문을 받아 에이전트 이벤트를 NDJSON 스트림으로 반환
@app.post('/chat')
def chat(body: Question):
    # 역할: 모델 접근 잠금을 획득하고 이벤트를 외부 공개 형태로 변환; 실패·종료 시 잠금 해제
    def events():
        if not BUSY.acquire(blocking=False):
            yield json.dumps({'type': 'error', 'message': '다른 질문을 처리 중입니다. 잠시 후 다시 시도해 주세요.'}, ensure_ascii=False) + '\n'
            return
        try:
            yield json.dumps({'type': 'started'}) + '\n'
            with OpenAI(base_url=os.getenv('VLLM_BASE_URL', 'http://127.0.0.1:8000/v1'),
                        api_key=os.getenv('VLLM_API_KEY', 'not-needed'), timeout=120, max_retries=0) as client:
                models = client.models.list().data
                model = os.getenv('VLLM_MODEL') or models[0].id
                limit = next(m for m in models if m.id == model).model_extra['max_model_len']
                with httpx.Client(base_url=str(client.base_url).removesuffix('/').removesuffix('/v1'),
                                  headers={'Authorization': f'Bearer {client.api_key}'}, timeout=30) as http:
                    for event in run_agent(client, model, body.question, OUTPUT_DIR,
                                           history=[turn.model_dump() for turn in body.history],
                                           active_artifact=body.active_artifact.model_dump() if body.active_artifact else None,
                                           count_tokens=token_counter(http, model), context_limit=int(limit)):
                        # 서버 파일 경로와 내부 도구 인자는 웹에 노출하지 않고 공개 URL·진행 상태만 전달
                        if event['type'] == 'artifact':
                            event = {k: v for k, v in event.items() if k != 'path'}
                            event['url'] = f"/api/artifacts/{event['artifact_id']}"
                        elif event['type'] == 'tool_started':
                            event = {'type': 'tool_started', 'name': event['name']}
                        elif event['type'] == 'tool_error':
                            event = {'type': 'tool_error', 'error': '도식 입력을 수정하고 있습니다.'}
                        yield json.dumps(event, ensure_ascii=False) + '\n'
        except Exception:
            logging.exception('Agent request failed')
            yield json.dumps({'type': 'error', 'message': '답변 생성에 실패했습니다. 모델 서버 상태를 확인하고 다시 시도해 주세요.'}, ensure_ascii=False) + '\n'
        finally:
            BUSY.release()
    return StreamingResponse(events(), media_type='application/x-ndjson',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


# 역할: 검증된 도식 ID의 SVG를 제한된 브라우저 실행 정책과 함께 반환
@app.get('/artifacts/{artifact_id}')
def artifact(artifact_id: str):
    if not re.fullmatch(r'[0-9a-f]{32}', artifact_id):
        raise HTTPException(404)
    path = OUTPUT_DIR / f'{artifact_id}.svg'
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type='image/svg+xml', headers={
        'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; font-src data:; sandbox",
        'X-Content-Type-Options': 'nosniff',
    })


# 역할: 채팅과 같은 모델 잠금을 사용해 저장 상태의 커리큘럼 한 단계만 실행
@app.post('/curriculum/step', response_model=JobState)
def curriculum_step(body: JobState):
    if not BUSY.acquire(blocking=False):
        raise HTTPException(503, '다른 요청을 처리 중입니다. 잠시 후 다시 시도해 주세요.')
    try:
        with OpenAI(base_url=os.getenv('VLLM_BASE_URL', 'http://127.0.0.1:8000/v1'),
                    api_key=os.getenv('VLLM_API_KEY', 'not-needed'), timeout=300, max_retries=0) as client:
            models = client.models.list().data
            model = os.getenv('VLLM_MODEL') or models[0].id
            limit = next(m for m in models if m.id == model).model_extra['max_model_len']
            with httpx.Client(base_url=str(client.base_url).removesuffix('/').removesuffix('/v1'),
                              headers={'Authorization': f'Bearer {client.api_key}'}, timeout=30) as http:
                return step(body, ModelGateway(client, http, model, int(limit)))
    except Exception:
        logging.exception('Curriculum step failed')
        raise HTTPException(502, '모델 서버에 연결하지 못했습니다. 저장된 작업을 이어서 재시도해 주세요.') from None
    finally:
        BUSY.release()
