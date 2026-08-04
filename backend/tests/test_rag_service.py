import sys
import unittest
from pathlib import Path

from sqlmodel import Session

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from app.db.models import KnowledgeBase, KnowledgeItem
from app.services import rag_service
from app.services.vector_service import SemanticSearchHit
from postgres_test_utils import PostgresTestDatabase
from resource_authorization_utils import create_test_identity


class RagServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.test_database = PostgresTestDatabase()
        self.engine = self.test_database.create_engine()
        self.session = Session(self.engine)
        self.principal = create_test_identity(self.session)
        ownership = {
            "organization_id": self.principal.organization_id,
            "created_by_user_id": self.principal.user_id,
        }

        knowledge_base = KnowledgeBase(name="制度库", description="RAG 测试", **ownership)
        self.session.add(knowledge_base)
        self.session.commit()
        self.session.refresh(knowledge_base)
        self.knowledge_base = knowledge_base

        knowledge_item = KnowledgeItem(
            knowledge_base_id=knowledge_base.id,
            title="差旅报销流程",
            content="员工差旅报销需要先提交发票，再走审批流程。",
            tags="[]",
            status="active",
            source_type="manual",
            **ownership,
        )
        self.session.add(knowledge_item)
        self.session.commit()
        self.session.refresh(knowledge_item)
        self.knowledge_item = knowledge_item

    def tearDown(self) -> None:
        self.session.close()
        self.test_database.dispose()

    def test_retrieve_enriches_titles(self) -> None:
        original_search = rag_service.retrieve_hybrid_chunks
        try:
            rag_service.retrieve_hybrid_chunks = lambda **kwargs: [
                SemanticSearchHit(
                    vector_id="vector_1",
                    chunk_id=31,
                    document_id=None,
                    knowledge_item_id=self.knowledge_item.id,
                    content="员工差旅报销需要先提交发票，再走审批流程。",
                    score=0.95,
                    metadata={"heading_path": ["报销制度"]},
                )
            ]
            documents = rag_service.retrieve(
                question="差旅报销怎么走流程",
                knowledge_base_id=self.knowledge_base.id,
                session=self.session,
                top_k=3,
            )
        finally:
            rag_service.retrieve_hybrid_chunks = original_search

        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].title, "差旅报销流程")
        self.assertEqual(documents[0].chunk_id, 31)
        self.assertEqual(documents[0].score, 0.95)

    def test_format_context_renders_documents(self) -> None:
        documents = [
            rag_service.RetrievedDocument(
                doc_id=1,
                chunk_id=2,
                knowledge_item_id=3,
                title="差旅报销流程",
                content="员工差旅报销需要先提交发票。",
                score=0.91,
                metadata={},
            )
        ]

        context = rag_service.format_context(documents)

        self.assertIn("标题：差旅报销流程", context)
        self.assertIn("doc_id: 1", context)
        self.assertIn("chunk_id: 2", context)
        self.assertIn("员工差旅报销需要先提交发票。", context)

    def test_generate_answer_builds_answer_and_citations(self) -> None:
        documents = [
            rag_service.RetrievedDocument(
                doc_id=1,
                chunk_id=2,
                knowledge_item_id=3,
                title="差旅报销流程",
                content="员工差旅报销需要先提交发票。审批完成后由财务复核并打款。",
                score=0.91,
                metadata={},
            ),
            rag_service.RetrievedDocument(
                doc_id=1,
                chunk_id=3,
                knowledge_item_id=3,
                title="差旅报销流程",
                content="如果票据缺失，需要先补齐材料再申请报销。",
                score=0.84,
                metadata={},
            ),
        ]

        result = rag_service.generate_answer(
            question="差旅报销怎么走流程",
            documents=documents,
        )

        self.assertIn("根据当前知识库检索结果", result.answer)
        self.assertIn("员工差旅报销需要先提交发票", result.answer)
        self.assertEqual(len(result.citations), 2)
        self.assertTrue(result.used_fallback)
        self.assertIn("标题：差旅报销流程", result.context)

    def test_generate_answer_handles_empty_documents(self) -> None:
        result = rag_service.generate_answer(
            question="差旅报销怎么走流程",
            documents=[],
        )

        self.assertIn("没有检索到", result.answer)
        self.assertEqual(result.citations, [])
