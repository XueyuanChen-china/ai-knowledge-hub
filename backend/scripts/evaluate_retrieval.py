"""运行固定评测集，并保存 Dense-only 或 Hybrid 检索报告。"""

import argparse
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import retrieval_service, vector_service
from app.services.retrieval_evaluation import (
    evaluate_retrieval_cases,
    load_evaluation_cases,
    save_evaluation_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", type=int, required=True)
    parser.add_argument("--knowledge-base-id", type=int, required=True)
    parser.add_argument("--mode", choices=("dense", "rrf", "hybrid"), required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--cases",
        type=Path,
        default=BACKEND_DIR / "tests" / "fixtures" / "retrieval_evaluation" / "cases.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases = load_evaluation_cases(args.cases)

    original_reranker = retrieval_service.rerank_semantic_hits
    if args.mode == "rrf":
        # RRF 是有价值的消融基线。它不下载 reranker，也不改变生产默认配置。
        retrieval_service.rerank_semantic_hits = lambda _query, hits, *, top_n: hits

    try:
        def retrieve(case):
            if args.mode == "dense":
                return vector_service.search_similar_chunks(
                    args.organization_id,
                    args.knowledge_base_id,
                    case.query,
                    top_k=args.top_k,
                )
            return retrieval_service.retrieve_hybrid_chunks(
                organization_id=args.organization_id,
                knowledge_base_id=args.knowledge_base_id,
                query=case.query,
                top_k=args.top_k,
            )

        report = evaluate_retrieval_cases(cases, retrieve, top_k=args.top_k)
        save_evaluation_report(report, args.output)
    finally:
        retrieval_service.rerank_semantic_hits = original_reranker
    print(
        " ".join(
            [
                f"mode={args.mode}",
                f"cases={report.case_count}",
                f"recall@{args.top_k}={report.recall_at_k:.4f}",
                f"mrr={report.mrr:.4f}",
                f"ndcg@{args.top_k}={report.ndcg_at_k:.4f}",
                f"no_answer_rejection={report.no_answer_rejection_rate:.4f}",
                f"output={args.output}",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
