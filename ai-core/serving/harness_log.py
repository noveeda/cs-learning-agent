## @file
# @brief 근거 검사 하네스의 질문별 단계 기록을 날짜별 JSONL로 남김(spec 5-7 기록·측정)
# - 기록 항목(고른 개념, 문단 ID와 유사도, 생성 문장, 판정과 근거 구절, 수정 횟수, 단계별 소요 시간)은 harness.answer가 채움
# - 학습자 질문이 들어 있으므로 Git 밖 ai-core/outputs/harness-logs/에만 둠(outputs/는 gitignore)
import json
from datetime import date
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[1] / 'outputs' / 'harness-logs'


## @brief 기록 폴더 하나에 하루 한 파일(YYYY-MM-DD.jsonl)로 덧붙여 쓰는 기록기
class HarnessLog:
    def __init__(self, dir: Path = LOG_DIR):
        self.dir = Path(dir)

    ## @brief 기록 한 건을 오늘 날짜 파일 끝에 JSON 한 줄로 추가
    # - 한 줄씩 덧붙이므로 측정 스크립트가 쓰는 중인 파일도 앞부분은 그대로 읽을 수 있음
    def write(self, record: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / f'{date.today().isoformat()}.jsonl', 'a', encoding='utf-8') as file:
            file.write(json.dumps(record, ensure_ascii=False) + '\n')
