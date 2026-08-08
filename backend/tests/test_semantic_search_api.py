import sys
import unittest
from pathlib import Path

from fastapi import HTTPException
from sqlmodel import Session

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from app.api import search as search_api
from app.db.models import KnowledgeBase, KnowledgeItem
from app.schemas.search import SemanticSearchRequest
from app.services.vector_service import SemanticSearchHit
from postgres_test_utils import PostgresTestDatabase
from resource_authorization_utils import create_test_identity


class SemanticSearchApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.test_database = PostgresTestDatabase()
        self.engine = self.test_database.create_engine()
        self.session = Session(self.engine)
        self.principal = create_test_identity(self.session)
        ownership = {
            "organization_id": self.principal.organization_id,
            "created_by_user_id": self.principal.user_id,
        }

        knowledge_base = KnowledgeBase(name="制度库", description="用于语义搜索测试", **ownership)
        self.session.add(knowledge_base)
        self.session.commit()
        self.session.refresh(knowledge_base)
        self.knowledge_base = knowledge_base

        knowledge_item = KnowledgeItem(
            knowledge_base_id=knowledge_base.id,
            title="差旅报销流程",
            content="员工差旅报销需要先提交发票，再走审批。",
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

    def test_semantic_search_returns_enriched_hits(self) -> None:
        original_search = search_api.retrieve_hybrid_chunks
        try:
            search_api.retrieve_hybrid_chunks = lambda **kwargs: [
                SemanticSearchHit(
                    vector_id="vector_1",
                    chunk_id=21,
                    document_id=None,
                    knowledge_item_id=self.knowledge_item.id,
                    content="员工差旅报销需要先提交发票，再走审批流程，然后财务复核。",
                    score=0.92,
                    metadata={"heading_path": ["报销制度"]},
                )
            ]

            results = search_api.semantic_search(
                SemanticSearchRequest(
                    knowledge_base_id=self.knowledge_base.id,
                    query="怎么报销差旅费",
                    top_k=3,
                ),
                principal=self.principal,
                session=self.session,
            )
        finally:
            search_api.retrieve_hybrid_chunks = original_search

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].chunk_id, 21)
        self.assertEqual(results[0].title, "差旅报销流程")
        self.assertEqual(results[0].score, 0.92)
        self.assertIn("员工差旅报销", results[0].content_preview)
        self.assertEqual(results[0].metadata["heading_path"], ["报销制度"])

    def test_semantic_search_rejects_empty_query(self) -> None:
        with self.assertRaises(HTTPException) as context:
            search_api.semantic_search(
                SemanticSearchRequest(
                    knowledge_base_id=self.knowledge_base.id,
                    query="   ",
                    top_k=3,
                ),
                principal=self.principal,
                session=self.session,
            )

        self.assertEqual(context.exception.status_code, 400)
