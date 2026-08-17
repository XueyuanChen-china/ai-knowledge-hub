"""U8 的统一混合检索入口。"""

import logging
import re
import time
from dataclasses import replace
from typing import Iterable, Optional

from app.config import get_settings
from app.observability.logging import log_event
from app.observability.metrics import get_metrics
from app.services.retrieval.reranker import rerank_semantic_hits
from app.services.vector_service import (
    SemanticSearchHit,
    search_bm25_chunks,
    search_similar_chunks,
)

logger = logging.getLogger(__name__)


def retrieve_hybrid_chunks(
    *,
    organization_id: int,
    knowledge_base_id: int,
    query: str,
    top_k: int = 5,
    query_variants: Optional[list[str]] = None,
) -> list[SemanticSearchHit]:
    """执行 Dense + BM25 + RRF + BGE rerank 的检索主流程。

    query_variants 用于 Query Rewrite：原始 query 始终参与检索，补充 query
    只增加召回范围，最终仍由同一个 reranker 依据原始问题精排。
    """

    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("query must not be empty")

    settings = get_settings()
    started_at = time.perf_counter()
    try:
        queries = [normalized_query]
        for variant in query_variants or []:
            normalized_variant = str(variant or "").strip()
            if normalized_variant and normalized_variant not in queries:
                queries.append(normalized_variant)

        dense_hits: list[SemanticSearchHit] = []
        bm25_hits: list[SemanticSearchHit] = []
        for retrieval_query in queries:
            dense_hits.extend(
                search_similar_chunks(
                    organization_id,
                    knowledge_base_id,
                    retrieval_query,
                    top_k=max(top_k, settings.retrieval_dense_candidate_k),
                )
            )
            bm25_hits.extend(
                search_bm25_chunks(
                    organization_id,
                    knowledge_base_id,
                    retrieval_query,
                    top_k=max(top_k, settings.retrieval_bm25_candidate_k),
                )
            )
        # 先在每一路候选中去重，再做 RRF。否则同一文件的重复上传会占满
        # dense/BM25 的候选窗口，真正相关但只命中一次的章节根本进不了融合阶段。
        dense_hits = deduplicate_retrieval_hits(
            dense_hits,
            top_k=max(top_k, settings.retrieval_dense_candidate_k),
        )
        bm25_hits = deduplicate_retrieval_hits(
            bm25_hits,
            top_k=max(top_k, settings.retrieval_bm25_candidate_k),
        )
        fused_hits = reciprocal_rank_fusion(
            dense_hits,
            bm25_hits,
            rrf_k=settings.retrieval_rrf_k,
            top_k=max(top_k, settings.retrieval_rerank_top_n),
        )

        try:
            reranked_hits = rerank_semantic_hits(
                normalized_query,
                fused_hits,
                top_n=settings.retrieval_rerank_top_n,
            )
        except Exception as exc:
            # reranker 是精排增强，不应让已有 RRF 检索整体不可用。
            log_event(
                logger,
                "retrieval.reranker_fallback",
                level=logging.WARNING,
                knowledge_base_id=knowledge_base_id,
                error_type=type(exc).__name__,
            )
            reranked_hits = fused_hits

        get_metrics().record_operation(
            "hybrid_retrieval",
            time.perf_counter() - started_at,
            outcome="success",
        )
        return deduplicate_retrieval_hits(reranked_hits, top_k=top_k)
    except Exception:
        get_metrics().record_operation(
            "hybrid_retrieval",
            time.perf_counter() - started_at,
            outcome="error",
        )
        raise


def deduplicate_retrieval_hits(
    hits: Iterable[SemanticSearchHit],
    *,
    top_k: int,
) -> list[SemanticSearchHit]:
    """去掉重复上传产生的相同 chunk，避免候选位被副本占满。

    同一个文件可能被上传多次，数据库和 ES 中会有不同的 document/chunk ID，
    但正文完全相同。检索时按“文件名 + 规范化正文”去重，保留当前排序中分数
    最高的一条；不同章节仍然保留，避免把同一文件的有效证据错误合并。
    """

    if top_k < 1:
        return []

    unique_hits: list[SemanticSearchHit] = []
    seen: set[tuple[str, str]] = set()
    for hit in hits:
        filename = str(hit.metadata.get("filename") or "").strip().lower()
        normalized_content = re.sub(r"\s+", "", str(hit.content or "")).strip()
        key = (filename, normalized_content or hit.vector_id)
        if key in seen:
            continue
        seen.add(key)
        unique_hits.append(hit)
        if len(unique_hits) >= top_k:
            break
    return unique_hits


def reciprocal_rank_fusion(
    dense_hits: Iterable[SemanticSearchHit],
    bm25_hits: Iterable[SemanticSearchHit],
    *,
    rrf_k: int,
    top_k: int,
) -> list[SemanticSearchHit]:
    """通过 Reciprocal Rank Fusion 融合两路排名。

    RRF 只关心名次，不直接比较 cosine score 和 BM25 score 的量纲，因此适合这两类
    原始分数不可直接相加的检索器。相同 chunk 按 vector_id 去重。
    """

    if rrf_k < 1:
        raise ValueError("rrf_k must be at least 1")

    fused: dict[str, SemanticSearchHit] = {}
    for source, hits in (("dense", dense_hits), ("bm25", bm25_hits)):
        for rank, hit in enumerate(hits, start=1):
            key = hit.vector_id or f"chunk-{hit.chunk_id}"
            contribution = 1.0 / (rrf_k + rank)
            existing = fused.get(key)
            raw_dense_score = hit.score if source == "dense" else None
            raw_bm25_score = hit.score if source == "bm25" else None
            if existing is None:
                metadata = build_retrieval_metadata(
                    hit.metadata,
                    sources=(source,),
                    dense_score=raw_dense_score,
                    bm25_score=raw_bm25_score,
                    rrf_score=contribution,
                )
                fused[key] = replace(
                    hit,
                    score=contribution,
                    metadata=metadata,
                    retrieval_sources=(source,),
                    dense_score=raw_dense_score,
                    bm25_score=raw_bm25_score,
                    rrf_score=contribution,
                    rerank_score=None,
                )
                continue

            sources = tuple(dict.fromkeys((*existing.retrieval_sources, source)))
            dense_score = (
                existing.dense_score
                if existing.dense_score is not None
                else raw_dense_score
            )
            bm25_score = (
                existing.bm25_score
                if existing.bm25_score is not None
                else raw_bm25_score
            )
            rrf_score = (existing.rrf_score or 0.0) + contribution
            fused[key] = replace(
                existing,
                score=rrf_score,
                metadata=build_retrieval_metadata(
                    existing.metadata,
                    sources=sources,
                    dense_score=dense_score,
                    bm25_score=bm25_score,
                    rrf_score=rrf_score,
                ),
                retrieval_sources=sources,
                dense_score=dense_score,
                bm25_score=bm25_score,
                rrf_score=rrf_score,
            )

    ranked_hits = list(fused.values())
    ranked_hits.sort(key=lambda hit: (-(hit.rrf_score or 0.0), hit.vector_id))
    return ranked_hits[:top_k]


def build_retrieval_metadata(
    metadata: dict,
    *,
    sources: tuple[str, ...],
    dense_score: Optional[float],
    bm25_score: Optional[float],
    rrf_score: float,
) -> dict:
    """把排序证据镜像到 metadata，供 RAG、API 和前端追溯。"""

    result = dict(metadata)
    result["retrieval_sources"] = list(sources)
    result["dense_score"] = dense_score
    result["bm25_score"] = bm25_score
    result["rrf_score"] = rrf_score
    return result
