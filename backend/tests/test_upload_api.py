import unittest
from hashlib import sha256
from pathlib import Path
from typing import Dict, List, Optional, Union

from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from app.config import Settings
from app.db.models import Document, KnowledgeBase, UploadPart, UploadProcessingJob, UploadTask
from app.main import app
from app.schemas.upload import UploadInitRequest
from app.services import upload_celery_service
from app.services import upload_postprocess_service
from app.services import upload_worker
from app.services.storage.base import MultipartUploadInitResult, StorageAdapterError
from app.services.storage.provider import get_object_storage_adapter
from app.services.upload_service import get_upload_task_or_404, init_upload_task
from app.tasks import upload_tasks
from tests.postgres_test_utils import PostgresTestDatabase


class FakeStorageAdapter:
    """测试用假对象存储。"""

    def __init__(self) -> None:
        self.abort_calls = 0
        self.raise_on_initiate = False
        self.raise_on_complete = False
        self.completed_payloads: List[List[Dict[str, Union[str, int]]]] = []
        self.remote_parts: Dict[int, Dict[str, Union[str, int]]] = {}
        self.object_bytes: bytes = b"hello upload"

    def initiate_multipart_upload(
        self,
        *,
        bucket_name: str,
        object_key: str,
        content_type: str = "",
    ) -> MultipartUploadInitResult:
        if self.raise_on_initiate:
            raise StorageAdapterError("boom")
        return MultipartUploadInitResult(upload_id="oss-upload-001")

    def generate_upload_part_presigned_url(
        self,
        *,
        bucket_name: str,
        object_key: str,
        upload_id: str,
        part_number: int,
        expire_seconds: int,
        content_type: str = "",
    ) -> str:
        return f"https://example.com/{upload_id}/{part_number}?expires={expire_seconds}"

    def complete_multipart_upload(
        self,
        *,
        bucket_name: str,
        object_key: str,
        upload_id: str,
        parts: List[Dict[str, Union[str, int]]],
    ) -> None:
        if self.raise_on_complete:
            raise StorageAdapterError("complete failed")
        self.completed_payloads.append(parts)

    def abort_multipart_upload(
        self,
        *,
        bucket_name: str,
        object_key: str,
        upload_id: str,
    ) -> None:
        self.abort_calls += 1

    def list_uploaded_parts(
        self,
        *,
        bucket_name: str,
        object_key: str,
        upload_id: str,
    ) -> List[Dict[str, Union[str, int]]]:
        return [
            {
                "part_number": part_number,
                "etag": value["etag"],
                "size": value["size"],
            }
            for part_number, value in sorted(self.remote_parts.items())
        ]

    def head_object(
        self,
        *,
        bucket_name: str,
        object_key: str,
    ) -> Optional[Dict[str, Union[str, int]]]:
        return None

    def get_object(
        self,
        *,
        bucket_name: str,
        object_key: str,
    ) -> bytes:
        return self.object_bytes

    def download_object_to_path(
        self,
        *,
        bucket_name: str,
        object_key: str,
        destination_path: str,
        head_bytes_limit: int,
        chunk_size: int = 1024 * 1024,
    ):
        Path(destination_path).write_bytes(self.object_bytes)

        class FakeDownloadResult:
            byte_count = len(self.object_bytes)
            sha256_hex = sha256(self.object_bytes).hexdigest()
            head_bytes = self.object_bytes[:head_bytes_limit]

        return FakeDownloadResult()

    def delete_object(
        self,
        *,
        bucket_name: str,
        object_key: str,
    ) -> None:
        return None


