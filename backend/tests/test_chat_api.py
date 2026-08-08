import sys
import unittest
from pathlib import Path
import json

from fastapi.testclient import TestClient
from sqlmodel import Session, select

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from app.db.database import get_session
from app.db.models import Conversation, KnowledgeBase, Message, ReviewTask
from app.graph import nodes
from app.main import app
from app.security.dependencies import get_current_principal
from app.services import rag_service
from app.services.rag_service import RetrievedDocument
from postgres_test_utils import PostgresTestDatabase
from resource_authorization_utils import create_test_identity


class ChatApiTests(unittest.TestCase):
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
            knowledge_base = KnowledgeBase(
                name="制度库",
                description="chat api test",
                organization_id=self.principal.organization_id,
                created_by_user_id=self.principal.user_id,
            )
            session.add(knowledge_base)
            session.commit()
            session.refresh(knowledge_base)
            self.knowledge_base_id = knowledge_base.id

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.test_database.dispose()

    def parse_sse_events(self, raw_text: str) -> list[tuple[str, object]]:
        events: list[tuple[str, object]] = []
        for block in raw_text.split("\n\n"):
            block = block.strip()
            if not block:
                continue

            event_name = ""
            data_lines: list[str] = []
            for line in block.splitlines():
                if line.startswith("event:"):
                    event_name = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data_lines.append(line.split(":", 1)[1].strip())

            if not event_name or not data_lines:
                continue

            raw_data = "\n".join(data_lines)
            if event_name == "answer":
                events.append((event_name, raw_data))
            else:
                events.append((event_name, json.loads(raw_data)))

        return events

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
                lambda question, documents, **kwargs: rag_service.RagAnswerResult(
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

    def test_chat_stream_returns_completed_events(self) -> None:
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
                    score=0.92,
                    metadata={},
                )
            ]
            nodes.llm_router_service.route_question_with_llm = lambda *args, **kwargs: None
            nodes.llm_answer_service.generate_answer = (
                lambda question, documents, **kwargs: rag_service.RagAnswerResult(
                    answer="采购复核的触发条件是单次采购金额超过二十万元。\n\n参考来源：[1]",
                    context=rag_service.format_context(documents),
                    citations=rag_service.build_citations(documents),
                    used_fallback=False,
                )
            )

            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={
                    "knowledge_base_id": self.knowledge_base_id,
                    "question": "采购复核的触发条件是什么？",
                },
            ) as response:
                raw_text = "".join(response.iter_text())
        finally:
            nodes.rag_service.retrieve = original_retrieve
            nodes.llm_router_service.route_question_with_llm = original_llm_route
            nodes.llm_answer_service.generate_answer = original_answer_generate

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        events = self.parse_sse_events(raw_text)
        event_names = [event_name for event_name, _ in events]
        self.assertIn("start", event_names)
        self.assertIn("node", event_names)
        self.assertIn("answer", event_names)
        self.assertIn("references", event_names)
        self.assertIn("completed", event_names)

        answer_chunks = [payload for name, payload in events if name == "answer"]
        self.assertTrue(any("采购复核的触发条件" in str(chunk) for chunk in answer_chunks))
        references_payload = [payload for name, payload in events if name == "references"][-1]
        self.assertEqual(references_payload, [1])
        completed_payload = [payload for name, payload in events if name == "completed"][-1]
        self.assertEqual(completed_payload["status"], "completed")
        self.assertEqual(completed_payload["route"], "rag")
        self.assertEqual(completed_payload["retrieval_hit_count"], 1)
        self.assertIn("采购复核的触发条件是单次采购金额超过二十万元", completed_payload["answer"])

    def test_chat_stream_returns_interrupted_event(self) -> None:
        original_retrieve = nodes.rag_service.retrieve
        original_llm_route = nodes.llm_router_service.route_question_with_llm
        try:
            nodes.rag_service.retrieve = lambda *args, **kwargs: []
            nodes.llm_router_service.route_question_with_llm = lambda *args, **kwargs: None

            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={
                    "knowledge_base_id": self.knowledge_base_id,
                    "question": "一个知识库里不存在的问题",
                },
            ) as response:
                raw_text = "".join(response.iter_text())
        finally:
            nodes.rag_service.retrieve = original_retrieve
            nodes.llm_router_service.route_question_with_llm = original_llm_route

        self.assertEqual(response.status_code, 200)
        events = self.parse_sse_events(raw_text)
        interrupted_payload = [payload for name, payload in events if name == "interrupted"][-1]
        self.assertEqual(interrupted_payload["status"], "interrupted")
        self.assertTrue(interrupted_payload["need_human_review"])
        self.assertEqual(interrupted_payload["review_reason"], "no retrieved documents")

    def test_chat_interrupts_when_high_score_docs_miss_critical_entity(self) -> None:
        original_retrieve = nodes.rag_service.retrieve
        original_llm_route = nodes.llm_router_service.route_question_with_llm
        try:
            nodes.rag_service.retrieve = lambda *args, **kwargs: [
                RetrievedDocument(
                    doc_id=10,
                    chunk_id=20,
                    knowledge_item_id=30,
                    title="采购制度",
                    content="R-002 供应商交付延迟，由采购负责人跟进。",
                    score=0.91,
                    metadata={"heading_path": ["风险清单"]},
                )
            ]
            nodes.llm_router_service.route_question_with_llm = lambda *args, **kwargs: None

            response = self.client.post(
                "/api/chat",
                json={
                    "knowledge_base_id": self.knowledge_base_id,
                    "question": "R-001 风险由谁负责？",
                },
            )
        finally:
            nodes.rag_service.retrieve = original_retrieve
            nodes.llm_router_service.route_question_with_llm = original_llm_route

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "interrupted")
        self.assertTrue(payload["need_human_review"])
        self.assertEqual(payload["relevance_decision"], "need_review")
        self.assertEqual(
            payload["review_reason"],
            "retrieved docs do not cover critical query entities: r-001",
        )

    def test_list_conversations_returns_persisted_threads(self) -> None:
        with Session(self.engine) as session:
            first = Conversation(
                knowledge_base_id=self.knowledge_base_id,
                title="第一次问题",
                thread_id="thread-1",
                organization_id=self.principal.organization_id,
                created_by_user_id=self.principal.user_id,
            )
            second = Conversation(
                knowledge_base_id=self.knowledge_base_id,
                title="第二次问题",
                thread_id="thread-2",
                organization_id=self.principal.organization_id,
                created_by_user_id=self.principal.user_id,
            )
            session.add(first)
            session.add(second)
            session.commit()
            session.refresh(first)
            session.refresh(second)

            session.add(
                Message(
                    conversation_id=first.id,
                    role="user",
                    content="第一次问题的内容",
                    metadata_json="",
                )
            )
            session.add(
                Message(
                    conversation_id=second.id,
                    role="assistant",
                    content="第二次问题的回答内容",
                    metadata_json=json.dumps({"citations": []}, ensure_ascii=False),
                )
            )
            second.updated_at = second.updated_at.replace(year=second.updated_at.year + 1)
            session.add(second)
            session.commit()

        response = self.client.get(
            "/api/conversations",
            params={"knowledge_base_id": self.knowledge_base_id},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["title"], "第二次问题")
        self.assertEqual(payload[0]["last_message_role"], "assistant")
        self.assertEqual(payload[0]["message_count"], 1)
        self.assertIn("回答内容", payload[0]["last_message_preview"])

    def test_list_conversation_messages_returns_history_and_citations(self) -> None:
        with Session(self.engine) as session:
            conversation = Conversation(
                knowledge_base_id=self.knowledge_base_id,
                title="采购问题",
                thread_id="thread-history",
                organization_id=self.principal.organization_id,
                created_by_user_id=self.principal.user_id,
            )
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            conversation_id = conversation.id

            session.add(
                Message(
                    conversation_id=conversation_id,
                    role="user",
                    content="采购复核的触发条件是什么？",
                    metadata_json="",
                )
            )
            session.add(
                Message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content="单次采购金额超过二十万元。",
                    metadata_json=json.dumps(
                        {
                            "citations": [
                                {
                                    "doc_id": 8,
                                    "chunk_id": 51,
                                    "knowledge_item_id": 8,
                                    "title": "采购制度",
                                    "score": 0.79,
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            session.commit()

        response = self.client.get(
            f"/api/conversations/{conversation_id}/messages",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["role"], "user")
        self.assertEqual(payload[1]["role"], "assistant")
        self.assertEqual(len(payload[1]["citations"]), 1)
        self.assertEqual(payload[1]["citations"][0]["chunk_id"], 51)

    def test_update_conversation_can_rename_and_pin(self) -> None:
        with Session(self.engine) as session:
            conversation = Conversation(
                knowledge_base_id=self.knowledge_base_id,
                title="旧标题",
                thread_id="thread-update",
                organization_id=self.principal.organization_id,
                created_by_user_id=self.principal.user_id,
            )
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            conversation_id = conversation.id

        response = self.client.patch(
            f"/api/conversations/{conversation_id}",
            json={"title": "新标题", "is_pinned": True},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["title"], "新标题")
        self.assertTrue(payload["is_pinned"])

        list_response = self.client.get(
            "/api/conversations",
            params={"knowledge_base_id": self.knowledge_base_id},
        )
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()[0]["title"], "新标题")

    def test_delete_conversation_removes_messages_and_review_tasks(self) -> None:
        with Session(self.engine) as session:
            conversation = Conversation(
                knowledge_base_id=self.knowledge_base_id,
                title="待删除会话",
                thread_id="thread-delete",
                organization_id=self.principal.organization_id,
                created_by_user_id=self.principal.user_id,
            )
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            conversation_id = conversation.id

            session.add(
                Message(
                    conversation_id=conversation_id,
                    role="user",
                    content="删除测试",
                    metadata_json="",
                )
            )
            session.add(
                ReviewTask(
                    conversation_id=conversation_id,
                    question="删除测试",
                    docs_preview="[]",
                    status="pending",
                )
            )
            session.commit()

        response = self.client.delete(f"/api/conversations/{conversation_id}")

        self.assertEqual(response.status_code, 204)
        with Session(self.engine) as session:
            self.assertIsNone(session.get(Conversation, conversation_id))
            messages = session.exec(
                select(Message).where(Message.conversation_id == conversation_id)
            ).all()
            review_tasks = session.exec(
                select(ReviewTask).where(ReviewTask.conversation_id == conversation_id)
            ).all()
            self.assertEqual(messages, [])
            self.assertEqual(review_tasks, [])
