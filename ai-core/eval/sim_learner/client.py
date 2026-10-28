## @file
# @brief Spring 막힘 세션 공개 API 클라이언트. 사람 학습자의 브라우저와 같은 경로(POST /api/stuck/sessions, /{id}/step)로 진행
# - 응답은 브라우저 공개 형태({id, version, stage, view, entry})만 받음. 측정도 화면에 보이는 view만으로 계산함
"""Client for the public Spring stuck-session API."""
import time
from collections.abc import Callable

import httpx

# 재시도 규칙: 같은 세션 동시 요청(409)·모델 잠금 사용 중(503)은 잠시 뒤 풀리는 상태라 2초 간격으로 최대 3번 다시 보냄
# - 그 밖의 오류(422·502·504 등)는 다시 보내도 같으므로 바로 실패로 기록
RETRIES = 3
RETRY_WAIT = 2.0
RETRY_STATUS = {409, 503}
# 보충·채점 한 단계는 Spring의 AI core 대기(320초)까지 걸릴 수 있어 그보다 조금 길게 기다림
TIMEOUT = 330.0


class StuckApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f'{status}: {message}')
        self.status = status


class StuckClient:
    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep):
        self.http = httpx.Client(base_url=base_url, timeout=TIMEOUT, transport=transport)
        self.sleep = sleep

    ## @brief 새 막힘 세션을 시작하고 첫 공개 상태를 반환
    def start(self, entry: dict) -> dict:
        return self._post('/api/stuck/sessions', {'entry': entry})

    ## @brief 마지막으로 받은 version과 입력을 보내 다음 공개 상태를 반환
    def step(self, session: dict, data: dict) -> dict:
        return self._post(f'/api/stuck/sessions/{session["id"]}/step', {'version': session['version'], 'input': data})

    ## @brief POST 한 번(재시도 포함). 409·503은 RETRIES번까지 다시 보내고, 끝내 실패하면 상태 코드를 담은 StuckApiError
    def _post(self, path: str, body: dict) -> dict:
        for attempt in range(RETRIES):
            response = self.http.post(path, json=body)
            if response.status_code == 200:
                return response.json()
            if response.status_code not in RETRY_STATUS or attempt == RETRIES - 1:
                break
            self.sleep(RETRY_WAIT)
        try:
            message = response.json().get('message', '')
        except ValueError:
            message = response.text[:200]
        raise StuckApiError(response.status_code, message)
