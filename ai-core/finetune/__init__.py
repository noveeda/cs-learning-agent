## @file
# @brief 12B 하네스 역할 미세조정 패키지: 학습·측정 분리(split) 규칙
# - 학습 데이터·어댑터·가중치는 Git 밖(ai-core/outputs/finetune/, ~/.cache/cs-llm/finetune/)에 두고, 저장소에는 다시 만드는 코드와 제외 개념 목록만 둠
# - ai-core 테스트·CI는 trl·peft 없이 돌아야 하므로, 이 패키지 모듈은 학습 라이브러리를 최상위에서 import하지 않음
