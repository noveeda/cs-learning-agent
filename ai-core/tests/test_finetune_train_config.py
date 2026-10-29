## @file
# @brief 미세조정 학습 설정과 학습 행 변환을 검증: 허용한 바탕 모델만 받는지, 정답 JSON을 글자 그대로 학습 대상으로 넘기는지
# - trl·peft·transformers 없이 도는 순수 설정만 봄(실제 학습은 cs-train 환경의 finetune.train)
import json
import unittest

from finetune.train_config import BASES, config, to_text_rows


class ConfigTests(unittest.TestCase):
    # 검증 목적: 계획에 적은 두 체크포인트만 바탕 모델로 받고, 학습 설정 값이 계획과 같은지 확인
    # - 다른 체크포인트를 받으면 Task 11에서 고른 양자화(Q4_0·Q4_K_M)와 어긋난 모델을 학습하게 됨
    def test_config_rejects_unknown_base(self):
        with self.assertRaises(ValueError):
            config('google/gemma-4-12B-pt')
        qat = config('google/gemma-4-12B-it-qat-q4_0-unquantized')
        self.assertEqual({k: v for k, v in qat.items() if k not in ('base', 'quant')},
                         {'lora_r': 16, 'lora_alpha': 32, 'target_modules': 'all-linear', 'lr': 1e-4, 'epochs': 2,
                          'max_len': 4096, 'load_in_4bit': True, 'seed': 0})
        self.assertEqual((qat['base'], qat['quant']), ('google/gemma-4-12B-it-qat-q4_0-unquantized', 'Q4_0'))
        self.assertEqual(config('google/gemma-4-12B-it')['quant'], 'Q4_K_M')
        self.assertEqual(set(BASES), {'google/gemma-4-12B-it', 'google/gemma-4-12B-it-qat-q4_0-unquantized'})


class TextRowsTests(unittest.TestCase):
    # 검증 목적: 학습 행이 운영 요청과 같은 system·user를 프롬프트로, 공백 없는 한 줄 JSON(한글 그대로)을 정답으로 넘기고 사고 모드를 끄는지 확인
    # - 정답을 다시 직렬화하면 공백·이스케이프가 바뀌어 운영 xgrammar 문법(공백 없음)과 다른 토큰열을 배움
    # - role·meta 같은 생성기 기록 필드는 학습 입력에 섞이면 안 됨
    def test_to_text_rows_keeps_compact_assistant(self):
        answer = json.dumps({'verdict': 'supported', 'evidence': '나중에 넣은'}, ensure_ascii=False, separators=(',', ':'))
        row = {'role': 'judge', 'meta': {'source_id': 'q1-0'},
               'messages': [{'role': 'system', 'content': '지시문'},
                            {'role': 'user', 'content': '{"sentence": "스택은", "source": "문단"}'},
                            {'role': 'assistant', 'content': answer}]}
        [text] = to_text_rows([row])
        self.assertEqual(text['prompt'], row['messages'][:2])
        self.assertEqual(text['completion'], answer)
        self.assertEqual(text['chat_template_kwargs'], {'enable_thinking': False})
        self.assertEqual(set(text), {'prompt', 'completion', 'chat_template_kwargs'})
        with self.assertRaises(ValueError):
            to_text_rows([{'messages': row['messages'][:2]}])


if __name__ == '__main__':
    unittest.main()
