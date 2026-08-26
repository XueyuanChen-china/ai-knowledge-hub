import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import llm_answer_service, llm_router_service, query_rewrite_service


class QueryRewriteTests(unittest.TestCase):
    def test_self_contained_question_skips_rewrite(self) -> None:
        decision = query_rewrite_service.decide_query_rewrite(
            "采购复核的触发条件是什么？",
            [{"role": "user", "content": "上一轮问题"}],
        )
        self.assertFalse(decision.need_rewrite)
        self.assertEqual(decision.reason, "question_is_self_contained")

    def test_reference_question_requires_rewrite_when_history_exists(self) -> None:
        decision = query_rewrite_service.decide_query_rewrite(
            "这个流程多久完成？",
            [{"role": "user", "content": "差旅报销需要哪些材料？"}],
        )
        self.assertTrue(decision.need_rewrite)
        self.assertEqual(decision.reason, "contains_context_reference")

    def test_reference_without_history_does_not_call_llm(self) -> None:
        decision = query_rewrite_service.decide_query_rewrite("这个流程多久完成？", [])
        self.assertFalse(decision.need_rewrite)
        self.assertEqual(decision.reason, "no_conversation_context")

    def test_parse_rewrite_output_keeps_original_and_limits_variants(self) -> None:
        queries = query_rewrite_service.parse_rewrite_output(
            '{"queries":["差旅报销提交时限是多少？","费用申请需要几天内提交？","第三个","第四个"]}',
            "这个流程多久完成？",
        )
        self.assertEqual(
            queries,
            [
                "这个流程多久完成？",
                "差旅报销提交时限是多少？",
                "费用申请需要几天内提交？",
                "第三个",
            ],
        )

    def test_router_and_answer_builders_receive_different_context_shapes(self) -> None:
        router_messages = llm_router_service.build_router_messages(
            "这个流程多久完成？",
            knowledge_base_id=1,
            conversation_context={
                "recent_messages": [{"role": "user", "content": "差旅报销"}],
            },
        )
        answer_messages = llm_answer_service.build_answer_messages(
            "这个流程多久完成？",
            "[1] 内容：十个工作日内提交。",
            conversation_context={
                "recent_messages": [
                    {"role": "user", "content": "差旅报销"},
                    {"role": "assistant", "content": "请提供具体问题。"},
                ],
            },
        )

        self.assertIn("差旅报销", router_messages[1]["content"])
        self.assertIn("十个工作日内提交", answer_messages[1]["content"])
        self.assertIn("recent conversation", answer_messages[1]["content"])
        self.assertNotIn("十个工作日内提交", router_messages[1]["content"])

    def test_answer_builder_uses_structured_context_fields(self) -> None:
        answer_messages = llm_answer_service.build_answer_messages(
            "刚才的制度原文是什么？",
            "",
            conversation_context={
                "system_instructions": ["来源查询只能返回文件和原文位置。"],
                "persistent_memory": [
                    {"content": "用户偏好中文回答。", "pinned": True}
                ],
                "evidence_items": [
                    {
                        "content": "供应商准入制度原文。",
                        "source_id": "chunk:51",
                        "document_id": 8,
                        "chunk_id": 51,
                        "title": "供应商制度",
                        "score": 0.9,
                    }
                ],
                "tool_result_refs": [
                    {
                        "tool_name": "get_document",
                        "result_ref": "tool-result-1",
                        "summary": "已找到供应商制度原文。",
                        "source_ids": ["document:8"],
                        "protected_for_turn": True,
                    }
                ],
            },
        )

        self.assertIn("来源查询只能返回文件和原文位置", answer_messages[0]["content"])
        self.assertIn("供应商准入制度原文", answer_messages[1]["content"])
        self.assertIn("用户偏好中文回答", answer_messages[1]["content"])
        self.assertIn("已找到供应商制度原文", answer_messages[1]["content"])


if __name__ == "__main__":
    unittest.main()
