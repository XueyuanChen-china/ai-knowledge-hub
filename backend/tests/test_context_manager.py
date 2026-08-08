import sys
import unittest
from unittest.mock import patch
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.context_budget import estimate_tokens
from app.services.context_manager import build_context_pack, build_conversation_contexts
from app.services.context_manager import summarize_conversation_with_llm
from app.db.models import Message
from app.services.context_types import EvidenceItem


class ContextManagerTests(unittest.TestCase):
    def test_context_pack_keeps_recent_messages_in_order(self) -> None:
        messages = [
            {"role": "user", "content": f"历史消息 {index}"}
            for index in range(10)
        ]

        pack = build_context_pack(
            purpose="router",
            messages=messages,
            summary="早期对话摘要",
        )

        self.assertLessEqual(len(pack.recent_messages), 2)
        self.assertEqual(pack.recent_messages[-1]["content"], "历史消息 9")
        self.assertIn("早期对话摘要", pack.summary)
        self.assertLessEqual(pack.estimated_tokens, 800)

    def test_answer_pack_limits_retrieval_context_and_marks_truncation(self) -> None:
        pack = build_context_pack(
            purpose="answer",
            messages=[],
            retrieval_context="采购复核证据。" * 10000,
        )

        self.assertTrue(pack.truncated)
        self.assertLessEqual(pack.estimated_tokens, 6000)
        self.assertIn("已裁剪", pack.retrieval_context)

    def test_three_contexts_share_summary_but_have_different_budgets(self) -> None:
        messages = [
            {"role": "user", "content": "差旅报销流程"},
            {"role": "assistant", "content": "请问你想了解哪一步？"},
        ]

        contexts = build_conversation_contexts(
            messages=messages,
            summary="用户正在了解差旅报销流程。",
        )

        self.assertEqual(set(contexts), {"router_context", "rewrite_context", "answer_context"})
        self.assertEqual(
            contexts["router_context"]["summary"],
            contexts["answer_context"]["summary"],
        )
        self.assertEqual(
            contexts["router_context"]["estimated_tokens"]
            <= contexts["answer_context"]["estimated_tokens"],
            True,
        )
        self.assertEqual(estimate_tokens(""), 0)

    def test_structured_evidence_is_selected_atomically(self) -> None:
        pack = build_context_pack(
            purpose="answer",
            messages=[],
            evidence_items=[
                EvidenceItem(
                    content="采购复核需要委员会确认。",
                    source_id="chunk-1",
                    chunk_id=1,
                    title="采购制度",
                ),
                EvidenceItem(
                    content="背景材料。" * 10000,
                    source_id="chunk-2",
                    chunk_id=2,
                    title="超长背景",
                ),
            ],
        )

        self.assertEqual([item.chunk_id for item in pack.evidence_items], [1])
        self.assertTrue(pack.truncated)
        self.assertTrue(any(item.source_id == "chunk-2" for item in pack.omitted_items))
        self.assertIn("chunk_id=1", pack.retrieval_context)

    def test_relevant_history_is_budgeted_as_whole_items(self) -> None:
        pack = build_context_pack(
            purpose="rewrite",
            messages=[],
            relevant_history=[
                {
                    "source_id": "message-1",
                    "content": "用户之前确认采购复核金额门槛。",
                    "importance": 2,
                }
            ],
        )

        self.assertEqual(len(pack.relevant_history), 1)
        self.assertEqual(pack.relevant_history[0].source_ids, ["message-1"])
        self.assertIn("relevant_history", pack.to_dict())

    def test_summary_parser_accepts_structured_json_mode_output(self) -> None:
        messages = [Message(id=12, conversation_id=1, role="user", content="采购复核金额")]
        with patch(
            "app.services.llm_router_service.is_llm_router_configured",
            return_value=True,
        ), patch(
            "app.services.llm_router_service.call_openai_compatible_chat",
            return_value=(
                '{"facts":["采购复核金额超过二十万元"],"decisions":[],'
                '"open_questions":[],"entities":["采购复核"]}'
            ),
        ) as call:
            summary = summarize_conversation_with_llm(messages)

        self.assertIsNotNone(summary)
        self.assertEqual(summary.facts, ["采购复核金额超过二十万元"])
        self.assertTrue(call.call_args.kwargs["json_mode"])


if __name__ == "__main__":
    unittest.main()
