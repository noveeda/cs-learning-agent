"""Local web bridge for the diagram agent (no authentication; bind to loopback)."""
import json
import logging
import re
import os
import threading
import httpx
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from openai import OpenAI
from pydantic import BaseModel, Field, model_validator

from serving.curriculum import JobState, ModelGateway, step
from serving.agent import run_agent
from serving.context import token_counter
from serving.tools.diagram import Diagram

app = FastAPI(title='CS learning agent')
OUTPUT_DIR = Path(__file__).resolve().parents[1] / 'outputs' / 'diagrams'
BUSY = threading.Lock()


class Artifact(BaseModel):
    artifact_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    diagram: Diagram


class Turn(BaseModel):
    role: str = Field(pattern='^(user|assistant)$')
    content: str = Field(min_length=1)
    artifacts: list[Artifact] = Field(default_factory=list)


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    history: list[Turn] = Field(default_factory=list)
    active_artifact: Artifact | None = None

    @model_validator(mode='after')
    def validate_size(self):
        if not self.question.strip():
            raise ValueError('질문을 입력해 주세요.')
        return self


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.post('/chat')
def chat(body: Question):
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
