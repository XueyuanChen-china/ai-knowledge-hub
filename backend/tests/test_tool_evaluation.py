import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agent_tools.evaluation import evaluate_tool_planner, load_tool_evaluation_cases


class ToolEvaluationTests(unittest.TestCase):
    def test_versioned_fixture_measures_tool_selection(self) -> None:
        fixture = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "tool_evaluation"
            / "cases.json"
        )
        cases = load_tool_evaluation_cases(fixture)
        report = evaluate_tool_planner(cases)
        self.assertEqual(report["case_count"], 6)
        self.assertEqual(report["correct_count"], 6)
        self.assertEqual(report["tool_selection_accuracy"], 1.0)

