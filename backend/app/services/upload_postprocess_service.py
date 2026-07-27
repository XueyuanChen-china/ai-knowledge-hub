import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
from threading import BoundedSemaphore
from typing import Optional

from sqlmodel import Session, select

from app.api.document import (
    extract_pdf_pages,
    extract_text_from_file,
    get_existing_document_vector_ids,
    regenerate_document_chunks,
)
from app.config import Settings
from app.db.database import engine
from app.db.models import Chunk, Document, UploadProcessingJob, UploadTask
from app.services.storage.base import DownloadObjectResult
from app.services.storage.provider import get_object_storage_adapter
from app.services.upload_audit_service import log_upload_event
from app.services.vector_service import (
    add_chunks,
    delete_vectors,
    embed_chunks,
    index_chunks,
)

UPLOAD_DOWNLOAD_DIR = Path("data/uploads")

JOB_STATUS_PENDING = "pending"
JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_RETRY_SCHEDULED = "retry_scheduled"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_FAILED = "failed"

UPLOAD_PROCESSING_BACKEND_CELERY = "celery"

JOB_TYPE_UPLOAD_PIPELINE = "upload_pipeline"
JOB_STAGE_DOWNLOAD = "download"
JOB_STAGE_VALIDATE = "validate"
JOB_STAGE_PARSE = "parse"
JOB_STAGE_SPLIT = "split"
JOB_STAGE_EMBED = "embed"
JOB_STAGE_INDEX = "index"

PROCESSING_ALERT_PENDING = "pending"
PROCESSING_ALERT_SENT = "sent"

_DOWNLOAD_STAGE_SEMAPHORE: Optional[BoundedSemaphore] = None
_INDEX_STAGE_SEMAPHORE: Optional[BoundedSemaphore] = None


@dataclass
class UploadPostprocessResult:
    """上传完成后的后处理结果。"""

    document_id: Optional[int]
    processing_job_id: int
    processing_status: str
    processing_error_message: str


def is_celery_processing_backend(settings: Settings) -> bool:
    """判断当前上传后处理是否使用 Celery 后端。"""

    return settings.upload_processing_backend.strip().lower() == UPLOAD_PROCESSING_BACKEND_CELERY


def clear_processing_job_claim(job: UploadProcessingJob) -> None:
    """清空 worker claim 信息。

    job 进入 completed / failed / retry_scheduled 后不应继续占着租约。
    """

    job.claim_token = ""
    job.locked_by = ""
    job.claimed_at = None
    job.lease_expires_at = None


def configure_postprocess_stage_limits(settings: Settings) -> None:
    """初始化后处理阶段的并发限制。"""

    global _DOWNLOAD_STAGE_SEMAPHORE, _INDEX_STAGE_SEMAPHORE
    if _DOWNLOAD_STAGE_SEMAPHORE is None:
        _DOWNLOAD_STAGE_SEMAPHORE = BoundedSemaphore(
            max(1, settings.upload_download_stage_concurrency)
        )
    if _INDEX_STAGE_SEMAPHORE is None:
        _INDEX_STAGE_SEMAPHORE = BoundedSemaphore(
            max(1, settings.upload_index_stage_concurrency)
        )


def get_download_stage_semaphore(settings: Settings) -> BoundedSemaphore:
    configure_postprocess_stage_limits(settings)
    return _DOWNLOAD_STAGE_SEMAPHORE  # type: ignore[return-value]


def get_index_stage_semaphore(settings: Settings) -> BoundedSemaphore:
    configure_postprocess_stage_limits(settings)
    return _INDEX_STAGE_SEMAPHORE  # type: ignore[return-value]


def build_local_upload_file_path(upload_task: UploadTask) -> Path:
    """为对象存储回落到本地文件生成稳定路径。"""

    original_name = Path(upload_task.original_filename).name or "upload.bin"
    safe_name = f"{upload_task.upload_id}_{original_name}"
    UPLOAD_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOAD_DOWNLOAD_DIR / safe_name


