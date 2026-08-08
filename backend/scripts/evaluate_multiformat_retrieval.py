#!/usr/bin/env python3
"""评估已索引的多格式知识库检索质量。

这个脚本只评估搜索层，不负责上传或重新索引文件。它使用固定的 40 条问题集，
统计不同问题类型的文档召回、MRR、关键词支持和请求延迟，并把结果保存成 JSON。
"""

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = BACKEND_DIR / "tests" / "fixtures" / "multiformat_e2e"


def request_json(
    base_url: str,
    path: str,
    payload: dict[str, Any],
    access_token: str,
) -> Any:
    """调用搜索 API，并统一处理鉴权和 HTTP 错误。"""

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def percentile(values: list[float], ratio: float) -> float:
    """计算一个简单的线性插值分位数，避免引入额外依赖。"""

    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * ratio
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * weight, 2)


def evaluate_query(
    *,
    base_url: str,
    knowledge_base_id: int,
    case: dict[str, Any],
    top_k: int,
    access_token: str,
) -> dict[str, Any]:
    """执行一条问题并返回可序列化的评估结果。"""

    started_at = time.perf_counter()
    results = request_json(
        base_url,
        "/search/semantic",
        {
            "knowledge_base_id": knowledge_base_id,
            "query": case["query"],
            "top_k": top_k,
        },
        access_token,
    )
    latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
    results = list(results or [])[:top_k]

    expected_files = set(case.get("expected_document_keys") or [])
    manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    filename_by_key = {
        item["document_key"]: item["filename"] for item in manifest["documents"]
    }
    expected_filenames = {
        filename_by_key[key] for key in expected_files if key in filename_by_key
    }
    returned_titles = [str(item.get("title") or "") for item in results]
    relevant_ranks = [
        index
        for index, title in enumerate(returned_titles, start=1)
        if any(filename in title for filename in expected_filenames)
    ]

    combined_text = " ".join(
        f"{item.get('title', '')} {item.get('content_preview', '')}"
        for item in results
    )
    expected_keywords = [str(item) for item in case.get("expected_keywords") or []]
    keyword_hits = [keyword for keyword in expected_keywords if keyword in combined_text]
    is_no_answer = case["category"] == "no_answer"
    is_permission_case = case["category"] == "permission"

    return {
        "query_id": case["query_id"],
        "category": case["category"],
        "query": case["query"],
        "latency_ms": latency_ms,
        "result_count": len(results),
        "expected_files": sorted(expected_filenames),
        "returned_titles": returned_titles,
        "first_relevant_rank": relevant_ranks[0] if relevant_ranks else None,
        "document_recall_hit": bool(relevant_ranks),
        "mrr": 1 / relevant_ranks[0] if relevant_ranks else 0.0,
        "expected_keywords": expected_keywords,
        "matched_keywords": keyword_hits,
        "keyword_support_rate": (
            len(keyword_hits) / len(expected_keywords) if expected_keywords else None
        ),
        # 搜索 API 是召回层；no-answer 的最终拒答由 relevance gate / Chat 流程决定。
        "no_answer_retrieval_hit": is_no_answer and bool(results),
        "permission_case_requires_isolated_org_test": is_permission_case,
    }


def aggregate(results: list[dict[str, Any]], top_k: int) -> dict[str, Any]:
    """生成总指标和按问题类型拆分的指标。"""

    answerable = [
        item for item in results if item["category"] not in {"no_answer", "permission"}
    ]
    no_answer = [item for item in results if item["category"] == "no_answer"]
    latencies = [float(item["latency_ms"]) for item in results]

    def summary(items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "query_count": len(items),
            "document_recall_at_k": round(
                sum(item["document_recall_hit"] for item in items) / len(items), 4
            )
            if items
            else 0.0,
            "mrr": round(sum(item["mrr"] for item in items) / len(items), 4)
            if items
            else 0.0,
            "keyword_support_rate": round(
                statistics.mean(
                    item["keyword_support_rate"]
                    for item in items
                    if item["keyword_support_rate"] is not None
                ),
                4,
            )
            if any(item["keyword_support_rate"] is not None for item in items)
            else None,
        }

    categories = sorted({item["category"] for item in results})
    return {
        "top_k": top_k,
        "query_count": len(results),
        "answerable_query_count": len(answerable),
        "answerable": summary(answerable),
        "no_answer": {
            "query_count": len(no_answer),
            "retrieval_hit_count": sum(item["no_answer_retrieval_hit"] for item in no_answer),
            "retrieval_hit_rate": round(
                sum(item["no_answer_retrieval_hit"] for item in no_answer) / len(no_answer),
                4,
            )
            if no_answer
            else 0.0,
        },
        "permission": {
            "query_count": sum(item["category"] == "permission" for item in results),
            "note": "权限越权需要用两个组织的独立账号和知识库验证，不能用单组织搜索结果判定。",
        },
        "by_category": {category: summary([item for item in results if item["category"] == category]) for category in categories},
        "latency_ms": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "max": round(max(latencies), 2) if latencies else 0.0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-base-id", type=int, required=True)
    parser.add_argument("--organization-id", type=int, default=None, help="仅作为报告标识保存")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--access-token", default=os.getenv("E2E_ACCESS_TOKEN", ""))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--queries", type=Path, default=FIXTURE_ROOT / "queries.json")
    args = parser.parse_args()
    if not args.access_token:
        raise SystemExit("missing --access-token or E2E_ACCESS_TOKEN")

    cases = json.loads(args.queries.read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        result = evaluate_query(
            base_url=args.base_url,
            knowledge_base_id=args.knowledge_base_id,
            case=case,
            top_k=args.top_k,
            access_token=args.access_token,
        )
        results.append(result)
        print(
            f"{index}/{len(cases)} {result['query_id']} "
            f"latency_ms={result['latency_ms']} "
            f"results={result['result_count']} "
            f"hit={result['document_recall_hit']}"
        )

    report = {
        "dataset": "multiformat-enterprise-e2e-v1",
        "knowledge_base_id": args.knowledge_base_id,
        "organization_id": args.organization_id,
        "mode": "dense + BM25 + RRF + rerank",
        "summary": aggregate(results, args.top_k),
        "cases": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, TimeoutError, urllib.error.URLError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
