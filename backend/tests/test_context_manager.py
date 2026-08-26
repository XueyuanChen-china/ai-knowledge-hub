import sys
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.context_budget import estimate_tokens
from app.services.context_manager import (
    build_answer_context,
    build_context_pack,
    build_conversation_contexts,
    estimate_history_region_tokens,
    get_summary_refresh_reason,
    select_summary_batch,
    split_recent_messages_by_rounds,
)
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

    def test_answer_context_marks_existing_citations_as_protected(self) -> None:
        document = SimpleNamespace(
            content="采购复核需要委员会确认。",
            doc_id=8,
            chunk_id=51,
            knowledge_item_id=8,
            title="采购制度.pdf",
            score=0.91,
            metadata={},
        )

        context = build_answer_context(
            recent_context={},
            retrieved_documents=[document],
            protected_citations=[{"doc_id": 8, "chunk_id": 51}],
        )

        self.assertTrue(context["evidence_items"][0]["metadata"]["citation_used"])

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

    def test_short_history_does_not_refresh_only_because_four_turns_accumulated(self) -> None:
        conversation = type(
            "ConversationStub",
            (),
            {
                "context_summary": "",
                "context_summary_through_message_id": None,
            },
        )()
        messages = [
            Message(id=index, conversation_id=1, role="user", content="短消息")
            for index in range(1, 13)
        ]
        settings = SimpleNamespace(
            context_summary_keep_recent_messages=4,
            context_history_region_budget_tokens=1_000,
            context_summary_hard_watermark_tokens=1_000,
            context_summary_soft_watermark_ratio=0.70,
            context_summary_min_compactable_messages=4,
            context_summary_min_compactable_tokens=100,
            context_answer_recent_messages=8,
        )

        with patch("app.services.context_manager.get_settings", return_value=settings):
            reason = get_summary_refresh_reason(messages, conversation)

        self.assertEqual(reason, "")

    def test_history_region_usage_includes_summary_recent_history_and_memory(self) -> None:
        messages = [
            Message(id=index, conversation_id=1, role="user", content="近期消息" * 20)
            for index in range(5, 9)
        ]

        tokens = estimate_history_region_tokens(
            messages=messages,
            summary='{"facts":["较早事实"],"decisions":[],"open_questions":[],"entities":[]}',
            persistent_memory=[{"content": "长期约束" * 10}],
            relevant_history=[{"content": "恢复历史" * 10}],
            summary_through_message_id=4,
        )

        self.assertGreater(tokens, estimate_tokens("近期消息" * 20) * 4)

    def test_history_region_soft_watermark_triggers_with_compactable_messages(self) -> None:
        conversation = type(
            "ConversationStub",
            (),
            {
                "context_summary": "",
                "context_summary_through_message_id": None,
            },
        )()
        messages = [
            Message(id=index, conversation_id=1, role="user", content="较长历史" * 50)
            for index in range(1, 9)
        ]
        settings = SimpleNamespace(
            context_summary_keep_recent_messages=4,
            context_history_region_budget_tokens=1_000,
            context_summary_hard_watermark_tokens=1_000,
            context_summary_soft_watermark_ratio=0.70,
            context_summary_min_compactable_messages=4,
            context_summary_min_compactable_tokens=100,
            context_answer_recent_messages=8,
        )

        with patch("app.services.context_manager.get_settings", return_value=settings):
            reason = get_summary_refresh_reason(messages, conversation)

        self.assertEqual(reason, "soft_watermark_history_region")

    def test_summary_watermark_uses_answer_window_not_all_database_history(self) -> None:
        conversation = type(
            "ConversationStub",
            (),
            {
                "context_summary": "",
                "context_summary_through_message_id": None,
            },
        )()
        messages = [
            Message(id=index, conversation_id=1, role="user", content="很长的旧消息" * 200)
            for index in range(1, 9)
        ] + [
            Message(id=index, conversation_id=1, role="user", content="近期短消息")
            for index in range(9, 21)
        ]
        settings = SimpleNamespace(
            context_summary_keep_recent_messages=4,
            context_history_region_budget_tokens=1_000,
            context_summary_hard_watermark_tokens=1_000,
            context_summary_soft_watermark_ratio=0.70,
            context_summary_min_compactable_messages=4,
            context_summary_min_compactable_tokens=1,
            context_answer_recent_messages=8,
        )

        with patch("app.services.context_manager.get_settings", return_value=settings):
            reason = get_summary_refresh_reason(messages, conversation)

        self.assertEqual(reason, "")

    def test_contexts_exclude_messages_already_covered_by_summary_cursor(self) -> None:
        messages = [
            Message(id=index, conversation_id=1, role="user", content=f"消息 {index}")
            for index in range(1, 13)
        ]

        contexts = build_conversation_contexts(
            messages=messages,
            summary="早期摘要",
            summary_through_message_id=8,
        )

        answer_messages = contexts["answer_context"]["recent_messages"]
        self.assertEqual(
            [item["content"] for item in answer_messages],
            ["消息 9", "消息 10", "消息 11", "消息 12"],
        )

    def test_summary_batch_targets_sixty_percent_history_usage(self) -> None:
        messages = [
            Message(id=index, conversation_id=1, role="user", content="历史内容" * 100)
            for index in range(1, 13)
        ]

        selected = select_summary_batch(
            messages=messages,
            keep_recent_messages=4,
            current_history_tokens=2_000,
            history_budget_tokens=2_000,
            target_ratio=0.60,
            max_summary_tokens=200,
            min_messages=4,
            max_messages=12,
        )

        self.assertGreaterEqual(len(selected), 4)
        self.assertLessEqual(len(selected), 8)

    def test_answer_context_keeps_at_most_four_user_turns(self) -> None:
        messages = [
            {"role": "user", "content": f"问题 {index}"}
            for index in range(20)
        ]
        pack = build_context_pack(
            purpose="answer",
            messages=messages,
        )

        self.assertEqual(len(pack.recent_messages), 4)
        self.assertEqual(pack.recent_messages[0]["content"], "问题 16")
        self.assertEqual(pack.recent_messages[-1]["content"], "问题 19")

    def test_answer_context_uses_four_user_assistant_rounds(self) -> None:
        messages = []
        for index in range(1, 14):
            messages.extend(
                [
                    {"role": "user", "content": f"问题 {index}"},
                    {"role": "assistant", "content": f"回答 {index}"},
                ]
            )

        pack = build_context_pack(purpose="answer", messages=messages)

        self.assertEqual(len(pack.recent_messages), 8)
        self.assertEqual(pack.recent_messages[0]["content"], "问题 10")
        self.assertEqual(pack.recent_messages[-1]["content"], "回答 13")

    def test_tool_pack_keeps_reference_not_full_result(self) -> None:
        pack = build_context_pack(
            purpose="answer",
            messages=[],
            tool_result_refs=[
                {
                    "tool_name": "get_document",
                    "result_ref": "tool-result-1",
                    "summary": "供应商制度包含采购复核条件。",
                    "source_ids": ["chunk:51"],
                    "content": "这是不应进入 Pack 的完整长原文。" * 100,
                }
            ],
        )

        self.assertEqual(len(pack.tool_result_refs), 1)
        self.assertEqual(pack.tool_result_refs[0].result_ref, "tool-result-1")
        self.assertNotIn("完整长原文", pack.tool_results[0])

    def test_hard_watermark_keeps_latest_two_of_four_active_rounds(self) -> None:
        messages = []
        for index in range(1, 5):
            messages.extend(
                [
                    {"role": "user", "content": f"问题 {index}"},
                    {"role": "assistant", "content": f"回答 {index}"},
                ]
            )

        older, recent = split_recent_messages_by_rounds(
            messages,
            keep_recent_rounds=2,
            max_rounds=4,
            max_messages=8,
        )

        self.assertEqual(
            [item["content"] for item in older],
            ["问题 1", "回答 1", "问题 2", "回答 2"],
        )
        self.assertEqual(
            [item["content"] for item in recent],
            ["问题 3", "回答 3", "问题 4", "回答 4"],
        )

    def test_pack_hard_watermark_compacts_replaceable_candidates(self) -> None:
        pack = build_context_pack(
            purpose="answer",
            messages=[
                {"role": "user", "content": "近期对话" * 500}
                for _ in range(6)
            ],
            evidence_items=[
                EvidenceItem(
                    content="检索证据" * 500,
                    source_id=f"chunk-{index}",
                    chunk_id=index,
                    score=float(10 - index),
                )
                for index in range(1, 5)
            ],
            tool_result_refs=[
                {
                    "tool_name": "get_document",
                    "result_ref": f"tool-result-{index}",
                    "summary": "工具摘要" * 100,
                }
                for index in range(1, 7)
            ],
        )

        self.assertIn("hard_watermark_preflight", pack.compaction_actions)
        self.assertIn("evict_old_tool_results", pack.compaction_actions)
        self.assertLessEqual(pack.estimated_tokens, pack.budget.total)

    def test_summary_soft_watermark_uses_history_region_budget(self) -> None:
        conversation = type(
            "ConversationStub",
            (),
            {
                "context_summary": "",
                "context_summary_through_message_id": None,
            },
        )()
        messages = [
            Message(id=index, conversation_id=1, role="user", content="长历史" * 100)
            for index in range(1, 8)
        ]
        settings = SimpleNamespace(
            context_summary_keep_recent_messages=4,
            context_history_region_budget_tokens=100,
            context_summary_hard_watermark_tokens=100,
            context_summary_soft_watermark_ratio=0.70,
            context_summary_min_compactable_messages=2,
            context_summary_min_compactable_tokens=1,
            context_answer_recent_messages=8,
        )

        with patch("app.services.context_manager.get_settings", return_value=settings):
            reason = get_summary_refresh_reason(messages, conversation)

        self.assertEqual(reason, "soft_watermark_history_region")


if __name__ == "__main__":
    unittest.main()
