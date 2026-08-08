import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.retrieval_evaluation import (
    EvaluationCase,
    evaluate_retrieval_cases,
    load_evaluation_cases,
    save_evaluation_report,
)
from app.services.vector_service import SemanticSearchHit


def build_hit(chunk_id: int) -> SemanticSearchHit:
    return SemanticSearchHit(
        vector_id=f"vector-{chunk_id}",
        chunk_id=chunk_id,
        document_id=1,
        knowledge_item_id=1,
        content="evidence",
        score=0.9,
        metadata={},
    )


def build_external_hit(external_id: str) -> SemanticSearchHit:
    return SemanticSearchHit(
        vector_id=f"vector-{external_id}",
        chunk_id=None,
        document_id=None,
        knowledge_item_id=None,
        content="evidence",
        score=0.9,
        metadata={"benchmark_doc_id": external_id},
    )


class RetrievalEvaluationTests(unittest.TestCase):
    def test_fixture_contains_required_question_categories(self) -> None:
        fixture_path = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "retrieval_evaluation"
            / "cases.json"
        )

        cases = load_evaluation_cases(fixture_path)

        self.assertEqual(
            {case.category for case in cases},
            {"factual", "conditional", "process", "summary", "no_answer", "unauthorized"},
        )

    def test_evaluation_reports_recall_mrr_and_no_answer_rejection(self) -> None:
        cases = [
            EvaluationCase("fact", "采购复核金额", {10}, "factual"),
            EvaluationCase("process", "怎么报销", {20}, "process"),
            EvaluationCase("no-answer", "夜班补贴", set(), "no_answer"),
        ]
        results = {
            "fact": [build_hit(99), build_hit(10)],
            "process": [build_hit(20)],
            "no-answer": [],
        }

        report = evaluate_retrieval_cases(cases, lambda case: results[case.case_id], top_k=5)

        self.assertEqual(report.case_count, 3)
        self.assertAlmostEqual(report.recall_at_k, 1.0)
        self.assertAlmostEqual(report.mrr, 0.75)
        self.assertAlmostEqual(report.ndcg_at_k, 0.8154648768, places=6)
        self.assertAlmostEqual(report.no_answer_rejection_rate, 1.0)
        self.assertEqual(report.case_results[0].first_relevant_rank, 2)

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "reports" / "hybrid.json"
            save_evaluation_report(report, output_path)
            self.assertIn('"recall_at_k": 1.0', output_path.read_text(encoding="utf-8"))

    def test_external_qrels_are_matched_by_stable_benchmark_id(self) -> None:
        cases = [
            EvaluationCase(
                "scifact-1",
                "claim",
                set(),
                "external_ir",
                expected_external_ids={"doc-2"},
                relevance_by_external_id={"doc-2": 1.0},
                dataset="BEIR/scifact",
                source_query_id="1",
            )
        ]
        report = evaluate_retrieval_cases(
            cases,
            lambda _case: [build_external_hit("doc-9"), build_external_hit("doc-2")],
            top_k=5,
        )

        self.assertEqual(report.recall_at_k, 1.0)
        self.assertEqual(report.case_results[0].first_relevant_rank, 2)
        self.assertEqual(report.case_results[0].retrieved_evidence_ids, ["doc-9", "doc-2"])


if __name__ == "__main__":
    unittest.main()
