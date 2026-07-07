import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.database import get_session
from app.db.models import KnowledgeBase
from app.graph import nodes
from app.main import app
from app.services import rag_service
from app.services.rag_service import RetrievedDocument


class ChatApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)

        def override_get_session():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = override_get_session
        self.client = TestClient(app)

        with Session(self.engine) as session:
            knowledge_base = KnowledgeBase(name="制度库", description="chat api test")
            session.add(knowledge_base)
            session.commit()
            session.refresh(knowledge_base)
            self.knowledge_base_id = knowledge_base.id

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_chat_interrupts_after_retrieve_when_no_docs(self) -> None:
        original_retrieve = nodes.rag_service.retrieve
        original_llm_route = nodes.llm_router_service.route_question_with_llm
        try:
            nodes.rag_service.retrieve = lambda *args, **kwargs: []
            nodes.llm_router_service.route_question_with_llm = lambda *args, **kwargs: None

            response = self.client.post(
                "/api/chat",
                json={
                    "knowledge_base_id": self.knowledge_base_id,
                    "question": "一个知识库里不存在的问题",
                },
            )
        finally:
            nodes.rag_service.retrieve = original_retrieve
            nodes.llm_router_service.route_question_with_llm = original_llm_route

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "interrupted")
        self.assertTrue(payload["thread_id"])
        self.assertTrue(payload["need_human_review"])
        self.assertEqual(payload["relevance_decision"], "need_review")
        self.assertEqual(payload["review_reason"], "no retrieved documents")
        self.assertIn("review_payload", payload)
        self.assertEqual(payload["review_payload"]["question"], "一个知识库里不存在的问题")

    def test_chat_resume_approved_continues_to_answer(self) -> None:
        original_retrieve = nodes.rag_service.retrieve
        original_llm_route = nodes.llm_router_service.route_question_with_llm
        original_answer_generate = nodes.llm_answer_service.generate_answer
        try:
            nodes.rag_service.retrieve = lambda *args, **kwargs: [
                RetrievedDocument(
                    doc_id=10,
                    chunk_id=20,
                    knowledge_item_id=30,
                    title="采购制度",
                    content="单次采购金额超过二十万元，需要采购委员会复核。",
                    score=0.12,
                    metadata={},
                )
            ]
            nodes.llm_router_service.route_question_with_llm = lambda *args, **kwargs: None
            nodes.llm_answer_service.generate_answer = (
                lambda question, documents: rag_service.RagAnswerResult(
                    answer="采购复核的触发条件是单次采购金额超过二十万元。\n\n参考来源：[1]",
                    context=rag_service.format_context(documents),
                    citations=rag_service.build_citations(documents),
                    used_fallback=False,
                )
            )

            start_response = self.client.post(
                "/api/chat",
                json={
                    "knowledge_base_id": self.knowledge_base_id,
                    "question": "采购复核的触发条件是什么？",
                },
            )
            self.assertEqual(start_response.status_code, 200)
            start_payload = start_response.json()
            self.assertEqual(start_payload["status"], "interrupted")

            resume_response = self.client.post(
                "/api/review/resume",
                json={
                    "thread_id": start_payload["thread_id"],
                    "approved": True,
                    "human_note": "允许继续生成答案",
                },
            )
        finally:
            nodes.rag_service.retrieve = original_retrieve
            nodes.llm_router_service.route_question_with_llm = original_llm_route
            nodes.llm_answer_service.generate_answer = original_answer_generate

        self.assertEqual(resume_response.status_code, 200)
        resume_payload = resume_response.json()
        self.assertEqual(resume_payload["status"], "completed")
        self.assertFalse(resume_payload["need_human_review"])
        self.assertEqual(resume_payload["relevance_decision"], "approved_by_human")
        self.assertIn("采购复核的触发条件是单次采购金额超过二十万元", resume_payload["answer"])
        self.assertEqual(len(resume_payload["citations"]), 1)

    def test_chat_resume_rejected_stops_workflow(self) -> None:
        original_retrieve = nodes.rag_service.retrieve
        original_llm_route = nodes.llm_router_service.route_question_with_llm
        try:
            nodes.rag_service.retrieve = lambda *args, **kwargs: [
                RetrievedDocument(
                    doc_id=10,
                    chunk_id=20,
                    knowledge_item_id=30,
                    title="采购制度",
                    content="和问题关系不大的内容。",
                    score=0.12,
                    metadata={},
                )
            ]
            nodes.llm_router_service.route_question_with_llm = lambda *args, **kwargs: None

            start_response = self.client.post(
                "/api/chat",
                json={
                    "knowledge_base_id": self.knowledge_base_id,
                    "question": "一个需要人工判断的问题",
                },
            )
            self.assertEqual(start_response.status_code, 200)
            start_payload = start_response.json()
            self.assertEqual(start_payload["status"], "interrupted")

            resume_response = self.client.post(
                "/api/review/resume",
                json={
                    "thread_id": start_payload["thread_id"],
                    "approved": False,
                    "human_note": "证据不足，拒绝直接回答",
                },
            )
        finally:
            nodes.rag_service.retrieve = original_retrieve
            nodes.llm_router_service.route_question_with_llm = original_llm_route

        self.assertEqual(resume_response.status_code, 200)
        resume_payload = resume_response.json()
        self.assertEqual(resume_payload["status"], "completed")
        self.assertEqual(resume_payload["relevance_decision"], "rejected_by_human")
        self.assertIn("人工复核未通过", resume_payload["answer"])
        self.assertIn("证据不足，拒绝直接回答", resume_payload["answer"])