class UploadApiTests(unittest.TestCase):
    """上传任务接口测试。"""

    def setUp(self) -> None:
        self.test_db = PostgresTestDatabase()
        self.engine = self.test_db.create_engine()
        self.fake_storage = FakeStorageAdapter()
        self.settings = Settings(
            database_url=self.test_db.database_url,
            storage_provider="aliyun-oss",
            oss_endpoint="oss-cn-shanghai.aliyuncs.com",
            oss_region="cn-shanghai",
            oss_bucket="ai-knowledge-hub-xueyuan-dev",
            oss_access_key_id="test-ak",
            oss_access_key_secret="test-sk",
            oss_storage_prefix="raw/dev",
            oss_presign_expire_seconds=900,
            upload_default_part_size=5 * 1024 * 1024,
            upload_max_file_size=10 * 1024 * 1024 * 1024,
            upload_worker_enabled=False,
        )

        def override_get_session():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_object_storage_adapter] = lambda: self.fake_storage

        from app.config import get_settings
        from app.db.database import get_session

        app.dependency_overrides[get_session] = override_get_session
        app.dependency_overrides[get_settings] = lambda: self.settings
        self.client = TestClient(app)
        self.original_add_chunks = upload_postprocess_service.add_chunks
        self.original_storage_provider = upload_postprocess_service.get_object_storage_adapter
        self.original_postprocess_engine = upload_postprocess_service.engine
        self.original_worker_engine = upload_worker.engine
        self.original_hello_apply_async = upload_celery_service.upload_hello_task.apply_async
        self.original_download_apply_async = upload_celery_service.upload_download_stage_task.apply_async
        self.original_stage_apply_async = {
            stage: getattr(upload_tasks, f"upload_{stage}_stage_task").apply_async
            for stage in ["validate", "parse", "split", "embed", "index"]
        }
        self.original_embed_chunks = upload_postprocess_service.embed_chunks
        self.original_index_chunks = upload_postprocess_service.index_chunks
        self.celery_dispatch_payload = {}
        self.download_dispatch_payload = {}
        self.stage_dispatch_payloads = {}
        upload_postprocess_service.add_chunks = self.fake_add_chunks
        upload_postprocess_service.get_object_storage_adapter = lambda: self.fake_storage
        upload_postprocess_service.engine = self.engine
        upload_worker.engine = self.engine
        upload_celery_service.upload_hello_task.apply_async = self.fake_hello_apply_async
        upload_celery_service.upload_download_stage_task.apply_async = self.fake_download_apply_async
        for stage in self.original_stage_apply_async:
            getattr(upload_tasks, f"upload_{stage}_stage_task").apply_async = (
                self.make_fake_stage_apply_async(stage)
            )

        with Session(self.engine) as session:
            session.add(KnowledgeBase(name="测试知识库", description="upload test"))
            session.commit()

    def tearDown(self) -> None:
        upload_postprocess_service.add_chunks = self.original_add_chunks
        upload_postprocess_service.get_object_storage_adapter = self.original_storage_provider
        upload_postprocess_service.engine = self.original_postprocess_engine
        upload_worker.engine = self.original_worker_engine
        upload_celery_service.upload_hello_task.apply_async = self.original_hello_apply_async
        upload_celery_service.upload_download_stage_task.apply_async = self.original_download_apply_async
        for stage, original_apply_async in self.original_stage_apply_async.items():
            getattr(upload_tasks, f"upload_{stage}_stage_task").apply_async = original_apply_async
        upload_postprocess_service.embed_chunks = self.original_embed_chunks
        upload_postprocess_service.index_chunks = self.original_index_chunks
        app.dependency_overrides.clear()
        self.test_db.dispose()

    def fake_add_chunks(self, chunks):
        class FakeResult:
            index_name = "knowledge_chunks_test"
            vector_ids = [f"chunk_test_{index}" for index, _ in enumerate(chunks)]

        return FakeResult()

    def fake_hello_apply_async(self, *args, **kwargs):
        self.celery_dispatch_payload = {
            "args": kwargs.get("args"),
            "kwargs": kwargs.get("kwargs"),
            "queue": kwargs.get("queue"),
        }

        class FakeAsyncResult:
            id = "celery-task-test-id"

        return FakeAsyncResult()

    def fake_download_apply_async(self, *args, **kwargs):
        self.download_dispatch_payload = {
            "args": kwargs.get("args"),
            "kwargs": kwargs.get("kwargs"),
            "queue": kwargs.get("queue"),
        }

        class FakeAsyncResult:
            id = "celery-download-task-test-id"

        return FakeAsyncResult()

    def make_fake_stage_apply_async(self, stage: str):
        def fake_stage_apply_async(*args, **kwargs):
            self.stage_dispatch_payloads[stage] = {
                "args": kwargs.get("args"),
                "queue": kwargs.get("queue"),
            }

            class FakeAsyncResult:
                id = f"celery-{stage}-task-test-id"

            return FakeAsyncResult()

        return fake_stage_apply_async

    def fake_embed_chunks(self, chunks):
        return [[0.1] * self.settings.embedding_dimensions for _ in chunks]

    def fake_index_chunks(self, chunks, embeddings):
        class FakeResult:
            index_name = "knowledge_chunks_test"
            vector_ids = [f"pipeline_vector_{index}" for index, _ in enumerate(chunks)]

        return FakeResult()

    def create_upload(
        self,
        filename: str = "supplier-policy.pdf",
        file_size: int = 734003200,
        client_mime_type: str = "application/pdf",
        file_sha256: str = "",
    ) -> str:
        response = self.client.post(
            "/uploads/init",
            json={
                "knowledge_base_id": 1,
                "filename": filename,
                "file_size": file_size,
                "client_mime_type": client_mime_type,
                "file_sha256": file_sha256,
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["upload_id"]

    def test_init_upload_returns_task_and_persists_storage_upload_id(self) -> None:
        upload_id = self.create_upload()
        with Session(self.engine) as session:
            task = get_upload_task_or_404(upload_id, session)
            self.assertEqual(task.storage_upload_id, "oss-upload-001")
            self.assertEqual(task.part_size, 5242880)
            self.assertEqual(task.total_parts, 140)
            self.assertEqual(
                task.object_key,
                f"raw/dev/1/{upload_id}/source.pdf",
            )

    def test_get_upload_task_returns_404_for_missing_task(self) -> None:
        response = self.client.get("/uploads/upl_missing")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Upload task not found")

    def test_presign_part_returns_url_and_updates_status(self) -> None:
        upload_id = self.create_upload(file_size=10 * 1024 * 1024)
        response = self.client.post(
            f"/uploads/{upload_id}/parts/presign",
            json={"part_number": 1},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["part_number"], 1)
        self.assertEqual(data["expire_seconds"], 900)
        self.assertIn("oss-upload-001/1", data["presigned_url"])
        self.assertEqual(data["status"], "uploading")

        with Session(self.engine) as session:
            task = get_upload_task_or_404(upload_id, session)
            self.assertEqual(task.status, "uploading")

    def test_complete_uploaded_part_validates_remote_and_persists_local_part(self) -> None:
        upload_id = self.create_upload(file_size=10 * 1024 * 1024)
        self.fake_storage.remote_parts[1] = {"etag": "etag-1", "size": 5242880}

        response = self.client.post(
            f"/uploads/{upload_id}/parts/complete",
            json={
                "part_number": 1,
                "etag": "etag-1",
                "part_size": 5242880,
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["part_number"], 1)
        self.assertEqual(data["completed_parts"], 1)

        with Session(self.engine) as session:
            task = get_upload_task_or_404(upload_id, session)
            parts = list(
                session.exec(
                    select(UploadPart).where(UploadPart.upload_task_id == task.id)
                ).all()
            )
            self.assertEqual(len(parts), 1)
            self.assertEqual(parts[0].etag, "etag-1")
            self.assertEqual(parts[0].status, "uploaded")

    def test_complete_uploaded_part_accepts_quoted_etag(self) -> None:
        upload_id = self.create_upload(file_size=1024)
        self.fake_storage.remote_parts = {
            1: {"etag": "etag-quoted", "size": 1024},
        }

        response = self.client.post(
            f"/uploads/{upload_id}/parts/complete",
            json={
                "part_number": 1,
                "etag": '"etag-quoted"',
                "part_size": 1024,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "uploaded")

    def test_get_upload_parts_supports_resume_query(self) -> None:
        upload_id = self.create_upload(file_size=3 * 5242880)
        self.fake_storage.remote_parts = {
            1: {"etag": "etag-1", "size": 5242880},
            2: {"etag": "etag-2", "size": 5242880},
        }
        self.client.post(
            f"/uploads/{upload_id}/parts/complete",
            json={"part_number": 1, "etag": "etag-1", "part_size": 5242880},
        )

        response = self.client.get(f"/uploads/{upload_id}/parts")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total_parts"], 3)
        self.assertEqual(data["completed_parts"], 2)
        self.assertEqual(len(data["local_parts"]), 1)
        self.assertEqual(len(data["remote_parts"]), 2)
        self.assertEqual(data["missing_part_numbers"], [3])

    def test_complete_upload_validates_all_parts_and_auto_creates_document(self) -> None:
        # 这个测试保留旧的应用内完整流程；Phase C 的 Celery download 流程在下一个测试覆盖。
        self.settings.upload_processing_backend = "in_app"
        self.fake_storage.object_bytes = b"hello upload indexing"
        file_sha256 = sha256(self.fake_storage.object_bytes).hexdigest()
        upload_id = self.create_upload(
            filename="upload-test.txt",
            file_size=len(self.fake_storage.object_bytes),
            client_mime_type="text/plain",
            file_sha256=file_sha256,
        )
        self.fake_storage.remote_parts = {
            1: {"etag": "etag-1", "size": len(self.fake_storage.object_bytes)},
        }
        self.client.post(
            f"/uploads/{upload_id}/parts/complete",
            json={
                "part_number": 1,
                "etag": "etag-1",
                "part_size": len(self.fake_storage.object_bytes),
            },
        )

        response = self.client.post(
            f"/uploads/{upload_id}/complete",
            json={"expected_total_parts": 1},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["completed_parts"], 1)
        self.assertIn(data["processing_status"], ["pending", "queued"])
        self.assertIsNone(data["document_id"])
        self.assertIsNotNone(data["processing_job_id"])
        self.assertEqual(len(self.fake_storage.completed_payloads), 1)
        self.assertEqual(
            self.fake_storage.completed_payloads[0],
            [{"part_number": 1, "etag": "etag-1"}],
        )
        with Session(self.engine) as session:
            job = session.get(UploadProcessingJob, data["processing_job_id"])
            self.assertIsNotNone(job)
            self.assertEqual(job.job_type, "upload_pipeline")
            self.assertEqual(job.stage, "download")
            self.assertIsNone(job.depends_on_job_id)
            self.assertEqual(job.attempt_count, 0)
            self.assertEqual(job.max_attempts, self.settings.upload_job_max_retries + 1)
            self.assertEqual(job.celery_task_id, "")

        upload_postprocess_service.run_processing_job(
            job_id=data["processing_job_id"],
            settings=self.settings,
        )
        with Session(self.engine) as session:
            task = get_upload_task_or_404(upload_id, session)
            job = session.get(UploadProcessingJob, data["processing_job_id"])
            document = session.get(Document, task.document_id)
            self.assertIsNotNone(document)
            self.assertEqual(document.file_type, "txt")
            self.assertEqual(document.status, "indexed")
            self.assertEqual(task.processing_status, "completed")
            self.assertEqual(job.attempt_count, 1)

    def test_batch_presign_returns_multiple_urls(self) -> None:
        upload_id = self.create_upload(file_size=3 * 1024 * 1024)
        response = self.client.post(
            f"/uploads/{upload_id}/parts/presign-batch",
            json={"part_numbers": [1]},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["recommended_parallelism"], 3)
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["part_number"], 1)

    def test_dispatch_celery_hello_records_task_id_on_processing_job(self) -> None:
        self.settings.upload_processing_backend = "in_app"
        upload_id = self.create_upload(file_size=1024)
        self.fake_storage.remote_parts = {
            1: {"etag": "etag-1", "size": 1024},
        }
        self.client.post(
            f"/uploads/{upload_id}/parts/complete",
            json={"part_number": 1, "etag": "etag-1", "part_size": 1024},
        )
        complete_response = self.client.post(
            f"/uploads/{upload_id}/complete",
            json={"expected_total_parts": 1},
        )
        processing_job_id = complete_response.json()["processing_job_id"]

        response = self.client.post(
            f"/uploads/processing-jobs/{processing_job_id}/celery/hello",
            json={"message": "hello test"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["processing_job_id"], processing_job_id)
        self.assertEqual(data["celery_task_id"], "celery-task-test-id")
        self.assertEqual(data["queue"], "ai_knowledge_hub")
        self.assertEqual(data["current_step"], "celery_hello_dispatched")
        self.assertEqual(self.celery_dispatch_payload["args"], [processing_job_id])
        self.assertEqual(self.celery_dispatch_payload["kwargs"], {"message": "hello test"})

        with Session(self.engine) as session:
            job = session.get(UploadProcessingJob, processing_job_id)
            self.assertEqual(job.celery_task_id, "celery-task-test-id")
            self.assertEqual(job.current_step, "celery_hello_dispatched")

    def test_complete_upload_dispatches_download_stage_to_celery(self) -> None:
        upload_id = self.create_upload(
            filename="download-stage.txt",
            file_size=1024,
            client_mime_type="text/plain",
        )
        self.fake_storage.remote_parts = {
            1: {"etag": "etag-1", "size": 1024},
        }
        self.client.post(
            f"/uploads/{upload_id}/parts/complete",
            json={"part_number": 1, "etag": "etag-1", "part_size": 1024},
        )

        response = self.client.post(
            f"/uploads/{upload_id}/complete",
            json={"expected_total_parts": 1},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        processing_job_id = data["processing_job_id"]
        self.assertEqual(data["processing_status"], "pending")
        self.assertEqual(
            self.download_dispatch_payload,
            {
                "args": [processing_job_id],
                "kwargs": None,
                "queue": "ai_knowledge_hub",
            },
        )

        with Session(self.engine) as session:
            job = session.get(UploadProcessingJob, processing_job_id)
            self.assertIsNotNone(job)
            self.assertEqual(job.stage, "download")
            self.assertEqual(job.status, "pending")
            self.assertEqual(job.current_step, "celery_download_dispatched")
            self.assertEqual(job.celery_task_id, "celery-download-task-test-id")
            self.assertEqual(job.attempt_count, 0)

    def test_download_stage_worker_downloads_and_marks_job_completed(self) -> None:
        self.fake_storage.object_bytes = b"worker download stage"
        file_sha256 = sha256(self.fake_storage.object_bytes).hexdigest()
        upload_id = self.create_upload(
            filename="download-stage.txt",
            file_size=len(self.fake_storage.object_bytes),
            client_mime_type="text/plain",
            file_sha256=file_sha256,
        )
        self.fake_storage.remote_parts = {
            1: {"etag": "etag-1", "size": len(self.fake_storage.object_bytes)},
        }
        self.client.post(
            f"/uploads/{upload_id}/parts/complete",
            json={
                "part_number": 1,
                "etag": "etag-1",
                "part_size": len(self.fake_storage.object_bytes),
            },
        )
        response = self.client.post(
            f"/uploads/{upload_id}/complete",
            json={"expected_total_parts": 1},
        )
        processing_job_id = response.json()["processing_job_id"]

        result = upload_postprocess_service.run_download_stage_job(
            job_id=processing_job_id,
            settings=self.settings,
            celery_task_id="celery-download-task-test-id",
            continue_pipeline=False,
        )

        self.assertEqual(result.processing_status, "completed")
        with Session(self.engine) as session:
            task = get_upload_task_or_404(upload_id, session)
            job = session.get(UploadProcessingJob, processing_job_id)
            self.assertEqual(job.status, "completed")
            self.assertEqual(job.current_step, "download_completed")
            self.assertEqual(job.attempt_count, 1)
            self.assertEqual(job.celery_task_id, "celery-download-task-test-id")
            self.assertEqual(task.status, "completed")
            self.assertEqual(task.processing_status, "completed")
            self.assertEqual(task.detected_mime_type, "text/plain")
            self.assertIsNone(task.document_id)

    def test_complete_upload_runs_all_pipeline_stages_to_indexed(self) -> None:
        self.fake_storage.object_bytes = b"full pipeline document text"
        file_sha256 = sha256(self.fake_storage.object_bytes).hexdigest()
        upload_id = self.create_upload(
            filename="full-pipeline.txt",
            file_size=len(self.fake_storage.object_bytes),
            client_mime_type="text/plain",
            file_sha256=file_sha256,
        )
        self.fake_storage.remote_parts = {
            1: {"etag": "etag-1", "size": len(self.fake_storage.object_bytes)},
        }
        self.client.post(
            f"/uploads/{upload_id}/parts/complete",
            json={
                "part_number": 1,
                "etag": "etag-1",
                "part_size": len(self.fake_storage.object_bytes),
            },
        )
        response = self.client.post(
            f"/uploads/{upload_id}/complete",
            json={"expected_total_parts": 1},
        )
        first_job_id = response.json()["processing_job_id"]

        upload_postprocess_service.embed_chunks = self.fake_embed_chunks
        upload_postprocess_service.index_chunks = self.fake_index_chunks
        upload_postprocess_service.run_download_stage_job(
            job_id=first_job_id,
            settings=self.settings,
        )

        completed_job_ids = [first_job_id]
        for stage in ["validate", "parse", "split", "embed", "index"]:
            with Session(self.engine) as session:
                job = session.exec(
                    select(UploadProcessingJob)
                    .where(
                        UploadProcessingJob.upload_task_id
                        == get_upload_task_or_404(upload_id, session).id,
                        UploadProcessingJob.stage == stage,
                    )
                    .order_by(UploadProcessingJob.id.desc())
                ).first()
                self.assertIsNotNone(job)
                stage_job_id = job.id
                previous_job_id = job.depends_on_job_id

            upload_postprocess_service.run_pipeline_stage_job(
                job_id=stage_job_id,
                settings=self.settings,
                celery_task_id=f"celery-{stage}-worker-id",
            )
            completed_job_ids.append(stage_job_id)
            with Session(self.engine) as session:
                completed_job = session.get(UploadProcessingJob, stage_job_id)
                self.assertEqual(completed_job.status, "completed")
                self.assertEqual(completed_job.stage, stage)
                self.assertEqual(completed_job.depends_on_job_id, previous_job_id)

        with Session(self.engine) as session:
            task = get_upload_task_or_404(upload_id, session)
            document = session.get(Document, task.document_id)
            jobs = list(
                session.exec(
                    select(UploadProcessingJob)
                    .where(UploadProcessingJob.upload_task_id == task.id)
                    .order_by(UploadProcessingJob.id)
                ).all()
            )
            self.assertEqual([job.stage for job in jobs], [
                "download", "validate", "parse", "split", "embed", "index"
            ])
            self.assertTrue(all(job.status == "completed" for job in jobs))
            self.assertEqual(task.processing_status, "completed")
            self.assertEqual(document.status, "indexed")
            self.assertGreater(len(jobs), 0)

    def test_cleanup_expired_marks_old_task_expired(self) -> None:
        upload_id = self.create_upload(file_size=1024)
        with Session(self.engine) as session:
            task = get_upload_task_or_404(upload_id, session)
            task.expires_at = task.created_at
            session.add(task)
            session.commit()

        response = self.client.post("/uploads/cleanup-expired")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["expired_count"], 1)
        self.assertEqual(response.json()["aborted_remote_count"], 1)
        with Session(self.engine) as session:
            task = get_upload_task_or_404(upload_id, session)
            self.assertEqual(task.status, "expired")

    def test_worker_claims_and_processes_due_jobs(self) -> None:
        self.settings.upload_processing_backend = "in_app"
        self.fake_storage.object_bytes = b"worker queue run"
        file_sha256 = sha256(self.fake_storage.object_bytes).hexdigest()
        upload_id = self.create_upload(
            filename="worker-test.txt",
            file_size=len(self.fake_storage.object_bytes),
            client_mime_type="text/plain",
            file_sha256=file_sha256,
        )
        self.fake_storage.remote_parts = {
            1: {"etag": "etag-1", "size": len(self.fake_storage.object_bytes)},
        }
        self.client.post(
            f"/uploads/{upload_id}/parts/complete",
            json={
                "part_number": 1,
                "etag": "etag-1",
                "part_size": len(self.fake_storage.object_bytes),
            },
        )
        response = self.client.post(
            f"/uploads/{upload_id}/complete",
            json={"expected_total_parts": 1},
        )
        processing_job_id = response.json()["processing_job_id"]
        claimed_jobs = upload_worker.claim_due_upload_processing_jobs(
            settings=self.settings,
            limit=10,
            worker_id="test-worker",
        )
        claimed_job_ids = [job.job_id for job in claimed_jobs]
        self.assertIn(processing_job_id, claimed_job_ids)
        upload_postprocess_service.run_processing_job(
            job_id=processing_job_id,
            settings=self.settings,
            claim_token=claimed_jobs[0].claim_token,
        )
        with Session(self.engine) as session:
            task = get_upload_task_or_404(upload_id, session)
            self.assertEqual(task.processing_status, "completed")

    def test_worker_claim_prevents_duplicate_claims_before_lease_expires(self) -> None:
        with Session(self.engine) as session:
            task = UploadTask(
                upload_id="upl_claim_once",
                knowledge_base_id=1,
                original_filename="policy.txt",
                storage_provider="aliyun-oss",
                bucket_name="ai-knowledge-hub-xueyuan-dev",
                object_key="raw/dev/1/upl_claim_once/source.txt",
                file_type="txt",
                file_size=1024,
                part_size=5242880,
                total_parts=1,
                storage_upload_id="oss-upload-001",
                status="completed",
            )
            session.add(task)
            session.commit()
            session.refresh(task)
            session.add(
                UploadProcessingJob(
                    upload_task_id=task.id,
                    job_type="parse_index",
                    status="pending",
                )
            )
            session.commit()

        first_claim = upload_worker.claim_due_upload_processing_jobs(
            settings=self.settings,
            limit=10,
            worker_id="test-worker-a",
        )
        second_claim = upload_worker.claim_due_upload_processing_jobs(
            settings=self.settings,
            limit=10,
            worker_id="test-worker-b",
        )

        self.assertEqual(len(first_claim), 1)
        self.assertEqual(second_claim, [])

        with Session(self.engine) as session:
            job = session.get(UploadProcessingJob, first_claim[0].job_id)
            self.assertEqual(job.status, "queued")
            self.assertEqual(job.locked_by, "test-worker-a")
            self.assertEqual(job.claim_token, first_claim[0].claim_token)
            self.assertIsNotNone(job.lease_expires_at)

    def test_abort_upload_calls_storage_and_marks_cancelled(self) -> None:
        upload_id = self.create_upload(file_size=10 * 1024 * 1024)
        response = self.client.post(f"/uploads/{upload_id}/abort")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancelled")
        self.assertEqual(self.fake_storage.abort_calls, 1)

    def test_complete_part_rejects_when_remote_part_not_found(self) -> None:
        upload_id = self.create_upload(file_size=10 * 1024 * 1024)
        response = self.client.post(
            f"/uploads/{upload_id}/parts/complete",
            json={"part_number": 1, "etag": "etag-1"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("has not been uploaded", response.json()["detail"])

    def test_init_upload_returns_502_when_storage_init_fails(self) -> None:
        self.fake_storage.raise_on_initiate = True
        response = self.client.post(
            "/uploads/init",
            json={
                "knowledge_base_id": 1,
                "filename": "policy.pdf",
                "file_size": 100,
            },
        )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json()["detail"],
            "Failed to initialize object storage multipart upload",
        )

        with Session(self.engine) as session:
            tasks = list(session.exec(select(UploadTask)).all())
            self.assertEqual(tasks, [])

    def test_init_upload_aborts_storage_when_database_write_fails(self) -> None:
        original_do_commit = self.engine.dialect.do_commit

        def broken_do_commit(dbapi_connection):
            raise SQLAlchemyError("commit failure")

        self.engine.dialect.do_commit = broken_do_commit
        try:
            with Session(self.engine) as session:
                with self.assertRaises(SQLAlchemyError):
                    init_upload_task(
                        payload=UploadInitRequest(
                            knowledge_base_id=1,
                            filename="policy.pdf",
                            file_size=100,
                        ),
                        session=session,
                        settings=self.settings,
                        storage=self.fake_storage,
                    )
        finally:
            self.engine.dialect.do_commit = original_do_commit

        self.assertEqual(self.fake_storage.abort_calls, 1)

    def test_upload_parts_unique_constraint(self) -> None:
        with Session(self.engine) as session:
            task = UploadTask(
                upload_id="upl_constraint",
                knowledge_base_id=1,
                original_filename="policy.pdf",
                storage_provider="aliyun-oss",
                bucket_name="ai-knowledge-hub-xueyuan-dev",
                object_key="raw/dev/1/upl_constraint/source.pdf",
                file_type="pdf",
                file_size=1024,
                part_size=5242880,
                total_parts=1,
                storage_upload_id="oss-upload-001",
                status="initiated",
            )
            session.add(task)
            session.commit()
            session.refresh(task)

            session.add(
                UploadPart(
                    upload_task_id=task.id,
                    part_number=1,
                    etag="etag-1",
                    part_size=1024,
                    status="uploaded",
                )
            )
            session.commit()

            session.add(
                UploadPart(
                    upload_task_id=task.id,
                    part_number=1,
                    etag="etag-2",
                    part_size=1024,
                    status="uploaded",
                )
            )
            with self.assertRaises(IntegrityError):
                session.commit()


if __name__ == "__main__":
    unittest.main()
