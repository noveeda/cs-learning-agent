## @file
# @brief 12B 하네스 역할 미세조정용 데이터 준비 패키지(학습·측정 분리 규칙, 학습 예시 형식, 26B 호출 녹화)
# - 학습 데이터·어댑터·가중치는 Git 밖(ai-core/outputs/finetune/, ~/.cache/cs-llm/finetune/)에 두고, 저장소에는 다시 만드는 코드와 제외 개념 목록만 둠
# - 학습 자체(trl·peft)는 이 패키지가 import하지 않음: ai-core 테스트와 CI가 학습 라이브러리 없이 돌게 하기 위함
