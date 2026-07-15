import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import llm_answer_service, rag_service


class LlmAnswerServiceTests(unittest.TestCase):
    def test_parse_answer_output_from_json(self) -> None:
        payload = llm_answer_service.parse_answer_output(
            '```json\n{"answer":"可以报销差旅费用。","used_context_numbers":[1,2]}\n```'
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload.answer, "可以报销差旅费用。")
        self.assertEqual(payload.used_context_numbers, [1, 2])

    def test_build_citations_from_context_numbers(self) -> None:
        documents = [
            rag_service.RetrievedDocument(
                doc_id=10,
                chunk_id=20,
                knowledge_item_id=30,
                title="采购制度",
                content="单次采购金额超过二十万元，需要采购委员会复核。",
                score=0.91,
                metadata={},
            ),
            rag_service.RetrievedDocument(
                doc_id=11,
                chunk_id=21,
                knowledge_item_id=31,
                title="报销制度",
                content="报销前需要补齐发票。",
                score=0.87,
                metadata={},
            ),
        ]

        citations = llm_answer_service.build_citations_from_context_numbers(
            documents,
            [2, 1, 9, 1],
        )

        self.assertEqual(len(citations), 2)
        self.assertEqual(citations[0]["chunk_id"], 21)
        self.assertEqual(citations[1]["chunk_id"], 20)

    def test_append_reference_labels(self) -> None:
        documents = [
            rag_service.RetrievedDocument(
                doc_id=10,
                chunk_id=20,
                knowledge_item_id=30,
                title="采购制度",
                content="单次采购金额超过二十万元，需要采购委员会复核。",
                score=0.91,
                metadata={},
            )
        ]
        citations = rag_service.build_citations(documents)
        answer = llm_answer_service.append_reference_labels(
            "单次采购金额超过二十万元，需要采购委员会复核。",
            citations,
            documents,
        )

        self.assertIn("参考来源：[1]", answer)

    def test_generate_answer_falls_back_without_configuration(self) -> None:
        original_resolve = llm_answer_service.resolve_answer_settings
        try:
            llm_answer_service.resolve_answer_settings = lambda: llm_answer_service.AnswerLlmSettings(
                base_url="",
                api_key="",
                model="",
                timeout_seconds=40,
            )
            documents = [
                rag_service.RetrievedDocument(
                    doc_id=10,
                    chunk_id=20,
                    knowledge_item_id=30,
                    title="采购制度",
                    content="单次采购金额超过二十万元，需要采购委员会复核。",
                    score=0.91,
                    metadata={},
                )
            ]
            result = llm_answer_service.generate_answer(
                "采购复核的触发条件是什么？",
                documents,
            )
        finally:
            llm_answer_service.resolve_answer_settings = original_resolve

        self.assertTrue(result.used_fallback)
        self.assertIn("根据当前知识库检索结果", result.answer)
