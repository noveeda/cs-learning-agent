## @file
# @brief 12B 하네스 역할 미세조정(QLoRA)의 학습 설정과 학습 행 변환 (계획 Task 5)
# - 순수 설정만 둠: ai-core 테스트·CI가 trl·peft·transformers 없이 돌아야 하므로 이 파일은 그 라이브러리를 import하지 않음
# - 실제 학습·병합은 cs-train 환경에서 finetune.train이 이 설정을 읽어 실행함

## @brief 바탕 모델로 받는 체크포인트와 학습 뒤 만들 GGUF 양자화
# - Task 11 결과로 둘 중 하나를 고름: Q4_K_M이 이기면 일반 체크포인트 → Q4_K_M, QAT가 이기면 QAT 체크포인트 → Q4_0
# - QAT 체크포인트는 Q4_0으로 줄일 때 손실이 작도록 학습된 가중치라 Q4_0과 짝을 맞춤
QUANTS = {
    'google/gemma-4-12B-it': 'Q4_K_M',
    'google/gemma-4-12B-it-qat-q4_0-unquantized': 'Q4_0',
}
BASES = tuple(QUANTS)

## @brief gemma 4 대화 템플릿에서 모델 차례가 끝났음을 뜻하는 표시(토큰 106, 생성 종료 토큰)
# - 정답 JSON 뒤에 붙여 학습해야 운영에서 JSON을 낸 뒤 바로 멈춤
END_OF_TURN = '<turn|>'


## @brief 바탕 모델 하나의 학습 설정(계획 Task 5 값)을 돌려줌; 허용하지 않은 체크포인트면 ValueError
# - lora_r·lora_alpha: 어댑터 크기와 배율. target_modules 'all-linear'은 언어 모델의 모든 선형층(학습 스크립트가 해석)
# - max_len: 프롬프트+정답 토큰 상한. 넘는 행은 자르지 않고 뺌(잘리면 정답 JSON 끝이 없어짐)
# - load_in_4bit: 바탕 가중치를 4bit(nf4)로 올려 학습하는 QLoRA
def config(base: str) -> dict:
    if base not in QUANTS:
        raise ValueError(f'바탕 모델은 {", ".join(BASES)} 중 하나여야 합니다: {base}')
    return {'base': base, 'quant': QUANTS[base], 'lora_r': 16, 'lora_alpha': 32, 'target_modules': 'all-linear',
            'lr': 1e-4, 'epochs': 2, 'max_len': 4096, 'load_in_4bit': True, 'seed': 0}


## @brief 학습 데이터 행(messages = system·user·assistant)을 프롬프트·정답 쌍으로 바꿈
# - prompt: 운영 요청과 같은 system·user 메시지. 학습 스크립트가 대화 템플릿에 add_generation_prompt로 넣음
# - completion: 정답 assistant JSON을 글자 그대로 둠(다시 직렬화하지 않음). 손실은 이 부분에만 매김
# - chat_template_kwargs: 사고 모드를 끔. 운영(vLLM·llama-server)도 같은 인자로 템플릿을 부르므로 프롬프트 끝 모양이 같아짐
#   (gemma 4는 사고를 끄면 모델 차례 앞에 빈 thought 칸을 둠. 대화 전체를 한 번에 렌더하면 이 칸이 빠져 운영과 어긋남)
# - role·meta 같은 생성기 기록 필드는 버림
def to_text_rows(rows) -> list[dict]:
    result = []
    for row in rows:
        *prompt, answer = row['messages']
        if answer['role'] != 'assistant' or [m['role'] for m in prompt] != ['system', 'user']:
            raise ValueError(f'학습 행은 system·user·assistant 순서여야 합니다: {[m["role"] for m in row["messages"]]}')
        result.append({'prompt': prompt, 'completion': answer['content'],
                       'chat_template_kwargs': {'enable_thinking': False}})
    return result
