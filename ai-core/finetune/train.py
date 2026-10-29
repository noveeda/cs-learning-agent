## @file
# @brief 12B 하네스 역할 QLoRA 학습과 BF16 병합 실행 스크립트 (계획 Task 5, cs-train 환경 전용)
# - 입력: Task 3의 train.jsonl·valid.jsonl(messages만 씀). 출력(--out): adapter/, merged/(BF16), losses.json, result.json
# - transformers·peft·bitsandbytes가 있는 ~/.venvs/cs-train에서만 실행. ai-core 테스트는 이 파일을 import하지 않음
# - GGUF 변환·양자화는 llama.cpp로 따로 함(명령은 docs/finetune.md)
#
# 실행(ai-core 폴더):
#   GPU 하나: CUDA_VISIBLE_DEVICES=0 ~/.venvs/cs-train/bin/python -m finetune.train --data <train·valid 폴더> --out <결과 폴더>
#   GPU 둘(DDP): CUDA_VISIBLE_DEVICES=0,1 ~/.venvs/cs-train/bin/torchrun --nproc-per-node 2 -m finetune.train ... --skip-merge
#     그 뒤 병합만: ~/.venvs/cs-train/bin/python -m finetune.train ... --skip-train
#   시험: --limit 200 --valid-limit 50 --epochs 1, 속도 재기: --max-steps 20
import argparse
import json
import os
import random
import shutil
import time
from pathlib import Path

import torch
from huggingface_hub import snapshot_download
from peft import LoraConfig, PeftModel, get_peft_model, prepare_model_for_kbit_training
from transformers import (AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, Trainer, TrainerCallback,
                          TrainingArguments)

from finetune.train_config import BASES, END_OF_TURN, config, to_text_rows

## @brief 'all-linear'을 언어 모델의 선형층으로 좁힌 정규식
# - 바탕 체크포인트는 그림·소리 인코더가 함께 든 멀티모달 모델. 학습은 글만 쓰므로 그쪽 선형층에 어댑터를 달 이유가 없음
# - 출력층(lm_head)은 peft 'all-linear'과 같게 뺌
LANGUAGE_LINEAR = r'.*language_model.*\.(q_proj|k_proj|v_proj|o_proj|gate_proj|up_proj|down_proj)$'


## @brief jsonl 파일을 행 목록으로 읽음
def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


## @brief 프롬프트·정답 쌍 하나를 토큰으로 바꿈; 손실은 정답(JSON + 차례 끝 표시)에만 매기도록 프롬프트 자리 label을 -100으로 둠
# - 프롬프트는 운영과 같이 add_generation_prompt와 사고 끔 인자로 렌더함(빈 thought 칸 포함)
# - 렌더한 글에 <bos>가 이미 있어 토크나이저가 특수 토큰을 다시 붙이지 않게 함
# - 프롬프트와 정답을 따로 토큰화한 결과가 이어 붙인 글의 토큰화와 다르면 None(경계에서 토큰이 갈리면 운영과 다른 토큰열을 배움)
def encode(tokenizer, row: dict) -> dict | None:
    prompt = tokenizer.apply_chat_template(row['prompt'], tokenize=False, add_generation_prompt=True,
                                           **row['chat_template_kwargs'])
    answer = row['completion'] + END_OF_TURN
    prompt_ids = tokenizer(prompt, add_special_tokens=False)['input_ids']
    answer_ids = tokenizer(answer, add_special_tokens=False)['input_ids']
    if tokenizer(prompt + answer, add_special_tokens=False)['input_ids'] != prompt_ids + answer_ids:
        return None
    return {'input_ids': prompt_ids + answer_ids, 'labels': [-100] * len(prompt_ids) + answer_ids}


## @brief 학습·검증 행을 읽어 토큰으로 바꾸고, max_len을 넘거나 경계가 어긋난 행은 빼며 그 수를 셈
# - limit: 시험 학습용. seed로 섞은 뒤 앞에서 limit행만 씀
def load_split(path: Path, tokenizer, max_len: int, seed: int, limit: int | None) -> tuple[list[dict], dict]:
    rows = _read_jsonl(path)
    if limit:
        random.Random(seed).shuffle(rows)
        rows = rows[:limit]
    encoded, dropped = [], {'too_long': 0, 'boundary': 0}
    for row in to_text_rows(rows):
        item = encode(tokenizer, row)
        if item is None:
            dropped['boundary'] += 1
        elif len(item['input_ids']) > max_len:
            dropped['too_long'] += 1
        else:
            encoded.append(item)
    return encoded, dropped


