"""单一可替换的 reranker 适配层。"""

from dataclasses import replace
from functools import lru_cache
from math import exp
from typing import Optional, Protocol

from app.config import get_settings
from app.services.vector_service import SemanticSearchHit


class TextReranker(Protocol):
    """最小 reranker 协议，测试可注入确定性 fake。"""

    def score(self, query: str, contents: list[str]) -> list[float]:
        """返回 query 与每个候选正文对应的原始相关性分数。"""


class BgeCrossEncoderReranker:
    """使用 sentence-transformers CrossEncoder 的本地 BGE reranker。"""

    def __init__(self, model_name: str, device: str) -> None:
        try:
            from sentence_transformers import CrossEncoder
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "sentence-transformers is required for the local BGE reranker"
            ) from exc

        self.model = CrossEncoder(model_name, device=device)

    def score(self, query: str, contents: list[str]) -> list[float]:
        # CrossEncoder 默认会对单标签模型应用 Sigmoid。这里显式取原始 logit，
        # 再由 normalize_rerank_score() 统一映射，避免不同模型双重归一化。
        import torch

        pairs = [(query, content) for content in contents]
        scores = self.model.predict(
            pairs,
            show_progress_bar=False,
            activation_fct=torch.nn.Identity(),
        )
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        return [float(score) for score in scores]


@lru_cache
def get_configured_reranker() -> TextReranker:
    """按配置创建唯一的本地 reranker 实例。"""

    settings = get_settings()
    provider = settings.retrieval_reranker_provider.strip().lower()
    if provider != "bge":
        raise ValueError(f"Unsupported retrieval reranker provider: {provider}")
    return BgeCrossEncoderReranker(
        settings.retrieval_reranker_model_name,
        settings.retrieval_reranker_device,
    )


def rerank_semantic_hits(
    query: str,
    hits: list[SemanticSearchHit],
    *,
    top_n: int,
    reranker: Optional[TextReranker] = None,
) -> list[SemanticSearchHit]:
    """对融合后的候选做精排，并保留 RRF 原始证据。

    候选为空时直接返回空列表。生产调用方会捕获模型故障并降级为 RRF，
    因此这个函数本身不吞掉异常，避免真正的配置问题被静默掩盖。
    """

    if not hits:
        return []

    candidate_count = max(1, min(top_n, len(hits)))
    candidates = hits[:candidate_count]
    active_reranker = reranker or get_configured_reranker()
    raw_scores = active_reranker.score(query, [hit.content for hit in candidates])
    if len(raw_scores) != len(candidates):
        raise ValueError("reranker score count does not match candidate count")

    ranked_candidates = []
    for hit, raw_score in zip(candidates, raw_scores):
        normalized_score = normalize_rerank_score(raw_score)
        metadata = dict(hit.metadata)
        metadata["rerank_score"] = normalized_score
        metadata["reranker_raw_score"] = float(raw_score)
        ranked_candidates.append(
            replace(
                hit,
                score=normalized_score,
                metadata=metadata,
                rerank_score=normalized_score,
            )
        )
    ranked_candidates.sort(
        key=lambda hit: (-(hit.rerank_score or 0.0), -(hit.rrf_score or 0.0))
    )
    return ranked_candidates + hits[candidate_count:]


def normalize_rerank_score(raw_score: float) -> float:
    """把 cross-encoder logit 映射到 0~1，供 relevance gate 使用。"""

    bounded = max(min(float(raw_score), 30.0), -30.0)
    return 1.0 / (1.0 + exp(-bounded))
