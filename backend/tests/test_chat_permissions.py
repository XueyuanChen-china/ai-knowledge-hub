import sys
import unittest
from pathlib import Path

from sqlmodel import Session

BACKEND_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
for path in (BACKEND_DIR, TESTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.db.models import Conversation, Message
from resource_authorization_utils import ResourceAuthorizationTestCase


class ChatPermissionTests(ResourceAuthorizationTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.setUp_resource_authorization()
        with Session(self.engine) as session:
            conversation = Conversation(
                organization_id=self.organization_b_id,
                created_by_user_id=self.user_b_id,
                knowledge_base_id=self.knowledge_base_b_id,
                title="Organization B private chat",
                thread_id="thread-organization-b",
            )
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            session.add(
                Message(
                    conversation_id=conversation.id,
                    role="user",
                    content="Private question",
                )
            )
            session.commit()
            self.conversation_b_id = conversation.id

    def tearDown(self) -> None:
        self.tearDown_resource_authorization()

    def test_cross_organization_conversation_is_not_listed_or_readable(self) -> None:
        self.use_organization_a()
        listed = self.client.get("/api/conversations")
        messages = self.client.get(f"/api/conversations/{self.conversation_b_id}/messages")
        resumed = self.client.post(
            "/api/review/resume",
            json={"thread_id": "thread-organization-b", "approved": True},
        )

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json(), [])
        self.assertEqual(messages.status_code, 404)
        self.assertEqual(resumed.status_code, 404)


if __name__ == "__main__":
    unittest.main()
