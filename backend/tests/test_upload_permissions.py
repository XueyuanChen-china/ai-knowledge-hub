import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from sqlmodel import Session

BACKEND_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
for path in (BACKEND_DIR, TESTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.db.models import UploadTask
from app.main import app
from app.services.storage.provider import get_object_storage_adapter
from resource_authorization_utils import ResourceAuthorizationTestCase


class UploadPermissionTests(ResourceAuthorizationTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.setUp_resource_authorization()
        # 这些用例只验证组织边界和角色权限，不应要求 CI 提供真实 OSS 密钥。
        # FastAPI 会在路由函数执行前解析 storage dependency，因此即使最终
        # 预期是 404/403，也必须显式注入测试替身。
        app.dependency_overrides[get_object_storage_adapter] = lambda: Mock()
        with Session(self.engine) as session:
            task = UploadTask(
                organization_id=self.organization_b_id,
                created_by_user_id=self.user_b_id,
                upload_id="upl_organization_b",
                knowledge_base_id=self.knowledge_base_b_id,
                original_filename="private.pdf",
                storage_provider="aliyun-oss",
                bucket_name="test-bucket",
                object_key="raw/dev/2/2/upl_organization_b/source.pdf",
                file_type="pdf",
                file_size=10,
                part_size=10,
                total_parts=1,
                storage_upload_id="storage-upload-b",
                expires_at=datetime.utcnow() + timedelta(hours=1),
            )
            session.add(task)
            session.commit()

    def tearDown(self) -> None:
        self.tearDown_resource_authorization()

    def test_cross_organization_upload_task_is_hidden(self) -> None:
        self.use_organization_a()
        self.assertEqual(
            self.client.get("/uploads/upl_organization_b").status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                "/uploads/upl_organization_b/parts/presign",
                json={"part_number": 1},
            ).status_code,
            404,
        )

    def test_viewer_cannot_receive_presigned_upload_url(self) -> None:
        self.use_organization_b(role="viewer")
        response = self.client.post(
            "/uploads/upl_organization_b/parts/presign",
            json={"part_number": 1},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
