import unittest
from unittest.mock import Mock, patch

from app.agent_tools.schemas import ToolExecutionResult
from app.graph import nodes


class ContextRecoveryNodeTests(unittest.TestCase):
    def test_gap_check_marks_explicit_history_reference(self) -> None:
        state = nodes.context_gap_check_node(
            {
                "question": "之前说过的金额门槛是什么？",
                "rewrite_context": {"recent_messages": [], "summary": ""},
                "node_trace": ["START", "router"],
            }
        )

        self.assertTrue(state["context_gap"]["need_recovery"])
        self.assertIn("context_gap_check", state["node_trace"])

    def test_history_recovery_injects_relevant_history_into_contexts(self) -> None:
        result = ToolExecutionResult(
            tool_name="search_conversation_history",
            ok=True,
            data={
                "messages": [
                    {
                        "message_id": 9,
                        "role": "user",
                        "content": "采购复核金额超过二十万元需要委员会复核。",
                        "score": 4.2,
                    }
                ]
            },
        )
        state = {
            "question": "之前说过的金额门槛是什么？",
            "organization_id": 1,
            "user_id": 2,
            "role": "viewer",
            "knowledge_base_id": 3,
            "conversation_id": 4,
            "context_gap": {
                "need_recovery": True,
                "reason": "缺少历史主体",
            },
            "router_context": {"recent_messages": [], "summary": ""},
            "rewrite_context": {"recent_messages": [], "summary": ""},
            "answer_context": {"recent_messages": [], "summary": ""},
            "node_trace": [],
        }
        with patch("app.graph.nodes.execute_readonly_tool", return_value=result):
            updated = nodes.history_recovery_node(state, Mock())

        self.assertTrue(updated["history_recovery_used"])
        self.assertEqual(len(updated["relevant_history"]), 1)
        self.assertEqual(
            updated["answer_context"]["relevant_history"][0]["source_ids"],
            ["9"],
        )
        self.assertEqual(updated.get("tool_call_count", 0), 0)
        self.assertEqual(updated["history_recovery_count"], 1)


if __name__ == "__main__":
    unittest.main()
