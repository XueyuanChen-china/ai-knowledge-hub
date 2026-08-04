"""U10 安全路径：验证鉴权和跨组织资源不会泄露。"""

from __future__ import annotations

import os
import unittest

from .e2e_support import E2EClient, e2e_enabled


class AuthorizationBoundariesTest(unittest.TestCase):
    def test_unauthenticated_request_is_rejected(self) -> None:
        if not e2e_enabled():
            self.skipTest("set RUN_ENTERPRISE_E2E=1 to run external-service E2E")
        client = E2EClient(os.getenv("E2E_BASE_URL", "http://127.0.0.1:8000"), "")
        status, _ = client.request("/knowledge-bases")
        self.assertEqual(status, 401)

    def test_cross_organization_knowledge_base_is_not_visible(self) -> None:
        if not e2e_enabled():
            self.skipTest("set RUN_ENTERPRISE_E2E=1 to run external-service E2E")
        token = os.getenv("E2E_ACCESS_TOKEN", "")
        foreign_id = os.getenv("E2E_FOREIGN_KNOWLEDGE_BASE_ID", "")
        if not token or not foreign_id:
            self.skipTest("E2E_ACCESS_TOKEN and E2E_FOREIGN_KNOWLEDGE_BASE_ID are required")
        client = E2EClient(os.getenv("E2E_BASE_URL", "http://127.0.0.1:8000"), token)
        status, _ = client.request(f"/knowledge-bases/{foreign_id}")
        self.assertEqual(status, 404)

    def test_viewer_cannot_initialize_upload(self) -> None:
        if not e2e_enabled():
            self.skipTest("set RUN_ENTERPRISE_E2E=1 to run external-service E2E")
        token = os.getenv("E2E_VIEWER_ACCESS_TOKEN", "")
        knowledge_base_id = os.getenv("E2E_KNOWLEDGE_BASE_ID", "")
        if not token or not knowledge_base_id:
            self.skipTest("viewer token and knowledge base id are required")
        client = E2EClient(os.getenv("E2E_BASE_URL", "http://127.0.0.1:8000"), token)
        status, _ = client.request(
            "/uploads/init",
            "POST",
            {
                "knowledge_base_id": int(knowledge_base_id),
                "filename": "forbidden.txt",
                "file_size": 1,
            },
        )
        self.assertIn(status, {401, 403})


if __name__ == "__main__":
    unittest.main()
