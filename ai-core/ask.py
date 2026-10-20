from openai import OpenAI

# OpenAI 클라이언트 객체 생성
client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="not-needed",
)

# 서버가 제공하는 모델명 조회
model = client.models.list().data[0].id

stream = client.chat.completions.create(
    model=model,
    messages=[
        {
            "role": "system",
            "content": "당신은 CS(Computer Science) 학습 조교입니다. 한국어로 정확하고 이해하기 쉽게 설명하세요. 자신의 역할을 기억하되, 사용자에게 본인의 역할을 강조하지 마세요. 사용 언어는 한국어, 영어 두가지만 강제합니다.",
        },
        {
            "role": "user",
            "content": "PagedAttention에 대해 설명해줘"
        },
    ],
    stream=True,
    temperature=0.5,
    extra_body={
        "chat_template_kwargs": {"enable_thinking" : False},
    }
)

for chunk in stream:
    if chunk.choices:
        text = chunk.choices[0].delta.content
        if text:
            print(text, end="", flush=True)
