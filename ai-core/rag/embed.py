## @file
# @brief 질문과 교재 문단을 OpenAI 호환 임베딩 주소(llama-server)로 벡터로 바꾸는 임베더
# - 색인(rag.index)은 문단에 embed_passages를, 검색(rag.search)은 질문에 embed_queries를 씀
# - 결과를 L2 정규화해 두므로 검색은 내적만으로 코사인 유사도를 얻음
# - 다국어 임베딩 모델 중 일부(e5 등)는 질문·문단 앞에 다른 접두어를 요구하므로 둘을 따로 받음. 모델은 Task 4 측정으로 정함
import math
import os

from openai import OpenAI

# 요청 한 번에 보내는 문장 수. llama-server의 한 번 처리 한도(batch)를 넘지 않게 나눠 보냄
BATCH = 32


## @brief 임베딩 서버 한 곳과 모델 하나에 묶인 임베더
# - client: 테스트용 가짜 클라이언트 주입. 없으면 base_url로 OpenAI 클라이언트를 만듦
class Embedder:
    def __init__(self, base_url: str, model: str, query_prefix: str = '', passage_prefix: str = '', client=None):
        self.model = model
        self.query_prefix = query_prefix
        self.passage_prefix = passage_prefix
        self.client = client or OpenAI(base_url=base_url, api_key=os.getenv('RAG_EMBED_API_KEY', 'not-needed'))

    ## @brief 환경 변수(RAG_EMBED_URL·RAG_EMBED_MODEL·RAG_EMBED_QUERY_PREFIX·RAG_EMBED_PASSAGE_PREFIX)로 임베더를 만듦
    # - 기본 주소는 측정·운영용 임베딩 서버 포트 20310
    @classmethod
    def from_env(cls):
        return cls(os.getenv('RAG_EMBED_URL', 'http://127.0.0.1:20310/v1'), os.getenv('RAG_EMBED_MODEL', 'embedding'),
                   os.getenv('RAG_EMBED_QUERY_PREFIX', ''), os.getenv('RAG_EMBED_PASSAGE_PREFIX', ''))

    ## @brief 학생 질문 목록을 질문용 접두어를 붙여 정규화 벡터 목록으로 바꿈
    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return self._embed([self.query_prefix + t for t in texts])

    ## @brief 교재 문단·그림 캡션 목록을 문단용 접두어를 붙여 정규화 벡터 목록으로 바꿈
    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return self._embed([self.passage_prefix + t for t in texts])

    ## @brief BATCH개씩 나눠 embeddings.create를 부르고 입력 순서대로 L2 정규화한 벡터를 모음
    # - 응답 data는 index로 다시 정렬함. 서버가 순서를 바꿔 돌려줘도 문단과 벡터가 어긋나지 않게 함
    def _embed(self, texts):
        vectors = []
        for start in range(0, len(texts), BATCH):
            response = self.client.embeddings.create(model=self.model, input=texts[start:start + BATCH])
            for item in sorted(response.data, key=lambda d: d.index):
                norm = math.sqrt(sum(x * x for x in item.embedding)) or 1.0
                vectors.append([x / norm for x in item.embedding])
        return vectors
