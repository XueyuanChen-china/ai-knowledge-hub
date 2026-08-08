import sys
import unittest
from pathlib import Path

from sqlmodel import Session

BACKEND_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
for path in (BACKEND_DIR, TESTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.db.models import Document, KnowledgeItem
from resource_authorization_utils import ResourceAuthorizationTestCase


class ResourceAuthorizationTests(ResourceAuthorizationTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.setUp_resource_authorization()
        with Session(self.engine) as session:
            self.document_b = Document(
                organization_id=self.organization_b_id,
                created_by_user_id=self.user_b_id,
                knowledge_base_id=self.knowledge_base_b_id,
                filename="private.txt",
                file_path="/tmp/private.txt",
                file_type="txt",
            )
            self.item_b = KnowledgeItem(
                organization_id=self.organization_b_id,
                created_by_user_id=self.user_b_id,
                knowledge_base_id=self.knowledge_base_b_id,
                title="Private item",
                content="Only organization B can read this.",
            )
            session.add(self.document_b)
            session.add(self.item_b)
            session.commit()
            session.refresh(self.document_b)
            session.refresh(self.item_b)

    def tearDown(self) -> None:
        self.tearDown_resource_authorization()

    def test_cross_organization_resource_ids_return_not_found(self) -> None:
        self.use_organization_a()
        self.assertEqual(
            self.client.get(f"/knowledge-bases/{self.knowledge_base_b_id}").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/documents/{self.document_b.id}/chunks").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/knowledge-items/{self.item_b.id}").status_code,
            404,
        )

    def test_knowledge_base_delete_reports_dependencies(self) -> None:
        self.use_organization_b(role="owner")
        response = self.client.delete(f"/knowledge-bases/{self.knowledge_base_b_id}")
        self.assertEqual(response.status_code, 409)
        self.assertIn("documents", response.json()["detail"]["dependencies"])
        self.assertIn("knowledge_items", response.json()["detail"]["dependencies"])


if __name__ == "__main__":
    unittest.main()
