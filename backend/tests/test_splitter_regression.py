import json
import unittest
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.document_splitter.evaluation import (
    build_splitter_regression_snapshot,
    evaluate_splitter_regression_snapshot,
)


FIXTURE_ROOT = BACKEND_DIR / "tests" / "fixtures" / "splitter_regression"
EXPECTED_ROOT = FIXTURE_ROOT / "expected"


class SplitterRegressionTests(unittest.TestCase):
    def load_cases(self) -> list[dict]:
        return json.loads((FIXTURE_ROOT / "cases.json").read_text(encoding="utf-8"))

    def load_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def build_case_snapshot(self, case: dict) -> dict:
        text = ""
        if "path" in case:
            sample_path = FIXTURE_ROOT / case["path"]
            text = sample_path.read_text(encoding="utf-8")

        return build_splitter_regression_snapshot(
            text,
            case["file_type"],
            pdf_path=str(FIXTURE_ROOT / case["pdf_path"]) if "pdf_path" in case else None,
            spreadsheet_path=(
                str(FIXTURE_ROOT / case["spreadsheet_path"])
                if "spreadsheet_path" in case
                else None
            ),
            word_path=str(FIXTURE_ROOT / case["word_path"]) if "word_path" in case else None,
        )

    def test_regression_snapshots_match_expected(self) -> None:
        for case in self.load_cases():
            with self.subTest(case=case["name"]):
                snapshot = self.build_case_snapshot(case)
                expected_snapshot = self.load_json(
                    EXPECTED_ROOT / f"{case['name']}.snapshot.json"
                )

                self.assertEqual(snapshot, expected_snapshot)

    def test_regression_metrics_match_expected(self) -> None:
        for case in self.load_cases():
            with self.subTest(case=case["name"]):
                snapshot = self.build_case_snapshot(case)
                metrics = evaluate_splitter_regression_snapshot(snapshot)
                expected_metrics = self.load_json(
                    EXPECTED_ROOT / f"{case['name']}.metrics.json"
                )

                self.assertEqual(metrics, expected_metrics)

    def test_regression_quality_gates(self) -> None:
        for case in self.load_cases():
            with self.subTest(case=case["name"]):
                snapshot = self.build_case_snapshot(case)
                metrics = evaluate_splitter_regression_snapshot(snapshot)

                self.assertEqual(metrics["oversized_chunk_count"], 0)
                self.assertEqual(metrics["noise_chunk_count"], 0)
                self.assertEqual(metrics["table_fragment_chunk_count"], 0)
                self.assertGreaterEqual(metrics["element_source_parser_coverage_ratio"], 1.0)
                self.assertGreaterEqual(metrics["block_heading_path_coverage_ratio"], 1.0)
                self.assertGreater(metrics["chunk_count"], 0)

                if metrics["heading_prefix_applicable_chunk_count"] > 0:
                    self.assertGreater(metrics["heading_prefix_ratio"], 0)
