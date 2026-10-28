## @file
# @brief 검색 측정 세트로 임베딩 모델의 정답 문단 상위 k개 회수율과 정답 점수 분포를 재는 도구(계획 Task 4, spec 8-2)
# - 실행: python -m eval.harness_retrieval --set ai-core/outputs/harness-sets/retrieval.jsonl
#   → 현재 색인(RAG_DATA_DIR/index.sqlite)과 환경 변수 임베더(RAG_EMBED_*)로 잼. 후보 모델마다 색인을 다시 만들고 반복함
# - 회수율은 운영 검색기(rag.search.Retriever.passages)를 그대로 거쳐 잼. 개념 꼬리표 거르기와 라이선스 계열 규칙까지 포함한 값임
# - 정답 점수 분포의 하위 5% 값을 RAG_MIN_SCORE 후보로 출력함. 이 값을 쓰면 정답 문단의 약 95%가 기준을 넘음
"""Retrieval recall@k and gold-score distribution for the harness retrieval set."""
import argparse
import json
import sqlite3
import statistics
import sys
from array import array
from pathlib import Path


## @brief 행마다 검색기가 행의 개념으로 고른 상위 k개 안에 정답 문단이 있는 비율(행이 없으면 0.0)
def recall_at_k(rows, retriever, k: int = 3) -> float:
    if not rows:
        return 0.0
    hits = sum(row['gold_chunk'] in {p.id for p in retriever.passages([row['concept']], row['question'], k=k)}
               for row in rows)
    return hits / len(rows)


## @brief 행마다 질문과 정답 문단의 코사인 유사도(색인에 저장된 정답 벡터와 질문 벡터의 내적)
# - 검색 순위와 무관하게 정답 문단 점수만 봄. RAG_MIN_SCORE를 정답 분포에서 정하려면 순위 밖 정답의 점수도 필요함
# - 색인에 없는 정답 문단(대응표가 바뀐 경우)은 건너뜀
def gold_scores(rows, retriever) -> list[float]:
    with sqlite3.connect(f'{Path(retriever.db_path).as_uri()}?mode=ro', uri=True) as db:
        vectors = {row['gold_chunk']: db.execute('SELECT vector FROM chunks WHERE id = ?', (row['gold_chunk'],)).fetchone()
                   for row in rows}
    rows = [row for row in rows if vectors[row['gold_chunk']]]
    queries = retriever.embedder.embed_queries([row['question'] for row in rows])
    return [sum(a * b for a, b in zip(q, array('f', vectors[row['gold_chunk']][0]))) for row, q in zip(rows, queries)]


## @brief 측정 명령 진입점: 세트 파일을 읽어 회수율·정답 점수 요약을 JSON 한 줄로 출력
# - 회수율은 min_score를 끈(-1.0) 검색기로 잼. 기준값은 이 측정의 결과로 정하는 값이라 측정에 미리 넣지 않음
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m eval.harness_retrieval', description='검색 회수율을 잽니다.')
    parser.add_argument('--set', type=Path, required=True)
    parser.add_argument('--db', type=Path)
    parser.add_argument('--k', type=int, default=3)
    args = parser.parse_args(argv)
    from rag.embed import Embedder
    from rag.fetch import DATA_DIR
    from rag.search import Retriever

    rows = [json.loads(line) for line in args.set.read_text().splitlines() if line.strip()]
    retriever = Retriever(args.db or DATA_DIR / 'index.sqlite', Embedder.from_env(), min_score=-1.0)
    scores = sorted(gold_scores(rows, retriever))
    print(json.dumps({'rows': len(rows), f'recall_at_{args.k}': round(recall_at_k(rows, retriever, args.k), 4),
                      'gold_score_p5': round(statistics.quantiles(scores, n=20)[0], 4),
                      'gold_score_median': round(statistics.median(scores), 4),
                      'gold_score_min': round(scores[0], 4)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