def detect_file_type_from_magic(
    *,
    head_bytes: bytes,
    upload_task: UploadTask,
    local_file_path: Path,
    settings: Settings,
) -> str:
    """基于 magic number 和容器结构校验文件类型。"""

    suffix = upload_task.file_type.lower()

    if suffix == "pdf":
        if head_bytes.startswith(b"%PDF-"):
            return "application/pdf"
        raise ValueError("Magic number validation failed for PDF")

    if suffix in {"docx", "xlsx"}:
        if not head_bytes.startswith(b"PK\x03\x04"):
            raise ValueError("Magic number validation failed for Office zip file")
        validate_office_zip_safety(local_file_path, suffix, settings)
        return (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if suffix == "docx"
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    if suffix in {"txt", "md", "csv"}:
        if b"\x00" in head_bytes:
            raise ValueError("Plain text upload contains NUL bytes")
        return "text/plain"

    raise ValueError(f"Unsupported file_type for magic detection: {suffix}")


def validate_office_zip_safety(
    local_file_path: Path,
    file_type: str,
    settings: Settings,
) -> None:
    """对 docx / xlsx 做基础恶意文件控制。"""

    with zipfile.ZipFile(local_file_path, "r") as zip_file:
        members = zip_file.infolist()
        if len(members) > settings.upload_zip_max_members:
            raise ValueError("Office zip file has too many members")

        total_uncompressed = sum(info.file_size for info in members)
        total_compressed = sum(max(1, info.compress_size) for info in members)
        if total_uncompressed > settings.upload_zip_max_uncompressed_bytes:
            raise ValueError("Office zip file exceeds max uncompressed size")

        ratio = total_uncompressed / total_compressed
        if ratio > settings.upload_zip_max_compression_ratio:
            raise ValueError("Office zip file compression ratio is too high")

        for info in members:
            normalized_name = info.filename.replace("\\", "/")
            if normalized_name.startswith("/") or ".." in normalized_name.split("/"):
                raise ValueError("Office zip file contains unsafe path entries")

        names = {info.filename for info in members}
        if file_type == "docx" and "word/document.xml" not in names:
            raise ValueError("DOCX container is missing word/document.xml")
        if file_type == "xlsx" and "xl/workbook.xml" not in names:
            raise ValueError("XLSX container is missing xl/workbook.xml")


def create_processing_job(
    upload_task: UploadTask,
    *,
    session: Session,
    settings: Settings,
    stage: str = JOB_STAGE_DOWNLOAD,
    depends_on_job_id: Optional[int] = None,
) -> UploadProcessingJob:
    """创建一个阶段级上传后处理 job。

    Phase A 只创建第一阶段 download job；后续阶段会由 Celery task 完成后继续创建。
    """

    max_attempts = settings.upload_job_max_retries + 1
    job = UploadProcessingJob(
        upload_task_id=upload_task.id,
        document_id=upload_task.document_id,
        job_type=JOB_TYPE_UPLOAD_PIPELINE,
        stage=stage,
        depends_on_job_id=depends_on_job_id,
        status=JOB_STATUS_PENDING,
        current_step="created",
        max_retry_count=settings.upload_job_max_retries,
        attempt_count=0,
        max_attempts=max_attempts,
        next_run_at=datetime.utcnow(),
        alert_status=PROCESSING_ALERT_PENDING,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    log_upload_event(
        session=session,
        upload_task_id=upload_task.id,
        processing_job_id=job.id,
        actor=upload_task.created_by,
        event_type="processing_job_created",
        detail={
            "job_type": job.job_type,
            "stage": job.stage,
            "depends_on_job_id": job.depends_on_job_id,
            "max_attempts": job.max_attempts,
        },
    )
    session.commit()
    return job


def enqueue_processing_job(
    upload_task: UploadTask,
    *,
    session: Session,
    settings: Settings,
) -> UploadPostprocessResult:
    """为 upload task 入队一个异步处理 job。"""

    if not upload_task.auto_create_document:
        upload_task.processing_status = "skipped"
        upload_task.processing_error_message = ""
        upload_task.updated_at = datetime.utcnow()
        session.add(upload_task)
        session.commit()
        session.refresh(upload_task)
        return UploadPostprocessResult(
            document_id=None,
            processing_job_id=0,
            processing_status="skipped",
            processing_error_message="",
        )

    existing_statement = (
        select(UploadProcessingJob)
        .where(UploadProcessingJob.upload_task_id == upload_task.id)
        .order_by(UploadProcessingJob.id.desc())
    )
    existing_job = session.exec(existing_statement).first()
    if existing_job is not None and existing_job.status in {
        JOB_STATUS_PENDING,
        JOB_STATUS_QUEUED,
        JOB_STATUS_RUNNING,
        JOB_STATUS_RETRY_SCHEDULED,
    }:
        upload_task.processing_status = existing_job.status
        upload_task.processing_error_message = existing_job.error_message
        upload_task.updated_at = datetime.utcnow()
        session.add(upload_task)
        session.commit()
        session.refresh(upload_task)
        return UploadPostprocessResult(
            document_id=upload_task.document_id,
            processing_job_id=existing_job.id,
            processing_status=existing_job.status,
            processing_error_message=existing_job.error_message,
        )

    job = create_processing_job(
        upload_task,
        session=session,
        settings=settings,
        stage=JOB_STAGE_DOWNLOAD,
    )
    if is_celery_processing_backend(settings):
        from app.services.upload_celery_service import dispatch_upload_download_stage_job

        dispatch_result = dispatch_upload_download_stage_job(
            processing_job_id=job.id,
            session=session,
            settings=settings,
        )
        session.refresh(upload_task)
        return UploadPostprocessResult(
            document_id=upload_task.document_id,
            processing_job_id=job.id,
            processing_status=dispatch_result.status,
            processing_error_message=upload_task.processing_error_message,
        )

    upload_task.processing_status = JOB_STATUS_PENDING
    upload_task.processing_error_message = ""
    upload_task.updated_at = datetime.utcnow()
    session.add(upload_task)
    session.commit()
    session.refresh(upload_task)
    return UploadPostprocessResult(
        document_id=upload_task.document_id,
        processing_job_id=job.id,
        processing_status=JOB_STATUS_PENDING,
        processing_error_message="",
    )


def get_or_create_document_for_upload_task(
    upload_task: UploadTask,
    *,
    local_file_path: Path,
    extracted_text: str,
    session: Session,
) -> Document:
    """为上传任务创建或复用 document。"""

    if upload_task.document_id is not None:
        document = session.get(Document, upload_task.document_id)
        if document is not None:
            document.filename = upload_task.original_filename
            document.file_path = str(local_file_path)
            document.file_type = upload_task.file_type
            document.extracted_text = extracted_text
            document.status = "uploaded"
            session.add(document)
            session.commit()
            session.refresh(document)
            return document

    document = Document(
        organization_id=upload_task.organization_id,
        created_by_user_id=upload_task.created_by_user_id,
        knowledge_base_id=upload_task.knowledge_base_id,
        filename=upload_task.original_filename,
        file_path=str(local_file_path),
        file_type=upload_task.file_type,
        status="uploaded",
        extracted_text=extracted_text,
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    upload_task.document_id = document.id
    session.add(upload_task)
    session.commit()
    session.refresh(upload_task)
    return document


def schedule_job_retry(
    upload_task: UploadTask,
    job: UploadProcessingJob,
    *,
    error_message: str,
    session: Session,
    settings: Settings,
    document: Optional[Document] = None,
) -> UploadPostprocessResult:
    """处理 job 失败后的重试退避。"""

    now = datetime.utcnow()
    job.retry_count += 1
    job.error_message = error_message
    job.updated_at = now

    if job.retry_count <= job.max_retry_count:
        backoff = min(
            settings.upload_job_retry_backoff_seconds * (2 ** (job.retry_count - 1)),
            settings.upload_job_retry_backoff_max_seconds,
        )
        job.status = JOB_STATUS_RETRY_SCHEDULED
        job.current_step = "retry_scheduled"
        job.next_run_at = now + timedelta(seconds=backoff)
    else:
        job.status = JOB_STATUS_FAILED
        job.current_step = "failed"
        if not job.last_alert_at:
            job.last_alert_at = now
            job.alert_status = PROCESSING_ALERT_SENT
    clear_processing_job_claim(job)

    session.add(job)

    upload_task.processing_status = job.status
    upload_task.processing_error_message = error_message
    upload_task.updated_at = now
    session.add(upload_task)

    if document is not None:
        document.status = "failed"
        session.add(document)

    log_upload_event(
        session=session,
        upload_task_id=upload_task.id,
        processing_job_id=job.id,
        actor=upload_task.created_by,
        event_type="processing_job_failed",
        level="error",
        detail={
            "retry_count": job.retry_count,
            "status": job.status,
            "error_message": error_message,
        },
    )
    session.commit()
    session.refresh(job)
    session.refresh(upload_task)
    if document is not None:
        session.refresh(document)

    return UploadPostprocessResult(
        document_id=document.id if document is not None else upload_task.document_id,
        processing_job_id=job.id,
        processing_status=job.status,
        processing_error_message=error_message,
    )


def run_download_stage_job(
    *,
    job_id: int,
    settings: Settings,
    celery_task_id: str = "",
    continue_pipeline: bool = True,
) -> UploadPostprocessResult:
    """执行阶段级 download job。

    这个阶段只把 OSS 原件下载到本地处理目录，并完成文件头、容器结构和可选 SHA256 校验。
    后续 parse / split / embed / index 会由各自的阶段 job 接续，不在这里提前执行。
    """

    configure_postprocess_stage_limits(settings)
    storage = get_object_storage_adapter()

    with Session(engine) as session:
        job = session.get(UploadProcessingJob, job_id)
        if job is None:
            raise ValueError(f"UploadProcessingJob not found: {job_id}")
        if job.stage != JOB_STAGE_DOWNLOAD:
            raise ValueError(f"UploadProcessingJob is not a download job: {job_id}")

        upload_task = session.get(UploadTask, job.upload_task_id)
        if upload_task is None:
            raise ValueError(f"UploadTask not found for job: {job_id}")

        if job.status in {JOB_STATUS_COMPLETED, JOB_STATUS_FAILED}:
            return UploadPostprocessResult(
                document_id=upload_task.document_id,
                processing_job_id=job.id,
                processing_status=job.status,
                processing_error_message=job.error_message,
            )

        now = datetime.utcnow()
        job.status = JOB_STATUS_RUNNING
        job.current_step = "download_object"
        job.attempt_count += 1
        job.started_at = now
        job.lease_expires_at = now + timedelta(seconds=settings.upload_job_lease_seconds)
        if celery_task_id:
            job.celery_task_id = celery_task_id
        job.updated_at = now
        session.add(job)
        upload_task.processing_status = JOB_STATUS_RUNNING
        upload_task.processing_error_message = ""
        upload_task.updated_at = now
        session.add(upload_task)
        log_upload_event(
            session=session,
            upload_task_id=upload_task.id,
            processing_job_id=job.id,
            actor=upload_task.created_by,
            event_type="processing_job_download_started",
            detail={
                "stage": JOB_STAGE_DOWNLOAD,
                "celery_task_id": job.celery_task_id,
            },
        )
        session.commit()

        try:
            local_file_path = build_local_upload_file_path(upload_task)
            with get_download_stage_semaphore(settings):
                download_result = storage.download_object_to_path(
                    bucket_name=settings.oss_bucket,
                    object_key=upload_task.object_key,
                    destination_path=str(local_file_path),
                    head_bytes_limit=settings.upload_magic_sniff_bytes,
                )

            if download_result.byte_count <= 0:
                raise ValueError("Downloaded object is empty")

            detected_mime_type = detect_file_type_from_magic(
                head_bytes=download_result.head_bytes,
                upload_task=upload_task,
                local_file_path=local_file_path,
                settings=settings,
            )
            upload_task.detected_mime_type = detected_mime_type

            if upload_task.file_sha256.strip():
                if download_result.sha256_hex != upload_task.file_sha256.strip():
                    raise ValueError(
                        "Uploaded object SHA256 does not match upload task file_sha256"
                    )

            now = datetime.utcnow()
            job.status = JOB_STATUS_COMPLETED
            job.current_step = "download_completed"
            job.error_message = ""
            job.completed_at = now
            job.updated_at = now
            clear_processing_job_claim(job)
            session.add(job)

            upload_task.status = "completed"
            upload_task.processing_status = JOB_STATUS_COMPLETED
            upload_task.processing_error_message = ""
            upload_task.updated_at = now
            session.add(upload_task)
            log_upload_event(
                session=session,
                upload_task_id=upload_task.id,
                processing_job_id=job.id,
                actor=upload_task.created_by,
                event_type="processing_job_download_completed",
                detail={
                    "stage": JOB_STAGE_DOWNLOAD,
                    "byte_count": download_result.byte_count,
                    "detected_mime_type": detected_mime_type,
                    "local_file_path": str(local_file_path),
                },
            )
            session.commit()
            session.refresh(job)
            session.refresh(upload_task)

            if continue_pipeline:
                advance_pipeline_after_stage(
                    upload_task=upload_task,
                    completed_job=job,
                    session=session,
                    settings=settings,
                )

            return UploadPostprocessResult(
                document_id=upload_task.document_id,
                processing_job_id=job.id,
                processing_status=JOB_STATUS_COMPLETED,
                processing_error_message="",
            )
        except Exception as exc:
            return schedule_job_retry(
                upload_task,
                job,
                error_message=str(exc),
                session=session,
                settings=settings,
            )


NEXT_STAGE_BY_STAGE = {
    JOB_STAGE_DOWNLOAD: JOB_STAGE_VALIDATE,
    JOB_STAGE_VALIDATE: JOB_STAGE_PARSE,
    JOB_STAGE_PARSE: JOB_STAGE_SPLIT,
    JOB_STAGE_SPLIT: JOB_STAGE_EMBED,
    JOB_STAGE_EMBED: JOB_STAGE_INDEX,
}


def advance_pipeline_after_stage(
    *,
    upload_task: UploadTask,
    completed_job: UploadProcessingJob,
    session: Session,
    settings: Settings,
) -> Optional[UploadProcessingJob]:
    """当前阶段完成后创建并投递下一个阶段 job。"""

    next_stage = NEXT_STAGE_BY_STAGE.get(completed_job.stage)
    if next_stage is None:
        return None

    # auto_index_on_complete=false 时只完成文档解析，不继续进入切片和索引。
    if next_stage in {JOB_STAGE_SPLIT, JOB_STAGE_EMBED, JOB_STAGE_INDEX} and not upload_task.auto_index_on_complete:
        upload_task.processing_status = JOB_STATUS_COMPLETED
        upload_task.updated_at = datetime.utcnow()
        session.add(upload_task)
        session.commit()
        return None

    next_job = create_processing_job(
        upload_task,
        session=session,
        settings=settings,
        stage=next_stage,
        depends_on_job_id=completed_job.id,
    )

    if is_celery_processing_backend(settings):
        from app.services.upload_celery_service import dispatch_upload_stage_job

        dispatch_upload_stage_job(
            processing_job_id=next_job.id,
            session=session,
            settings=settings,
        )

    upload_task.processing_status = next_job.status
    upload_task.processing_error_message = ""
    upload_task.updated_at = datetime.utcnow()
    session.add(upload_task)
    session.commit()
    session.refresh(next_job)
    return next_job


def calculate_file_sha256(file_path: Path) -> str:
    """按流计算本地文件 SHA256，避免一次性读入内存。"""

    hasher = sha256()
    with file_path.open("rb") as input_file:
        while True:
            data = input_file.read(1024 * 1024)
            if not data:
                break
            hasher.update(data)
    return hasher.hexdigest()


def run_pipeline_stage_job(
    *,
    job_id: int,
    settings: Settings,
    celery_task_id: str = "",
) -> UploadPostprocessResult:
    """执行 validate / parse / split / embed / index 阶段。"""

    with Session(engine) as session:
        job = session.get(UploadProcessingJob, job_id)
        if job is None:
            raise ValueError(f"UploadProcessingJob not found: {job_id}")
        if job.stage not in {
            JOB_STAGE_VALIDATE,
            JOB_STAGE_PARSE,
            JOB_STAGE_SPLIT,
            JOB_STAGE_EMBED,
            JOB_STAGE_INDEX,
        }:
            raise ValueError(f"Unsupported pipeline stage: {job.stage}")

        upload_task = session.get(UploadTask, job.upload_task_id)
        if upload_task is None:
            raise ValueError(f"UploadTask not found for job: {job_id}")
        if job.status in {JOB_STATUS_COMPLETED, JOB_STATUS_FAILED}:
            return UploadPostprocessResult(
                document_id=upload_task.document_id,
                processing_job_id=job.id,
                processing_status=job.status,
                processing_error_message=job.error_message,
            )

        now = datetime.utcnow()
        job.status = JOB_STATUS_RUNNING
        job.current_step = f"{job.stage}_started"
        job.attempt_count += 1
        job.started_at = now
        job.lease_expires_at = now + timedelta(seconds=settings.upload_job_lease_seconds)
        if celery_task_id:
            job.celery_task_id = celery_task_id
        job.updated_at = now
        session.add(job)
        upload_task.processing_status = JOB_STATUS_RUNNING
        upload_task.processing_error_message = ""
        upload_task.updated_at = now
        session.add(upload_task)
        log_upload_event(
            session=session,
            upload_task_id=upload_task.id,
            processing_job_id=job.id,
            actor=upload_task.created_by,
            event_type="processing_job_stage_started",
            detail={"stage": job.stage, "celery_task_id": job.celery_task_id},
        )
        session.commit()

        try:
            local_file_path = build_local_upload_file_path(upload_task)
            document: Optional[Document] = None

            if job.stage == JOB_STAGE_VALIDATE:
                if not local_file_path.exists():
                    raise ValueError("Downloaded local file does not exist")
                with local_file_path.open("rb") as input_file:
                    head_bytes = input_file.read(settings.upload_magic_sniff_bytes)
                upload_task.detected_mime_type = detect_file_type_from_magic(
                    head_bytes=head_bytes,
                    upload_task=upload_task,
                    local_file_path=local_file_path,
                    settings=settings,
                )
                if upload_task.file_sha256.strip():
                    actual_sha256 = calculate_file_sha256(local_file_path)
                    if actual_sha256 != upload_task.file_sha256.strip():
                        raise ValueError(
                            "Uploaded object SHA256 does not match upload task file_sha256"
                        )
                session.add(upload_task)

            elif job.stage == JOB_STAGE_PARSE:
                extracted_text = extract_text_from_file(
                    local_file_path,
                    f".{upload_task.file_type}",
                )
                if not extracted_text.strip():
                    raise ValueError("Extracted text is empty")
                document = get_or_create_document_for_upload_task(
                    upload_task,
                    local_file_path=local_file_path,
                    extracted_text=extracted_text,
                    session=session,
                )
                job.document_id = document.id
                session.add(job)

            elif job.stage == JOB_STAGE_SPLIT:
                document = session.get(Document, upload_task.document_id)
                if document is None:
                    raise ValueError("Document is required before split stage")
                pdf_pages = extract_pdf_pages(local_file_path) if document.file_type == "pdf" else None
                regenerate_document_chunks(document, session, pdf_pages=pdf_pages)
                session.commit()

            elif job.stage == JOB_STAGE_EMBED:
                chunks = list(
                    session.exec(
                        select(Chunk)
                        .where(Chunk.document_id == upload_task.document_id)
                        .order_by(Chunk.chunk_index)
                    ).all()
                )
                if not chunks:
                    raise ValueError("No chunks found before embed stage")
                embeddings = embed_chunks(chunks)
                for chunk, embedding in zip(chunks, embeddings):
                    chunk.embedding_json = json.dumps(embedding)
                    session.add(chunk)

            elif job.stage == JOB_STAGE_INDEX:
                document = session.get(Document, upload_task.document_id)
                if document is None:
                    raise ValueError("Document is required before index stage")
                chunks = list(
                    session.exec(
                        select(Chunk)
                        .where(Chunk.document_id == document.id)
                        .order_by(Chunk.chunk_index)
                    ).all()
                )
                embeddings: list[list[float]] = []
                for chunk in chunks:
                    if not chunk.embedding_json.strip():
                        raise ValueError(f"Missing embedding for chunk {chunk.id}")
                    embeddings.append(json.loads(chunk.embedding_json))

                existing_vector_ids = get_existing_document_vector_ids(document.id, session)
                if existing_vector_ids:
                    delete_vectors(document.knowledge_base_id, existing_vector_ids)
                index_result = index_chunks(chunks, embeddings)
                for chunk, vector_id in zip(chunks, index_result.vector_ids):
                    chunk.vector_id = vector_id
                    chunk.embedding_json = ""
                    session.add(chunk)
                document.status = "indexed"
                session.add(document)

            now = datetime.utcnow()
            job.status = JOB_STATUS_COMPLETED
            job.current_step = f"{job.stage}_completed"
            job.error_message = ""
            job.completed_at = now
            job.updated_at = now
            clear_processing_job_claim(job)
            session.add(job)
            upload_task.processing_status = JOB_STATUS_COMPLETED
            upload_task.processing_error_message = ""
            upload_task.updated_at = now
            session.add(upload_task)
            log_upload_event(
                session=session,
                upload_task_id=upload_task.id,
                processing_job_id=job.id,
                actor=upload_task.created_by,
                event_type="processing_job_stage_completed",
                detail={"stage": job.stage, "document_id": upload_task.document_id},
            )
            session.commit()
            session.refresh(job)
            session.refresh(upload_task)

            advance_pipeline_after_stage(
                upload_task=upload_task,
                completed_job=job,
                session=session,
                settings=settings,
            )

            return UploadPostprocessResult(
                document_id=upload_task.document_id,
                processing_job_id=job.id,
                processing_status=JOB_STATUS_COMPLETED,
                processing_error_message="",
            )
        except Exception as exc:
            return schedule_job_retry(
                upload_task,
                job,
                error_message=str(exc),
                session=session,
                settings=settings,
            )


def run_processing_job(
    *,
    job_id: int,
    settings: Settings,
    claim_token: str = "",
) -> UploadPostprocessResult:
    """执行单个上传后处理 job。"""

    configure_postprocess_stage_limits(settings)
    storage = get_object_storage_adapter()

    with Session(engine) as session:
        job = session.get(UploadProcessingJob, job_id)
        if job is None:
            raise ValueError(f"UploadProcessingJob not found: {job_id}")

        upload_task = session.get(UploadTask, job.upload_task_id)
        if upload_task is None:
            raise ValueError(f"UploadTask not found for job: {job_id}")

        if job.status in {JOB_STATUS_COMPLETED, JOB_STATUS_FAILED}:
            return UploadPostprocessResult(
                document_id=upload_task.document_id,
                processing_job_id=job.id,
                processing_status=job.status,
                processing_error_message=job.error_message,
            )

        if claim_token and job.claim_token != claim_token:
            raise ValueError(f"UploadProcessingJob claim token mismatch: {job_id}")

        now = datetime.utcnow()
        job.status = JOB_STATUS_RUNNING
        job.current_step = "download_object"
        job.attempt_count += 1
        job.started_at = now
        job.lease_expires_at = now + timedelta(seconds=settings.upload_job_lease_seconds)
        job.updated_at = now
        session.add(job)
        upload_task.processing_status = JOB_STATUS_RUNNING
        upload_task.processing_error_message = ""
        upload_task.updated_at = now
        session.add(upload_task)
        log_upload_event(
            session=session,
            upload_task_id=upload_task.id,
            processing_job_id=job.id,
            actor=upload_task.created_by,
            event_type="processing_job_started",
            detail={"job_type": job.job_type},
        )
        session.commit()

        document: Optional[Document] = None
        try:
            local_file_path = build_local_upload_file_path(upload_task)
            with get_download_stage_semaphore(settings):
                download_result = storage.download_object_to_path(
                    bucket_name=settings.oss_bucket,
                    object_key=upload_task.object_key,
                    destination_path=str(local_file_path),
                    head_bytes_limit=settings.upload_magic_sniff_bytes,
                )

                if download_result.byte_count <= 0:
                    raise ValueError("Downloaded object is empty")

                detected_mime_type = detect_file_type_from_magic(
                    head_bytes=download_result.head_bytes,
                    upload_task=upload_task,
                    local_file_path=local_file_path,
                    settings=settings,
                )
                upload_task.detected_mime_type = detected_mime_type

                if upload_task.file_sha256.strip():
                    if download_result.sha256_hex != upload_task.file_sha256.strip():
                        raise ValueError(
                            "Uploaded object SHA256 does not match upload task file_sha256"
                        )

                now = datetime.utcnow()
                job.current_step = "extract_text"
                job.updated_at = now
                session.add(job)
                session.add(upload_task)
                session.commit()

                extracted_text = extract_text_from_file(
                    local_file_path,
                    f".{upload_task.file_type}",
                )
                if not extracted_text.strip():
                    raise ValueError("Extracted text is empty")

                now = datetime.utcnow()
                job.current_step = "create_document"
                job.updated_at = now
                session.add(job)
                session.commit()

                document = get_or_create_document_for_upload_task(
                    upload_task,
                    local_file_path=local_file_path,
                    extracted_text=extracted_text,
                    session=session,
                )
                job.document_id = document.id
                session.add(job)
                session.commit()

            if upload_task.auto_index_on_complete:
                with get_index_stage_semaphore(settings):
                    now = datetime.utcnow()
                    job.current_step = "index_document"
                    job.updated_at = now
                    session.add(job)
                    session.commit()

                    pdf_pages = None
                    if document.file_type == "pdf":
                        pdf_pages = extract_pdf_pages(local_file_path)

                    existing_vector_ids = get_existing_document_vector_ids(document.id, session)
                    if existing_vector_ids:
                        delete_vectors(document.knowledge_base_id, existing_vector_ids)

                    _, created_chunks = regenerate_document_chunks(
                        document,
                        session,
                        pdf_pages=pdf_pages,
                    )
                    index_result = add_chunks(created_chunks)
                    for chunk, vector_id in zip(created_chunks, index_result.vector_ids):
                        chunk.vector_id = vector_id
                        session.add(chunk)

                    document.status = "indexed"
                    session.add(document)
                    session.commit()
                    session.refresh(document)

            now = datetime.utcnow()
            job.status = JOB_STATUS_COMPLETED
            job.current_step = "done"
            job.error_message = ""
            job.completed_at = now
            job.updated_at = now
            clear_processing_job_claim(job)
            session.add(job)

            upload_task.processing_status = JOB_STATUS_COMPLETED
            upload_task.processing_error_message = ""
            upload_task.updated_at = now
            session.add(upload_task)
            log_upload_event(
                session=session,
                upload_task_id=upload_task.id,
                processing_job_id=job.id,
                actor=upload_task.created_by,
                event_type="processing_job_completed",
                detail={"document_id": document.id if document is not None else None},
            )
            session.commit()
            session.refresh(job)
            session.refresh(upload_task)

            return UploadPostprocessResult(
                document_id=document.id if document is not None else upload_task.document_id,
                processing_job_id=job.id,
                processing_status=JOB_STATUS_COMPLETED,
                processing_error_message="",
            )
        except Exception as exc:
            return schedule_job_retry(
                upload_task,
                job,
                error_message=str(exc),
                session=session,
                settings=settings,
                document=document,
            )
