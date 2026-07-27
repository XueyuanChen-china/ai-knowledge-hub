import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from sqlmodel import Session

BACKEND_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
for path in (BACKEND_DIR, TESTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.db.models import UploadTask
from resource_authorization_utils import ResourceAuthorizationTestCase


class UploadPermissionTests(ResourceAuthorizationTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.setUp_resource_authorization()
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
