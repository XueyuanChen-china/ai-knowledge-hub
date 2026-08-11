import json
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agent_tools.schemas import ToolExecutionResult
from app.services.tool_context_policy import (
    build_tool_result_ref,
    compact_tool_refs,
)


class ToolContextPolicyTests(unittest.TestCase):
    def test_tool_result_is_projected_to_summary_and_source_ids(self) -> None:
        result = ToolExecutionResult(
            tool_name="get_document",
            ok=True,
            data={"filename": "policy.pdf", "content": "采购复核超过二十万元。" * 100},
            citations=[{"doc_id": 8, "chunk_id": 51}],
        )

        ref = build_tool_result_ref(result, result_ref="tool-result-1")

        self.assertEqual(ref.result_ref, "tool-result-1")
        self.assertLess(len(ref.summary), len(result.data["content"]))
        self.assertEqual(ref.source_ids, ["chunk_id:51", "doc_id:8"])
        self.assertNotIn(result.data["content"], ref.summary)

    def test_compaction_keeps_pinned_or_cited_refs(self) -> None:
        refs = [
            build_tool_result_ref(
                ToolExecutionResult(tool_name="search", ok=True, data={"value": index}),
                result_ref=f"ref-{index}",
            )
            for index in range(5)
        ]
        refs[0].importance = "pinned"
        refs[1].citation_used = True

        kept, removed = compact_tool_refs(refs, max_items=2)

        self.assertIn("ref-0", {item.result_ref for item in kept})
        self.assertIn("ref-1", {item.result_ref for item in kept})
        self.assertTrue(removed)


if __name__ == "__main__":
    unittest.main()
