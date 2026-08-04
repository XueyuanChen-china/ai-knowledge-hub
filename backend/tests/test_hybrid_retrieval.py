import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import retrieval_service
from app.services.vector_service import SemanticSearchHit


def build_hit(vector_id: str, chunk_id: int, score: float, content: str) -> SemanticSearchHit:
    return SemanticSearchHit(
        vector_id=vector_id,
        chunk_id=chunk_id,
        document_id=1,
        knowledge_item_id=1,
        content=content,
        score=score,
        metadata={"filename": "policy.md"},
    )


class HybridRetrievalTests(unittest.TestCase):
    def test_rrf_deduplicates_hits_and_keeps_raw_retrieval_evidence(self) -> None:
        dense_hits = [
            build_hit("shared", 10, 0.91, "采购复核金额条件"),
            build_hit("dense-only", 11, 0.88, "语义相近背景"),
        ]
        bm25_hits = [
            build_hit("bm25-only", 12, 14.2, "采购复核触发条件"),
            build_hit("shared", 10, 11.4, "采购复核金额条件"),
        ]

        fused = retrieval_service.reciprocal_rank_fusion(
            dense_hits,
            bm25_hits,
            rrf_k=60,
            top_k=5,
        )

        self.assertEqual([hit.vector_id for hit in fused], ["shared", "bm25-only", "dense-only"])
        shared = fused[0]
        self.assertEqual(shared.retrieval_sources, ("dense", "bm25"))
        self.assertEqual(shared.dense_score, 0.91)
        self.assertEqual(shared.bm25_score, 11.4)
        self.assertGreater(shared.rrf_score or 0.0, 0.0)
        self.assertEqual(shared.metadata["retrieval_sources"], ["dense", "bm25"])

    def test_rrf_preserves_a_zero_raw_score_instead_of_treating_it_as_missing(self) -> None:
        fused = retrieval_service.reciprocal_rank_fusion(
            [build_hit("shared", 10, 0.0, "Dense candidate")],
            [build_hit("shared", 10, 8.0, "BM25 candidate")],
            rrf_k=60,
            top_k=5,
        )

        self.assertEqual(fused[0].dense_score, 0.0)
        self.assertEqual(fused[0].bm25_score, 8.0)

    def test_hybrid_retrieval_applies_the_same_scope_to_both_retrievers(self) -> None:
        captured = {}
        original_dense = retrieval_service.search_similar_chunks
        original_bm25 = retrieval_service.search_bm25_chunks
        original_rerank = retrieval_service.rerank_semantic_hits
        original_settings = retrieval_service.get_settings
        try:
            def fake_dense(organization_id, knowledge_base_id, query, *, top_k):
                captured["dense"] = (organization_id, knowledge_base_id, query, top_k)
                return [build_hit("dense", 10, 0.9, "采购复核")]

            def fake_bm25(organization_id, knowledge_base_id, query, *, top_k):
                captured["bm25"] = (organization_id, knowledge_base_id, query, top_k)
                return [build_hit("bm25", 11, 8.0, "采购复核触发条件")]

            retrieval_service.search_similar_chunks = fake_dense
            retrieval_service.search_bm25_chunks = fake_bm25
            retrieval_service.rerank_semantic_hits = lambda query, hits, *, top_n: hits
            retrieval_service.get_settings = lambda: SimpleNamespace(
                retrieval_dense_candidate_k=8,
                retrieval_bm25_candidate_k=9,
                retrieval_rrf_k=60,
                retrieval_rerank_top_n=10,
            )

            hits = retrieval_service.retrieve_hybrid_chunks(
                organization_id=7,
                knowledge_base_id=3,
                query="采购复核的触发条件",
                top_k=3,
            )
        finally:
            retrieval_service.search_similar_chunks = original_dense
            retrieval_service.search_bm25_chunks = original_bm25
            retrieval_service.rerank_semantic_hits = original_rerank
            retrieval_service.get_settings = original_settings

        self.assertEqual(captured["dense"], (7, 3, "采购复核的触发条件", 8))
        self.assertEqual(captured["bm25"], (7, 3, "采购复核的触发条件", 9))
        self.assertEqual(len(hits), 2)

    def test_hybrid_retrieval_falls_back_to_rrf_when_reranker_is_unavailable(self) -> None:
        original_dense = retrieval_service.search_similar_chunks
        original_bm25 = retrieval_service.search_bm25_chunks
        original_rerank = retrieval_service.rerank_semantic_hits
        original_settings = retrieval_service.get_settings
        try:
            retrieval_service.search_similar_chunks = lambda *args, **kwargs: [
                build_hit("dense", 10, 0.9, "采购复核")
            ]
            retrieval_service.search_bm25_chunks = lambda *args, **kwargs: []
            retrieval_service.rerank_semantic_hits = lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("model unavailable")
            )
            retrieval_service.get_settings = lambda: SimpleNamespace(
                retrieval_dense_candidate_k=5,
                retrieval_bm25_candidate_k=5,
                retrieval_rrf_k=60,
                retrieval_rerank_top_n=5,
            )

            hits = retrieval_service.retrieve_hybrid_chunks(
                organization_id=7,
                knowledge_base_id=3,
                query="采购复核",
                top_k=3,
            )
        finally:
            retrieval_service.search_similar_chunks = original_dense
            retrieval_service.search_bm25_chunks = original_bm25
            retrieval_service.rerank_semantic_hits = original_rerank
            retrieval_service.get_settings = original_settings

        self.assertEqual([hit.vector_id for hit in hits], ["dense"])
        self.assertEqual(hits[0].rerank_score, None)
        self.assertGreater(hits[0].rrf_score or 0.0, 0.0)


if __name__ == "__main__":
    unittest.main()
