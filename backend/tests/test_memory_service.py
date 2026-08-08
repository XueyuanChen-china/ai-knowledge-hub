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

from app.db.models import Conversation, KnowledgeBase, Message
from app.services.context_manager import build_context_pack
from app.services.memory_service import (
    archive_memory,
    capture_explicit_memories,
    extract_explicit_memory_candidates,
    list_active_memories,
)
from postgres_test_utils import PostgresTestDatabase
from resource_authorization_utils import create_test_identity


class MemoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = PostgresTestDatabase()
        self.engine = self.database.create_engine()
        self.session = Session(self.engine)
        self.principal = create_test_identity(self.session)
        knowledge_base = KnowledgeBase(
            name="记忆测试知识库",
            description="会话长期记忆测试",
            organization_id=self.principal.organization_id,
            created_by_user_id=self.principal.user_id,
        )
        self.session.add(knowledge_base)
        self.session.commit()
        self.session.refresh(knowledge_base)
        self.conversation = Conversation(
            organization_id=self.principal.organization_id,
            created_by_user_id=self.principal.user_id,
            knowledge_base_id=knowledge_base.id,
            title="记忆测试",
            thread_id="memory-service-test",
        )
        self.session.add(self.conversation)
        self.session.commit()
        self.session.refresh(self.conversation)

    def tearDown(self) -> None:
        self.session.close()
        self.database.dispose()

    def test_only_explicit_memory_instruction_creates_candidate(self) -> None:
        candidates = extract_explicit_memory_candidates(
            "记住：所有搜索必须按 organization_id 过滤。"
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].memory_type, "constraint")
        self.assertEqual(candidates[0].content, "所有搜索必须按 organization_id 过滤")
        self.assertEqual(extract_explicit_memory_candidates("帮我解释 BM25"), [])

    def test_capture_deduplicates_and_scopes_memories(self) -> None:
        message = Message(
            conversation_id=self.conversation.id,
            role="user",
            content="记住：所有搜索必须按 organization_id 过滤。",
        )
        self.session.add(message)
        self.session.commit()
        self.session.refresh(message)

        saved = capture_explicit_memories(
            conversation_id=self.conversation.id,
            message=message,
            session=self.session,
        )
        duplicate = capture_explicit_memories(
            conversation_id=self.conversation.id,
            message=message,
            session=self.session,
        )

        self.assertEqual(len(saved), 1)
        self.assertEqual(duplicate, [])
        memories = list_active_memories(
            conversation_id=self.conversation.id,
            organization_id=self.principal.organization_id,
            user_id=self.principal.user_id,
            role="viewer",
            session=self.session,
        )
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0].source_message_id, message.id)
        self.assertEqual(
            list_active_memories(
                conversation_id=self.conversation.id,
                organization_id=self.principal.organization_id,
                user_id=self.principal.user_id + 100,
                role="viewer",
                session=self.session,
            ),
            [],
        )

    def test_archive_removes_memory_from_active_pack_source(self) -> None:
        message = Message(
            conversation_id=self.conversation.id,
            role="user",
            content="最终决定使用 RabbitMQ + Celery。",
        )
        self.session.add(message)
        self.session.commit()
        self.session.refresh(message)
        saved = capture_explicit_memories(
            conversation_id=self.conversation.id,
            message=message,
            session=self.session,
        )

        archived = archive_memory(
            memory_id=saved[0].id,
            conversation_id=self.conversation.id,
            organization_id=self.principal.organization_id,
            user_id=self.principal.user_id,
            role="viewer",
            session=self.session,
        )

        self.assertIsNotNone(archived)
        self.assertEqual(archived.status, "archived")
        self.assertEqual(
            list_active_memories(
                conversation_id=self.conversation.id,
                organization_id=self.principal.organization_id,
                user_id=self.principal.user_id,
                role="viewer",
                session=self.session,
            ),
            [],
        )

    def test_persistent_memory_enters_pack_as_atomic_item(self) -> None:
        pack = build_context_pack(
            purpose="answer",
            messages=[
                {"role": "user", "content": f"普通历史消息 {index}"}
                for index in range(20)
            ],
            persistent_memory=[
                {
                    "kind": "constraint",
                    "content": "所有搜索必须按 organization_id 过滤。",
                    "source_ids": ["memory-1"],
                    "importance": 1.0,
                    "pinned": True,
                }
            ],
        )

        self.assertEqual(len(pack.persistent_memory), 1)
        self.assertIn("organization_id", pack.persistent_memory[0].content)
        self.assertGreater(pack.budget.persistent_memory, 0)

    def test_oversized_persistent_memory_is_omitted_whole(self) -> None:
        pack = build_context_pack(
            purpose="rewrite",
            messages=[],
            persistent_memory=[
                {
                    "kind": "constraint",
                    "content": "重要约束。" * 1000,
                    "source_ids": ["memory-large"],
                    "importance": 1.0,
                    "pinned": True,
                }
            ],
        )

        self.assertEqual(pack.persistent_memory, [])
        self.assertTrue(pack.truncated)
        self.assertTrue(any(item.source_id == "memory-large" for item in pack.omitted_items))


if __name__ == "__main__":
    unittest.main()
