import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, select

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from app.db.database import get_session
from app.db.models import Chunk, KnowledgeBase, KnowledgeItem
from app.main import app
from app.api import knowledge_item as knowledge_item_api
from app.security.dependencies import get_current_principal
from app.services.document_splitter.models import ChunkData
from postgres_test_utils import PostgresTestDatabase
from resource_authorization_utils import create_test_identity


class FakeIndexResult:
    def __init__(self, index_name: str, vector_ids: list[str]):
        self.index_name = index_name
        self.vector_ids = vector_ids


class KnowledgeItemApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.test_database = PostgresTestDatabase()
        self.engine = self.test_database.create_engine()

        def override_get_session():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = override_get_session
        self.client = TestClient(app)

        with Session(self.engine) as session:
            self.principal = create_test_identity(session)
            app.dependency_overrides[get_current_principal] = lambda: self.principal
            ownership = {
                "organization_id": self.principal.organization_id,
                "created_by_user_id": self.principal.user_id,
            }
            knowledge_base = KnowledgeBase(
                name="知识库A",
                description="knowledge item api test",
                **ownership,
            )
            session.add(knowledge_base)
            session.commit()
            session.refresh(knowledge_base)
            self.knowledge_base_id = knowledge_base.id

            knowledge_item = KnowledgeItem(
                knowledge_base_id=knowledge_base.id,
                title="测试条目",
                content="用于测试 chunks 查询。",
                tags="测试",
                status="active",
                source_type="manual",
                **ownership,
            )
            session.add(knowledge_item)
            session.commit()
            session.refresh(knowledge_item)
            self.knowledge_item_id = knowledge_item.id

            indexable_item = KnowledgeItem(
                knowledge_base_id=knowledge_base.id,
                title="可索引条目",
                content="第一段内容。\n\n第二段内容。",
                tags="索引",
                status="draft",
                source_type="manual",
                **ownership,
            )
            session.add(indexable_item)
            session.commit()
            session.refresh(indexable_item)
            self.indexable_item_id = indexable_item.id

            first_chunk = Chunk(
                knowledge_base_id=knowledge_base.id,
                document_id=None,
                knowledge_item_id=knowledge_item.id,
                chunk_index=0,
                content="第一段 chunk",
                vector_id="vector_1",
                metadata_json='{"heading_path":["测试条目"]}',
                organization_id=self.principal.organization_id,
            )
            second_chunk = Chunk(
                knowledge_base_id=knowledge_base.id,
                document_id=None,
                knowledge_item_id=knowledge_item.id,
                chunk_index=1,
                content="第二段 chunk",
                vector_id="vector_2",
                metadata_json='{"heading_path":["测试条目"]}',
                organization_id=self.principal.organization_id,
            )
            session.add(first_chunk)
            session.add(second_chunk)
            session.commit()

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.test_database.dispose()

    def test_get_knowledge_item_chunks_returns_ordered_chunks(self) -> None:
        response = self.client.get(f"/knowledge-items/{self.knowledge_item_id}/chunks")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["chunk_index"], 0)
        self.assertEqual(payload[1]["chunk_index"], 1)
        self.assertEqual(payload[0]["vector_id"], "vector_1")
        self.assertEqual(payload[1]["vector_id"], "vector_2")

    def test_get_knowledge_item_chunks_returns_404_for_missing_item(self) -> None:
        response = self.client.get("/knowledge-items/999999/chunks")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Knowledge item not found")

    def test_split_knowledge_item_into_chunks_creates_rows(self) -> None:
        original_splitter = knowledge_item_api.split_document_text
        try:
            knowledge_item_api.split_document_text = lambda *args, **kwargs: [
                ChunkData(content="第一段内容", metadata={}),
                ChunkData(content="第二段内容", metadata={}),
            ]

            response = self.client.post(f"/knowledge-items/{self.indexable_item_id}/chunks")
        finally:
            knowledge_item_api.split_document_text = original_splitter

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["knowledge_item_id"], self.indexable_item_id)
        self.assertEqual(payload["chunk_count"], 2)

        with Session(self.engine) as session:
            chunks = session.exec(
                select(Chunk)
                .where(Chunk.knowledge_item_id == self.indexable_item_id)
                .order_by(Chunk.chunk_index)
            ).all()
            self.assertEqual(len(chunks), 2)
            self.assertTrue(chunks[0].content.startswith("# 可索引条目"))

    def test_index_knowledge_item_writes_vector_ids(self) -> None:
        original_splitter = knowledge_item_api.split_document_text
        original_add_chunks = knowledge_item_api.add_chunks
        original_delete_vectors = knowledge_item_api.delete_vectors
        try:
            knowledge_item_api.split_document_text = lambda *args, **kwargs: [
                ChunkData(content="第一段内容", metadata={}),
                ChunkData(content="第二段内容", metadata={}),
            ]
            knowledge_item_api.add_chunks = lambda chunks: FakeIndexResult(
                index_name="knowledge_chunks_test",
                vector_ids=["vector_a", "vector_b"],
            )
            knowledge_item_api.delete_vectors = lambda knowledge_base_id, vector_ids: None

            response = self.client.post(f"/knowledge-items/{self.indexable_item_id}/index")
        finally:
            knowledge_item_api.split_document_text = original_splitter
            knowledge_item_api.add_chunks = original_add_chunks
            knowledge_item_api.delete_vectors = original_delete_vectors

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["vector_count"], 2)
        self.assertEqual(payload["index_name"], "knowledge_chunks_test")

        with Session(self.engine) as session:
            chunks = session.exec(
                select(Chunk)
                .where(Chunk.knowledge_item_id == self.indexable_item_id)
                .order_by(Chunk.chunk_index)
            ).all()
            self.assertEqual([chunk.vector_id for chunk in chunks], ["vector_a", "vector_b"])

    def test_delete_knowledge_item_removes_chunks_before_delete(self) -> None:
        response = self.client.delete(f"/knowledge-items/{self.knowledge_item_id}")

        self.assertEqual(response.status_code, 204)

        with Session(self.engine) as session:
            knowledge_item = session.get(KnowledgeItem, self.knowledge_item_id)
            chunks = session.exec(
                select(Chunk).where(Chunk.knowledge_item_id == self.knowledge_item_id)
            ).all()

        self.assertIsNone(knowledge_item)
        self.assertEqual(chunks, [])
