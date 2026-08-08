import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.retrieval import reranker
from app.services.vector_service import SemanticSearchHit


class FakeReranker:
    def score(self, query: str, contents: list[str]) -> list[float]:
        self.query = query
        self.contents = contents
        return [-2.0, 3.0]


def build_hit(vector_id: str, content: str) -> SemanticSearchHit:
    return SemanticSearchHit(
        vector_id=vector_id,
        chunk_id=1,
        document_id=1,
        knowledge_item_id=1,
        content=content,
        score=0.02,
        metadata={},
        retrieval_sources=("dense", "bm25"),
        rrf_score=0.02,
    )


class RerankerTests(unittest.TestCase):
    def test_reranker_reorders_hits_and_preserves_rrf_evidence(self) -> None:
        fake = FakeReranker()
        ranked = reranker.rerank_semantic_hits(
            "采购复核触发条件",
            [build_hit("background", "制度背景"), build_hit("answer", "金额超过二十万需要复核")],
            top_n=2,
            reranker=fake,
        )

        self.assertEqual(fake.query, "采购复核触发条件")
        self.assertEqual([hit.vector_id for hit in ranked], ["answer", "background"])
        self.assertGreater(ranked[0].rerank_score or 0.0, ranked[1].rerank_score or 0.0)
        self.assertEqual(ranked[0].rrf_score, 0.02)
        self.assertGreater(ranked[0].score, ranked[1].score)
        self.assertIn("rerank_score", ranked[0].metadata)

    def test_reranker_always_scores_candidates(self) -> None:
        fake = FakeReranker()
        hits = [build_hit("first", "第一条"), build_hit("second", "第二条")]

        ranked = reranker.rerank_semantic_hits(
            "问题",
            hits,
            top_n=2,
            reranker=fake,
        )

        self.assertEqual(fake.query, "问题")
        self.assertIsNotNone(ranked[0].rerank_score)


if __name__ == "__main__":
    unittest.main()
