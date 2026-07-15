import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import llm_router_service


class LlmRouterServiceTests(unittest.TestCase):
    def test_normalize_route(self) -> None:
        self.assertEqual(llm_router_service.normalize_route(" direct "), "direct")
        self.assertEqual(llm_router_service.normalize_route("RAG"), "rag")
        self.assertEqual(llm_router_service.normalize_route("Complex"), "complex")
        self.assertIsNone(llm_router_service.normalize_route("unknown"))

    def test_parse_router_output_from_json(self) -> None:
        decision = llm_router_service.parse_router_output(
            '```json\n{"route":"complex","reason":"需要总结整个知识库"}\n```'
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.route, "complex")
        self.assertEqual(decision.reason, "需要总结整个知识库")

    def test_parse_router_output_from_plain_text(self) -> None:
        decision = llm_router_service.parse_router_output("rag")
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.route, "rag")
        self.assertEqual(decision.reason, "llm router")

    def test_build_router_messages(self) -> None:
        messages = llm_router_service.build_router_messages(
            "公司制度怎么报销",
            knowledge_base_id=7,
        )
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("knowledge_base_id: 7", messages[1]["content"])
        self.assertIn("公司制度怎么报销", messages[1]["content"])
        self.assertIn("JSON", messages[0]["content"])

    def test_build_chat_completion_payload_enables_json_mode(self) -> None:
        payload = llm_router_service.build_chat_completion_payload(
            model="qwen-turbo",
            messages=[
                {"role": "system", "content": "请输出 JSON"},
                {"role": "user", "content": "question: 什么是 RAG"},
            ],
        )
        self.assertEqual(payload["model"], "qwen-turbo")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["max_tokens"], 64)