## @brief 길이가 다른 행을 한 묶음으로 채움; 오른쪽을 pad 토큰(label -100)으로 채우고 attention_mask를 만듦
class PadCollator:
    def __init__(self, pad_id: int):
        self.pad_id = pad_id

    def __call__(self, items):
        width = max(len(i['input_ids']) for i in items)
        pad = lambda seq, value: seq + [value] * (width - len(seq))
        return {'input_ids': torch.tensor([pad(i['input_ids'], self.pad_id) for i in items]),
                'labels': torch.tensor([pad(i['labels'], -100) for i in items]),
                'attention_mask': torch.tensor([pad([1] * len(i['input_ids']), 0) for i in items])}


## @brief 정답 자리의 logits만 계산해 손실을 매기는 Trainer
# - 모델 기본 손실은 모든 자리(최대 약 4천)에서 어휘 26만 개 logits를 fp32로 만들어, 배치 2에서도 7GB를 한 번에 잡아 GPU가 모자람
# - 손실은 정답(completion) 자리에만 매기므로 그 자리만 logits_to_keep으로 계산함(계산 결과는 같고 메모리·시간만 줄어듦)
# - 배치 안 행마다 정답 자리가 다르므로, 어느 행이든 다음 토큰이 정답인 자리의 합집합을 계산하고 나머지 행 자리는 -100으로 무시
# - 기울기 누적·DDP 정규화는 Trainer 기본 규칙과 같게 맞춤(num_items_in_batch가 오면 누적 전체 토큰 수로 나눔)
class CompletionLossTrainer(Trainer):
    def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
        labels = inputs.pop('labels')
        targets = labels[:, 1:]
        keep = (targets != -100).any(dim=0).nonzero().squeeze(-1)
        outputs = model(**inputs, logits_to_keep=keep)
        logits = outputs.logits.float()
        picked = targets[:, keep]
        total = torch.nn.functional.cross_entropy(logits.reshape(-1, logits.size(-1)), picked.reshape(-1),
                                                  ignore_index=-100, reduction='sum')
        self.loss_is_scaled_for_ga = num_items_in_batch is not None
        if num_items_in_batch is None:
            loss = total / (picked != -100).sum()
        else:
            loss = total / num_items_in_batch
            if self.args.average_tokens_across_devices:
                loss = loss * self.accelerator.num_processes
        return (loss, outputs) if return_outputs else loss


## @brief 검증 손실이 2회 연속 오르면 학습을 멈추는 콜백(계획 Task 5 중단 규칙)
# - 최저값 기준(EarlyStopping)이 아니라 직전 값 대비 연속 상승만 봄: 잠깐 오른 뒤 다시 내려가는 경우는 계속 학습
class StopOnRisingEval(TrainerCallback):
    def __init__(self, times: int = 2):
        self.times, self.losses = times, []

    def on_evaluate(self, args, state, control, metrics=None, **kwargs):
        self.losses.append(metrics['eval_loss'])
        recent = self.losses[-(self.times + 1):]
        if len(recent) == self.times + 1 and all(a < b for a, b in zip(recent, recent[1:])):
            control.should_training_stop = True


