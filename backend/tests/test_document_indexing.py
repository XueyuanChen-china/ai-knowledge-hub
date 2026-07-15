import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlmodel import Session, select

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from app.api import document as document_api
from app.db.models import Chunk, Document, KnowledgeBase
from app.services.document_splitter.models import ChunkData
from postgres_test_utils import PostgresTestDatabase


class FakeIndexResult:
    def __init__(self, index_name: str, vector_ids: list[str]):
        self.index_name = index_name
        self.vector_ids = vector_ids


class DocumentIndexingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.test_database = PostgresTestDatabase()
        self.engine = self.test_database.create_engine()
        self.session = Session(self.engine)

        knowledge_base = KnowledgeBase(name="测试知识库", description="用于 Day 9 测试")
        self.session.add(knowledge_base)
        self.session.commit()
        self.session.refresh(knowledge_base)
        self.knowledge_base = knowledge_base

        file_path = Path(self.temp_dir.name) / "policy.txt"
        file_path.write_text("第一段\n\n第二段", encoding="utf-8")

        document = Document(
            knowledge_base_id=knowledge_base.id,
            filename="policy.txt",
            file_path=str(file_path),
            file_type="txt",
            status="uploaded",
            extracted_text="第一段\n\n第二段",
        )
        self.session.add(document)
        self.session.commit()
        self.session.refresh(document)
        self.document = document

    def tearDown(self) -> None:
        self.session.close()
        self.test_database.dispose()
        self.temp_dir.cleanup()

    def test_index_document_writes_chunks_and_vector_ids(self) -> None:
        original_splitter = document_api.split_document_text
        original_add_chunks = document_api.add_chunks
        original_delete_vectors = document_api.delete_vectors
        try:
            document_api.split_document_text = lambda *args, **kwargs: [
                ChunkData(content="第一段", metadata={"heading_path": ["标题一"]}),
                ChunkData(content="第二段", metadata={"heading_path": ["标题二"]}),
            ]
            document_api.add_chunks = lambda chunks: FakeIndexResult(
                index_name="knowledge_chunks_1",
                vector_ids=["vector_1", "vector_2"],
            )
            document_api.delete_vectors = lambda knowledge_base_id, vector_ids: None

            response = document_api.index_document(self.document.id, session=self.session)
        finally:
            document_api.split_document_text = original_splitter
            document_api.add_chunks = original_add_chunks
            document_api.delete_vectors = original_delete_vectors

        self.assertEqual(response.document_id, self.document.id)
        self.assertEqual(response.chunk_count, 2)
        self.assertEqual(response.vector_count, 2)
        self.assertEqual(response.index_name, "knowledge_chunks_1")

        refreshed_document = self.session.get(Document, self.document.id)
        self.assertEqual(refreshed_document.status, "indexed")

        chunks = list(
            self.session.exec(
                select(Chunk).where(Chunk.document_id == self.document.id).order_by(Chunk.chunk_index)
            ).all()
        )
        self.assertEqual(len(chunks), 2)
        self.assertEqual([chunk.vector_id for chunk in chunks], ["vector_1", "vector_2"])

    def test_index_document_marks_failed_when_vector_write_raises(self) -> None:
        original_splitter = document_api.split_document_text
        original_add_chunks = document_api.add_chunks
        original_delete_vectors = document_api.delete_vectors
        try:
            document_api.split_document_text = lambda *args, **kwargs: [
                ChunkData(content="第一段", metadata={"heading_path": ["标题一"]}),
            ]

            def raise_index_error(chunks):
                raise RuntimeError("vector write failed")

            document_api.add_chunks = raise_index_error
            document_api.delete_vectors = lambda knowledge_base_id, vector_ids: None

            with self.assertRaises(RuntimeError):
                document_api.index_document(self.document.id, session=self.session)
        finally:
            document_api.split_document_text = original_splitter
            document_api.add_chunks = original_add_chunks
            document_api.delete_vectors = original_delete_vectors

        refreshed_document = self.session.get(Document, self.document.id)
        self.assertEqual(refreshed_document.status, "failed")

    def test_list_documents_supports_knowledge_base_filter(self) -> None:
        other_base = KnowledgeBase(name="另一个知识库", description="filter test")
        self.session.add(other_base)
        self.session.commit()
        self.session.refresh(other_base)

        other_document = Document(
            knowledge_base_id=other_base.id,
            filename="other.txt",
            file_path=str(Path(self.temp_dir.name) / "other.txt"),
            file_type="txt",
            status="uploaded",
            extracted_text="另一个文档",
        )
        self.session.add(other_document)
        self.session.commit()

        filtered_documents = document_api.list_documents(
            knowledge_base_id=self.knowledge_base.id,
            session=self.session,
        )

        self.assertEqual(len(filtered_documents), 1)
        self.assertEqual(filtered_documents[0].id, self.document.id)
