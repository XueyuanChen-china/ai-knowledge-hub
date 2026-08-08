import unittest

from fastapi import HTTPException

from app.config import Settings, validate_oss_settings
from app.schemas.upload import UploadInitRequest
from app.services.upload_service import (
    build_object_key,
    calculate_total_parts,
    validate_upload_init_request,
)


class UploadServiceTests(unittest.TestCase):
    """上传服务纯函数测试。"""

    def build_settings(self) -> Settings:
        return Settings(
            database_url="postgresql+psycopg://postgres:postgres@localhost:5432/ai_knowledge_hub_test",
            oss_endpoint="oss-cn-shanghai.aliyuncs.com",
            oss_region="cn-shanghai",
            oss_bucket="ai-knowledge-hub-xueyuan-dev",
            oss_storage_prefix="raw/dev",
            oss_access_key_id="test-ak",
            oss_access_key_secret="test-sk",
            upload_default_part_size=5 * 1024 * 1024,
            upload_max_file_size=10 * 1024 * 1024 * 1024,
        )

    def test_build_object_key_uses_system_generated_path(self) -> None:
        object_key = build_object_key("raw/dev", 3, 7, "upl_abcd1234", ".pdf")
        self.assertEqual(
            object_key,
            "raw/dev/3/7/upl_abcd1234/source.pdf",
        )

    def test_validate_upload_init_request_rejects_path_traversal_filename(self) -> None:
        payload = UploadInitRequest(
            knowledge_base_id=1,
            filename="../evil.pdf",
            file_size=1024,
        )
        with self.assertRaises(HTTPException) as ctx:
            validate_upload_init_request(payload, self.build_settings())
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("invalid path segments", str(ctx.exception.detail))

    def test_validate_upload_init_request_rejects_unsupported_extension(self) -> None:
        payload = UploadInitRequest(
            knowledge_base_id=1,
            filename="archive.exe",
            file_size=1024,
        )
        with self.assertRaises(HTTPException) as ctx:
            validate_upload_init_request(payload, self.build_settings())
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("supported", str(ctx.exception.detail))

    def test_validate_upload_init_request_rejects_invalid_file_size(self) -> None:
        payload = UploadInitRequest(
            knowledge_base_id=1,
            filename="policy.pdf",
            file_size=0,
        )
        with self.assertRaises(HTTPException) as ctx:
            validate_upload_init_request(payload, self.build_settings())
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("greater than 0", str(ctx.exception.detail))

    def test_calculate_total_parts(self) -> None:
        total_parts = calculate_total_parts(
            file_size=734003200,
            part_size=5242880,
        )
        self.assertEqual(total_parts, 140)

    def test_validate_oss_settings_requires_non_empty_values(self) -> None:
        settings = self.build_settings()
        settings.oss_access_key_secret = ""
        with self.assertRaises(RuntimeError) as ctx:
            validate_oss_settings(settings)
        self.assertIn("OSS_ACCESS_KEY_SECRET", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