## @brief 4bit 바탕 모델에 LoRA를 달아 학습하고, 검증 손실이 가장 낮았던 어댑터를 out/adapter에 저장함
# - 반환: result.json에 남길 수치(행 수, 뺀 행, 걸린 시간, 초당 토큰, GPU 최대 메모리, 마지막 손실)
# - GPU 여러 장(torchrun)이면 프로세스마다 자기 GPU(LOCAL_RANK)에 모델 한 벌을 올리는 DDP
# - 유효 배치(batch × accum × GPU 수)는 GPU 수·배치와 상관없이 effective_batch(16)로 고정: 속도만 바꾸고 학습 설정은 같게 함
# - 길이가 비슷한 행끼리 묶어(group_by_length) 배치를 키워도 패딩으로 버리는 계산이 적게 함
#   (여러 행을 한 줄로 이어 붙이는 packing·padding_free는 gemma 4 어텐션에서 정확성을 확인하지 않아 쓰지 않음)
def train(args, cfg: dict, tokenizer) -> dict:
    world = int(os.environ.get('WORLD_SIZE', 1))
    if args.effective_batch % (args.batch * world):
        raise ValueError(f'유효 배치 {args.effective_batch}이 batch {args.batch} × GPU {world}로 나누어떨어지지 않습니다')
    accum = args.effective_batch // (args.batch * world)
    train_rows, train_dropped = load_split(args.data / 'train.jsonl', tokenizer, cfg['max_len'], cfg['seed'], args.limit)
    valid_rows, valid_dropped = load_split(args.data / 'valid.jsonl', tokenizer, cfg['max_len'], cfg['seed'],
                                           args.valid_limit)
    print(f'학습 {len(train_rows)}행(뺀 행 {train_dropped}), 검증 {len(valid_rows)}행(뺀 행 {valid_dropped})', flush=True)

    quant = BitsAndBytesConfig(load_in_4bit=cfg['load_in_4bit'], bnb_4bit_quant_type='nf4', bnb_4bit_use_double_quant=True,
                               bnb_4bit_compute_dtype=torch.bfloat16)
    model = AutoModelForCausalLM.from_pretrained(cfg['base'], quantization_config=quant, dtype=torch.bfloat16,
                                                 device_map={'': int(os.environ.get('LOCAL_RANK', 0))})
    model = prepare_model_for_kbit_training(model, gradient_checkpointing_kwargs={'use_reentrant': False})
    targets = LANGUAGE_LINEAR if cfg['target_modules'] == 'all-linear' else cfg['target_modules']
    model = get_peft_model(model, LoraConfig(r=cfg['lora_r'], lora_alpha=cfg['lora_alpha'], lora_dropout=0.05,
                                             target_modules=targets, task_type='CAUSAL_LM'))
    model.print_trainable_parameters()

    epochs = args.epochs or cfg['epochs']
    steps = args.max_steps or epochs * len(train_rows) // args.effective_batch
    training_args = TrainingArguments(
        output_dir=str(args.out / 'checkpoints'), num_train_epochs=epochs, learning_rate=cfg['lr'], seed=cfg['seed'],
        per_device_train_batch_size=args.batch, per_device_eval_batch_size=args.batch,
        gradient_accumulation_steps=accum, warmup_steps=max(1, steps // 30), lr_scheduler_type='linear',
        max_steps=args.max_steps or -1, train_sampling_strategy='group_by_length',
        include_num_input_tokens_seen='non_padding', ddp_find_unused_parameters=False,
        bf16=True, gradient_checkpointing=True, gradient_checkpointing_kwargs={'use_reentrant': False},
        logging_steps=10, eval_strategy='steps', eval_steps=args.eval_steps, save_strategy='steps',
        save_steps=args.eval_steps, save_total_limit=2, load_best_model_at_end=True, metric_for_best_model='eval_loss',
        greater_is_better=False, report_to='none', remove_unused_columns=False, prediction_loss_only=True)
    trainer = CompletionLossTrainer(model=model, args=training_args, train_dataset=train_rows, eval_dataset=valid_rows,
                                    data_collator=PadCollator(tokenizer.pad_token_id), callbacks=[StopOnRisingEval()])
    torch.cuda.reset_peak_memory_stats()
    started = time.time()
    trainer.train()
    elapsed = time.time() - started
    final = trainer.evaluate()
    trainer.save_model(str(args.out / 'adapter'))
    memory = round(torch.cuda.max_memory_reserved() / 2**20)
    print(f'GPU {os.environ.get("LOCAL_RANK", 0)} 최대 메모리(reserved) {memory}MiB', flush=True)
    if trainer.is_world_process_zero():
        (args.out / 'losses.json').write_text(json.dumps(trainer.state.log_history, indent=1) + '\n', encoding='utf-8')
    return {'train_rows': len(train_rows), 'valid_rows': len(valid_rows), 'dropped': {'train': train_dropped,
            'valid': valid_dropped}, 'gpus': world, 'batch': args.batch, 'accum': accum, 'epochs': epochs,
            'steps': trainer.state.global_step, 'tokens_seen': trainer.state.num_input_tokens_seen,
            'tokens_per_second': round(trainer.state.num_input_tokens_seen / elapsed),
            'is_main': trainer.is_world_process_zero(),
            'best_checkpoint': trainer.state.best_model_checkpoint, 'best_eval_loss': trainer.state.best_metric,
            'final_eval_loss': final['eval_loss'], 'train_seconds': round(elapsed),
            'max_memory_allocated_mib': round(torch.cuda.max_memory_allocated() / 2**20),
            'max_memory_reserved_mib': memory}


## @brief BF16 바탕 모델에 어댑터를 합쳐 out/merged에 저장하고, 대화 템플릿·토크나이저 파일을 바탕 체크포인트에서 그대로 복사함
# - CPU에서 합침: 12B BF16(약 24GB)과 학습 중간 파일이 GPU 메모리를 다투지 않게 함
# - 바탕 체크포인트와 같은 클래스·가중치 이름으로 저장해 llama.cpp convert_hf_to_gguf.py가 원본처럼 읽게 함
# - 토크나이저·템플릿은 다시 저장하지 않고 파일째 복사: 글자 하나 달라지지 않게 하고, 프로세서 저장에 필요한 torchvision 없이 됨
def merge(args, cfg: dict) -> dict:
    started = time.time()
    base = AutoModelForCausalLM.from_pretrained(cfg['base'], dtype=torch.bfloat16, device_map='cpu')
    merged = PeftModel.from_pretrained(base, str(args.out / 'adapter')).merge_and_unload()
    merged.save_pretrained(str(args.out / 'merged'), safe_serialization=True, max_shard_size='5GB')
    snapshot = Path(snapshot_download(cfg['base'], local_files_only=True))
    for name in ('tokenizer.json', 'tokenizer_config.json', 'chat_template.jinja', 'processor_config.json',
                 'generation_config.json'):
        if (snapshot / name).exists():
            shutil.copyfile(snapshot / name, args.out / 'merged' / name)
    return {'merge_seconds': round(time.time() - started), 'base_revision': snapshot.name}


## @brief 명령 진입점: 학습(--skip-train이 아니면) → 병합(--skip-merge가 아니면) 순서로 실행하고 조건·수치를 result.json에 씀
def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m finetune.train', description='12B 하네스 역할 QLoRA 학습·병합')
    parser.add_argument('--data', type=Path, required=True, help='train.jsonl·valid.jsonl이 있는 폴더')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--base', choices=BASES, default='google/gemma-4-12B-it-qat-q4_0-unquantized')
    parser.add_argument('--limit', type=int, help='시험 학습: 학습 행 수')
    parser.add_argument('--valid-limit', type=int, help='시험 학습: 검증 행 수')
    parser.add_argument('--epochs', type=int, help='에폭 수(없으면 설정값)')
    parser.add_argument('--batch', type=int, default=2, help='GPU 하나가 한 번에 넣는 행 수')
    parser.add_argument('--effective-batch', type=int, default=16,
                        help='유효 배치(batch × 기울기 누적 × GPU 수). 누적 횟수는 여기에 맞춰 정함')
    parser.add_argument('--max-steps', type=int, help='속도 재기: 이 최적화 단계 수만 돌고 멈춤')
    parser.add_argument('--eval-steps', type=int, default=100, help='검증·저장 간격(최적화 단계)')
    parser.add_argument('--skip-train', action='store_true', help='이미 있는 adapter로 병합만 함')
    parser.add_argument('--skip-merge', action='store_true')
    args = parser.parse_args(argv)
    cfg = config(args.base)
    args.out.mkdir(parents=True, exist_ok=True)
    result_path = args.out / 'result.json'
    result = json.loads(result_path.read_text(encoding='utf-8')) if result_path.exists() else {}
    result.update(config=cfg, effective_batch=args.effective_batch, eval_steps=args.eval_steps, limit=args.limit,
                  valid_limit=args.valid_limit, max_steps=args.max_steps)
    if not args.skip_train:
        result.update(train(args, cfg, AutoTokenizer.from_pretrained(cfg['base'])))
        if not result.pop('is_main'):  # DDP에서 기록·병합은 0번 프로세스만 함
            return
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        shutil.rmtree(args.out / 'checkpoints', ignore_errors=True)
        torch.cuda.empty_cache()
    if int(os.environ.get('WORLD_SIZE', 1)) > 1:
        print('DDP 실행에서는 병합하지 않음: 끝난 뒤 --skip-train으로 병합만 따로 실행', flush=True)
    elif not args.skip_merge:
        result.update(merge(args, cfg))
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
