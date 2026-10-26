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
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError, model_validator

from serving.concept_map import load_concept_map
from serving.curriculum import JobState, ModelGateway, step
from serving.item_bank import load_item_bank
from serving.stuck import LLMUnavailable, StepInput, StuckState, step as stuck_step
from serving.stuck_llm import StuckModelGateway
from serving.agent import run_agent
from serving.context import token_counter
from serving.animation.model import AnimationRequest
from serving.tools.diagram import Diagram

app = FastAPI(title='CS learning agent')
OUTPUT_DIR = Path(__file__).resolve().parents[1] / 'outputs' / 'diagrams'
BUSY = threading.Lock()


# 역할: 대화 문맥에 다시 전달할 도식 ID와 그래프 구조
class DiagramArtifact(BaseModel):
    artifact_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    diagram: Diagram


# 역할: 대화 문맥에 다시 전달할 애니메이션 ID와 장면 요청. 프레임은 요청에서 다시 계산되므로 받지 않음
class AnimationArtifact(BaseModel):
    artifact_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    kind: Literal['animation']
    request: AnimationRequest


# Spring은 artifact 이벤트를 그대로 저장해 이력·활성 artifact로 돌려보내므로 두 종류를 모두 받음
# - kind가 없으면 도식, kind='animation'이면 애니메이션. 저장 이벤트의 나머지 필드(url, frames 등)는 무시
Artifact = DiagramArtifact | AnimationArtifact


# 역할: 완료 이력으로 전달되는 문답 한 항목
class Turn(BaseModel):
    role: str = Field(pattern='^(user|assistant)$')
    content: str = Field(min_length=1)
    artifacts: list[Artifact] = Field(default_factory=list)


# 역할: 최신 질문·완료 이력·활성 도식(또는 애니메이션)을 묶는 요청 계약
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


# 역할: 그래프 규칙 검증을 통과한 자료구조 개념 지도 반환; 진단 엔진과 로드맵 화면이 같은 지도를 쓰게 함
@app.get('/concept-map')
def concept_map():
    try:
        return load_concept_map().model_dump()
    except (OSError, ValidationError):
        # 손상되거나 규칙을 어긴 지도는 진단 기준이 될 수 없으므로 일부라도 응답하지 않고, 경로·오류 내용은 로그에만 남김
        logging.exception('Concept map failed to load')
        raise HTTPException(500, '개념 지도를 불러오지 못했습니다.') from None


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


# 도구 실패 시 화면에 보여 줄 안내; 검증 오류 원문은 모델 재시도용이라 웹에 내보내지 않고 도구 이름에 맞는 문구만 전달
TOOL_ERROR_MESSAGES = {'create_diagram': '도식 입력을 수정하고 있습니다.', 'show_animation': '애니메이션 입력을 수정하고 있습니다.'}


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
                        # 애니메이션은 SVG 파일이 없으므로 주소를 붙이지 않고 프레임 데이터만 전달
                        if event['type'] == 'artifact':
                            event = {k: v for k, v in event.items() if k != 'path'}
                            if event.get('kind') != 'animation':
                                event['url'] = f"/api/artifacts/{event['artifact_id']}"
                        elif event['type'] == 'tool_started':
                            event = {'type': 'tool_started', 'name': event['name']}
                        elif event['type'] == 'tool_error':
                            event = {'type': 'tool_error', 'error': TOOL_ERROR_MESSAGES.get(event.get('name'), '도구 입력을 수정하고 있습니다.')}
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
