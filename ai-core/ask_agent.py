## @file
# @brief 웹 없이 질문·도구 호출·도식 생성을 확인하는 CLI 실행 진입점
"""Run with: python ask_agent.py '페이징 구조를 그림으로 설명해줘'"""
import argparse
import json
import os
from pathlib import Path

from openai import OpenAI

from serving.agent import run_agent


## @brief 질문과 출력 옵션을 읽고 에이전트 이벤트를 JSON 또는 사람이 읽는 텍스트로 출력
def main():
    parser = argparse.ArgumentParser(description='Qwen + SVG diagram tool demo')
    parser.add_argument('question', nargs='?', default='가상 페이지 0, 1, 2가 물리 프레임 3, 0, 2에 매핑되는 도식을 만들고 설명해줘.')
    parser.add_argument('--json', action='store_true', help='Print events as JSON lines')
    args = parser.parse_args()
    output_dir = Path(__file__).resolve().parent / 'outputs' / 'diagrams'
    with OpenAI(base_url=os.getenv('VLLM_BASE_URL', 'http://127.0.0.1:8000/v1'),
                    api_key=os.getenv('VLLM_API_KEY', 'not-needed'), timeout=120, max_retries=0) as client:
        model = os.getenv('VLLM_MODEL') or client.models.list().data[0].id
        for event in run_agent(client, model, args.question, output_dir):
            if args.json:
                print(json.dumps(event, ensure_ascii=False), flush=True)
            elif event['type'] == 'tool_started':
                print(f"[도구 호출] {event['name']}", flush=True)
            elif event['type'] == 'artifact':
                print(f"[도식 저장] {event['path']}", flush=True)
            elif event['type'] == 'tool_error':
                print(f"[도구 오류] {event['error']}", flush=True)
            elif event['type'] == 'text_delta':
                print(event['text'], end='', flush=True)
            elif event['type'] == 'answer':
                print(flush=True)


if __name__ == '__main__':
    main()
