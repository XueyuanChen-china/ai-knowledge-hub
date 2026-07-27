from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from uuid import uuid4

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.config import Settings
from app.db.models import (
    KnowledgeBase,
    UploadPart,
    UploadTask,
)
from app.schemas.upload import (
    UploadBatchPresignItem,
    UploadCompleteRequest,
    UploadInitRequest,
    UploadPartCompleteRequest,
    UploadPartRecord,
)
from app.services.storage.base import ObjectStorageAdapter, StorageAdapterError
from app.services.upload_postprocess_service import (
    UploadPostprocessResult,
    enqueue_processing_job,
)
from app.services.upload_audit_service import log_upload_event

ALLOWED_UPLOAD_EXTENSIONS = {".txt", ".md", ".pdf", ".docx", ".xlsx", ".csv"}

UPLOAD_STATUS_INITIATED = "initiated"
UPLOAD_STATUS_UPLOADING = "uploading"
UPLOAD_STATUS_UPLOADED = "uploaded"
UPLOAD_STATUS_VERIFYING = "verifying"
UPLOAD_STATUS_COMPLETED = "completed"
UPLOAD_STATUS_FAILED = "failed"
UPLOAD_STATUS_CANCELLED = "cancelled"
UPLOAD_STATUS_EXPIRED = "expired"

ACTIVE_UPLOAD_STATUSES = {
    UPLOAD_STATUS_INITIATED,
    UPLOAD_STATUS_UPLOADING,
    UPLOAD_STATUS_UPLOADED,
}


def ensure_knowledge_base_exists(knowledge_base_id: int, session: Session) -> None:
    """确认知识库存在。"""

    knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )


def build_upload_id() -> str:
    """生成对外暴露的上传任务 ID。"""

    return f"upl_{uuid4().hex[:16]}"


def extract_safe_extension(filename: str) -> str:
    """提取并校验后缀名。"""

    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_UPLOAD_EXTENSIONS))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only {allowed} files are supported",
        )
    return suffix


def validate_upload_init_request(payload: UploadInitRequest, settings: Settings) -> str:
    """校验初始化上传请求，并返回安全后缀名。"""

    if payload.file_size <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="file_size must be greater than 0",
        )

    if payload.file_size > settings.upload_max_file_size:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="file_size exceeds UPLOAD_MAX_FILE_SIZE",
        )

    if not payload.filename.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="filename must not be empty",
        )

    original_name = Path(payload.filename).name
    if original_name != payload.filename.strip() or ".." in payload.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="filename contains invalid path segments",
        )

    return extract_safe_extension(payload.filename.strip())


def build_object_key(
    storage_prefix: str,
    organization_id: int,
    knowledge_base_id: int,
    upload_id: str,
    extension: str,
) -> str:
    """按统一规则生成 object_key。"""

    normalized_prefix = storage_prefix.strip().strip("/")
    clean_extension = extension.lstrip(".").lower()
    return (
        f"{normalized_prefix}/{organization_id}/{knowledge_base_id}/{upload_id}/"
        f"source.{clean_extension}"
    )


def calculate_total_parts(file_size: int, part_size: int) -> int:
    """根据文件大小和分片大小计算总片数。"""

    return (file_size + part_size - 1) // part_size


def get_upload_task_or_404(upload_id: str, session: Session) -> UploadTask:
    """按 upload_id 查询上传任务。"""

    statement = select(UploadTask).where(UploadTask.upload_id == upload_id)
    upload_task = session.exec(statement).first()
    if upload_task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Upload task not found",
        )
    return upload_task


def mark_upload_task_expired(upload_task: UploadTask, session: Session) -> None:
    """把上传任务标记为 expired。"""

    upload_task.status = UPLOAD_STATUS_EXPIRED
    upload_task.updated_at = datetime.utcnow()
    session.add(upload_task)
    session.commit()
    session.refresh(upload_task)


def ensure_upload_not_expired(upload_task: UploadTask, session: Session) -> None:
    """确保上传任务没有过期。"""

    if upload_task.status == UPLOAD_STATUS_EXPIRED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Upload task is expired",
        )

    if upload_task.expires_at <= datetime.utcnow():
        mark_upload_task_expired(upload_task, session)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Upload task is expired",
        )


def list_upload_parts_for_task(upload_task_id: int, session: Session) -> List[UploadPart]:
    """查询本地已经登记的 part 列表。"""

    statement = (
        select(UploadPart)
        .where(UploadPart.upload_task_id == upload_task_id)
        .order_by(UploadPart.part_number.asc())
    )
    return list(session.exec(statement).all())


def validate_part_number(part_number: int, total_parts: int) -> None:
    """校验 part_number 范围。"""

    if part_number <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="part_number must be greater than 0",
        )


def expected_part_size(upload_task: UploadTask, part_number: int) -> int:
    """返回某一分片的精确字节数，最后一片允许小于标准 part_size。"""

    validate_part_number(part_number, upload_task.total_parts)
    if part_number < upload_task.total_parts:
        return upload_task.part_size
    return upload_task.file_size - upload_task.part_size * (upload_task.total_parts - 1)

    if part_number > total_parts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="part_number exceeds total_parts",
        )


def ensure_upload_is_active(upload_task: UploadTask, session: Session) -> None:
    """确保上传任务处于可继续上传的状态。"""

    ensure_upload_not_expired(upload_task, session)
    if upload_task.status not in ACTIVE_UPLOAD_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Upload task status {upload_task.status} does not allow this operation",
        )


def count_uploaded_parts(parts: List[UploadPart]) -> int:
    """统计本地已上传完成的 part 数量。"""

    return sum(1 for part in parts if part.status == "uploaded")


def map_remote_parts_by_number(
    remote_parts: List[Dict[str, object]],
) -> Dict[int, Dict[str, object]]:
    """把 OSS 侧返回的 part 列表转成按 part_number 索引。"""

    return {
        int(part["part_number"]): part
        for part in remote_parts
    }


def sync_upload_progress(upload_task: UploadTask, session: Session) -> None:
    """根据本地 part 状态刷新 upload_task.completed_parts。"""

    local_parts = list_upload_parts_for_task(upload_task.id, session)
    upload_task.completed_parts = count_uploaded_parts(local_parts)
    if upload_task.completed_parts > 0 and upload_task.status == UPLOAD_STATUS_INITIATED:
        upload_task.status = UPLOAD_STATUS_UPLOADING
    upload_task.updated_at = datetime.utcnow()
    session.add(upload_task)


def build_part_record(
    part_number: int,
    etag: str,
    part_size: int,
    part_sha256: str,
    status_text: str,
    source: str,
    retry_count: int = 0,
    last_error_message: str = "",
    updated_at: Optional[datetime] = None,
) -> UploadPartRecord:
    """构建响应层的 part 记录。"""

    return UploadPartRecord(
        part_number=part_number,
        etag=etag,
        part_size=part_size,
        part_sha256=part_sha256,
        status=status_text,
        source=source,
        retry_count=retry_count,
        last_error_message=last_error_message,
        updated_at=updated_at,
    )


def build_missing_part_numbers(
    total_parts: int,
    completed_part_numbers: Set[int],
) -> List[int]:
    """返回还未完成的 part_number 列表。"""

    return [
        index
        for index in range(1, total_parts + 1)
        if index not in completed_part_numbers
    ]


def enforce_upload_actor_limits(
    payload: UploadInitRequest,
    session: Session,
    settings: Settings,
) -> None:
    """做基础限流和配额控制。"""

    actor = payload.created_by.strip()
    if not actor:
        return

    active_statement = select(UploadTask).where(
        UploadTask.created_by == actor,
        UploadTask.status.in_(
            [
                UPLOAD_STATUS_INITIATED,
                UPLOAD_STATUS_UPLOADING,
                UPLOAD_STATUS_UPLOADED,
            ]
        ),
    )
    active_tasks = list(session.exec(active_statement).all())
    if len(active_tasks) >= settings.upload_max_active_tasks_per_actor:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Actor active upload task count exceeds UPLOAD_MAX_ACTIVE_TASKS_PER_ACTOR",
        )

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    quota_statement = select(UploadTask).where(
        UploadTask.created_by == actor,
        UploadTask.created_at >= today_start,
    )
    today_tasks = list(session.exec(quota_statement).all())
    used_bytes = sum(task.file_size for task in today_tasks)
    if used_bytes + payload.file_size > settings.upload_daily_quota_bytes:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Actor daily upload quota exceeds UPLOAD_DAILY_QUOTA_BYTES",
        )


def get_or_create_part_record(
    upload_task: UploadTask,
    *,
    part_number: int,
    session: Session,
) -> UploadPart:
    """按 upload_task + part_number 查找或创建本地 part 记录。"""

    statement = select(UploadPart).where(
        UploadPart.upload_task_id == upload_task.id,
        UploadPart.part_number == part_number,
    )
    upload_part = session.exec(statement).first()
    if upload_part is not None:
        return upload_part

    upload_part = UploadPart(
        upload_task_id=upload_task.id,
        part_number=part_number,
        status="pending",
        retry_count=0,
        last_error_message="",
    )
    session.add(upload_part)
    session.flush()
    return upload_part


def bump_part_retry_count(
    upload_part: UploadPart,
    *,
    max_retries: int,
) -> None:
    """增加重试次数并做上限校验。"""

    if upload_part.status == "uploaded":
        return

    if upload_part.retry_count >= max_retries:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="part retry_count exceeds UPLOAD_MAX_PART_RETRIES",
        )

    upload_part.retry_count += 1
    upload_part.updated_at = datetime.utcnow()


def upsert_upload_part(
    upload_task: UploadTask,
    *,
    part_number: int,
    etag: str,
    part_size: int,
    part_sha256: str,
    status_text: str,
    session: Session,
) -> UploadPart:
    """插入或更新单个 part 记录。"""

    upload_part = get_or_create_part_record(
        upload_task,
        part_number=part_number,
        session=session,
    )
    upload_part.etag = etag
    upload_part.part_size = part_size
    upload_part.part_sha256 = part_sha256
    upload_part.status = status_text
    upload_part.last_error_message = ""
    upload_part.updated_at = datetime.utcnow()
    session.add(upload_part)
    return upload_part


def init_upload_task(
    payload: UploadInitRequest,
    *,
    organization_id: int,
    created_by_user_id: int,
    session: Session,
    settings: Settings,
    storage: ObjectStorageAdapter,
) -> UploadTask:
    """初始化上传任务并在对象存储侧创建 multipart upload。"""

    knowledge_base = session.exec(
        select(KnowledgeBase).where(
            KnowledgeBase.id == payload.knowledge_base_id,
            KnowledgeBase.organization_id == organization_id,
        )
    ).first()
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )
    extension = validate_upload_init_request(payload, settings)
    enforce_upload_actor_limits(
        payload.model_copy(update={"created_by": str(created_by_user_id)}),
        session,
        settings,
    )

    upload_id = build_upload_id()
    part_size = settings.upload_default_part_size
    total_parts = calculate_total_parts(payload.file_size, part_size)
    object_key = build_object_key(
        settings.oss_storage_prefix,
        organization_id,
        payload.knowledge_base_id,
        upload_id,
        extension,
    )
    auto_create_document = (
        settings.upload_auto_create_document
        if payload.auto_create_document is None
        else payload.auto_create_document
    )
    auto_index_on_complete = (
        settings.upload_auto_index_on_complete
        if payload.auto_index_on_complete is None
        else payload.auto_index_on_complete
    )
    if not auto_create_document:
        auto_index_on_complete = False

    storage_upload_id = ""
    try:
        init_result = storage.initiate_multipart_upload(
            bucket_name=settings.oss_bucket,
            object_key=object_key,
            content_type=payload.client_mime_type,
        )
        storage_upload_id = init_result.upload_id

        upload_task = UploadTask(
            organization_id=organization_id,
            created_by_user_id=created_by_user_id,
            upload_id=upload_id,
            knowledge_base_id=payload.knowledge_base_id,
            original_filename=payload.filename.strip(),
            storage_provider=settings.storage_provider,
            bucket_name=settings.oss_bucket,
            object_key=object_key,
            file_type=extension.lstrip("."),
            client_mime_type=payload.client_mime_type.strip(),
            file_size=payload.file_size,
            part_size=part_size,
            total_parts=total_parts,
            file_sha256=payload.file_sha256.strip(),
            storage_upload_id=storage_upload_id,
            status=UPLOAD_STATUS_INITIATED,
            created_by=str(created_by_user_id),
            expires_at=datetime.utcnow() + timedelta(hours=settings.upload_task_expire_hours),
            auto_create_document=auto_create_document,
            auto_index_on_complete=auto_index_on_complete,
            processing_status="pending" if auto_create_document else "skipped",
            processing_error_message="",
        )
        session.add(upload_task)
        session.commit()
        session.refresh(upload_task)
        log_upload_event(
            session=session,
            upload_task_id=upload_task.id,
            actor=upload_task.created_by,
            event_type="upload_task_initialized",
            detail={
                "knowledge_base_id": upload_task.knowledge_base_id,
                "file_size": upload_task.file_size,
                "total_parts": upload_task.total_parts,
            },
        )
        session.commit()
        return upload_task
    except StorageAdapterError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to initialize object storage multipart upload",
        ) from exc
    except Exception:
        session.rollback()
        if storage_upload_id:
            try:
                storage.abort_multipart_upload(
                    bucket_name=settings.oss_bucket,
                    object_key=object_key,
                    upload_id=storage_upload_id,
                )
            except StorageAdapterError:
                pass
        raise


def list_remote_parts_or_502(
    upload_task: UploadTask,
    *,
    settings: Settings,
    storage: ObjectStorageAdapter,
) -> List[Dict[str, object]]:
    """查询 OSS 侧已上传的 part 列表。"""

    try:
        return storage.list_uploaded_parts(
            bucket_name=settings.oss_bucket,
            object_key=upload_task.object_key,
            upload_id=upload_task.storage_upload_id,
        )
    except StorageAdapterError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to list object storage uploaded parts",
        ) from exc


def generate_part_presigned_url(
    upload_task: UploadTask,
    *,
    part_number: int,
    session: Session,
    settings: Settings,
    storage: ObjectStorageAdapter,
) -> str:
    """为指定 part 生成预签名上传地址。"""

    ensure_upload_is_active(upload_task, session)
    validate_part_number(part_number, upload_task.total_parts)

    upload_part = get_or_create_part_record(
        upload_task,
        part_number=part_number,
        session=session,
    )
    bump_part_retry_count(
        upload_part,
        max_retries=settings.upload_max_part_retries,
    )
    upload_part.status = "pending"
    session.add(upload_part)

    try:
        presigned_url = storage.generate_upload_part_presigned_url(
            bucket_name=settings.oss_bucket,
            object_key=upload_task.object_key,
            upload_id=upload_task.storage_upload_id,
            part_number=part_number,
            expire_seconds=settings.oss_presign_expire_seconds,
            content_type=upload_task.client_mime_type,
        )
    except StorageAdapterError as exc:
        upload_part.last_error_message = "Failed to generate presigned URL"
        session.add(upload_part)
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to generate upload part presigned URL",
        ) from exc

    upload_task.status = UPLOAD_STATUS_UPLOADING
    upload_task.updated_at = datetime.utcnow()
    session.add(upload_task)
    session.commit()
    session.refresh(upload_task)
    return presigned_url


def generate_batch_presigned_urls(
    upload_task: UploadTask,
    *,
    part_numbers: List[int],
    session: Session,
    settings: Settings,
    storage: ObjectStorageAdapter,
) -> List[UploadBatchPresignItem]:
    """批量生成多个 part 的 presigned URL。"""

    if not part_numbers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="part_numbers must not be empty",
        )
    if len(part_numbers) > settings.upload_presign_batch_max_parts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="part_numbers exceeds UPLOAD_PRESIGN_BATCH_MAX_PARTS",
        )

    items: List[UploadBatchPresignItem] = []
    for part_number in part_numbers:
        presigned_url = generate_part_presigned_url(
            upload_task,
            part_number=part_number,
            session=session,
            settings=settings,
            storage=storage,
        )
        items.append(
            UploadBatchPresignItem(
                part_number=part_number,
                presigned_url=presigned_url,
            )
        )
    return items


def normalize_etag(etag: object) -> str:
    """统一 OSS HTTP 响应和 list parts 返回的 ETag 格式。"""

    return str(etag or "").strip().strip('"')


def complete_upload_part(
    upload_task: UploadTask,
    payload: UploadPartCompleteRequest,
    *,
    session: Session,
    settings: Settings,
    storage: ObjectStorageAdapter,
) -> UploadPart:
    """回写某个 part 已上传完成，并校验 OSS 上确实存在该 part。"""

    ensure_upload_is_active(upload_task, session)
    validate_part_number(payload.part_number, upload_task.total_parts)

    remote_parts = list_remote_parts_or_502(
        upload_task,
        settings=settings,
        storage=storage,
    )
    remote_parts_map = map_remote_parts_by_number(remote_parts)
    remote_part = remote_parts_map.get(payload.part_number)
    if remote_part is None:
        upload_part = get_or_create_part_record(
            upload_task,
            part_number=payload.part_number,
            session=session,
        )
        upload_part.last_error_message = "Target part has not been uploaded to object storage yet"
        upload_part.updated_at = datetime.utcnow()
        session.add(upload_part)
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Target part has not been uploaded to object storage yet",
        )

    remote_etag = normalize_etag(remote_part.get("etag"))
    remote_part_size = int(remote_part.get("size") or 0)
    expected_size = expected_part_size(upload_task, payload.part_number)
    if remote_part_size != expected_size:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="part_size does not match the expected upload task boundary",
        )
    if normalize_etag(payload.etag) and normalize_etag(payload.etag) != remote_etag:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="etag does not match object storage part record",
        )

    if payload.part_size is not None and payload.part_size != remote_part_size:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="part_size does not match object storage part record",
        )

    upload_part = upsert_upload_part(
        upload_task,
        part_number=payload.part_number,
        etag=remote_etag,
        part_size=remote_part_size,
        part_sha256=payload.part_sha256.strip(),
        status_text="uploaded",
        session=session,
    )

    sync_upload_progress(upload_task, session)
    local_parts = list_upload_parts_for_task(upload_task.id, session)
    if count_uploaded_parts(local_parts) == upload_task.total_parts:
        upload_task.status = UPLOAD_STATUS_UPLOADED
        session.add(upload_task)

    session.commit()
    session.refresh(upload_task)
    session.refresh(upload_part)
    return upload_part


def get_upload_parts_view(
    upload_task: UploadTask,
    *,
    session: Session,
    settings: Settings,
    storage: ObjectStorageAdapter,
) -> Dict[str, object]:
    """获取断点续传所需的本地 part 状态和 OSS 侧 part 列表。"""

    ensure_upload_not_expired(upload_task, session)
    local_parts = list_upload_parts_for_task(upload_task.id, session)
    remote_parts = list_remote_parts_or_502(
        upload_task,
        settings=settings,
        storage=storage,
    )
    remote_parts_map = map_remote_parts_by_number(remote_parts)
    local_parts_map = {
        int(part.part_number): part
        for part in local_parts
    }

    local_records = [
        build_part_record(
            part_number=int(part.part_number),
            etag=str(part.etag),
            part_size=int(part.part_size),
            part_sha256=str(part.part_sha256),
            status_text=str(part.status),
            source="local",
            retry_count=int(part.retry_count),
            last_error_message=str(part.last_error_message),
            updated_at=part.updated_at,
        )
        for part in local_parts
    ]

    remote_records = [
        build_part_record(
            part_number=int(part["part_number"]),
            etag=str(part.get("etag") or ""),
            part_size=int(part.get("size") or 0),
            part_sha256="",
            status_text="uploaded",
            source="remote",
        )
        for part in sorted(
            remote_parts,
            key=lambda item: int(item["part_number"]),
        )
    ]

    completed_numbers = set()
    for part_number, remote_part in remote_parts_map.items():
        local_part = local_parts_map.get(part_number)
        if local_part is not None and local_part.status == "uploaded":
            completed_numbers.add(part_number)
            continue
        if remote_part.get("etag"):
            completed_numbers.add(part_number)

    return {
        "upload_id": upload_task.upload_id,
        "status": upload_task.status,
        "total_parts": upload_task.total_parts,
        "completed_parts": len(completed_numbers),
        "local_parts": local_records,
        "remote_parts": remote_records,
        "missing_part_numbers": build_missing_part_numbers(
            upload_task.total_parts,
            completed_numbers,
        ),
    }


def complete_upload_task(
    upload_task: UploadTask,
    payload: UploadCompleteRequest,
    *,
    session: Session,
    settings: Settings,
    storage: ObjectStorageAdapter,
) -> Tuple[str, str, int, UploadPostprocessResult]:
    """完成整个 multipart upload，并把后处理 job 入队。"""

    ensure_upload_not_expired(upload_task, session)
    if upload_task.status == UPLOAD_STATUS_COMPLETED:
        return (
            UPLOAD_STATUS_COMPLETED,
            "Upload task is already completed",
            upload_task.completed_parts,
            UploadPostprocessResult(
                document_id=upload_task.document_id,
                processing_job_id=0,
                processing_status=upload_task.processing_status,
                processing_error_message=upload_task.processing_error_message,
            ),
        )

    ensure_upload_is_active(upload_task, session)

    if payload.expected_total_parts is not None and payload.expected_total_parts != upload_task.total_parts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="expected_total_parts does not match upload task",
        )

    local_parts = list_upload_parts_for_task(upload_task.id, session)
    if not local_parts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No uploaded parts have been registered locally",
        )

    uploaded_local_parts = [
        part
        for part in local_parts
        if part.status == "uploaded"
    ]
    uploaded_part_numbers = {int(part.part_number) for part in uploaded_local_parts}
    if uploaded_part_numbers != set(range(1, upload_task.total_parts + 1)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Not all parts have been uploaded and confirmed",
        )

    remote_parts = list_remote_parts_or_502(
        upload_task,
        settings=settings,
        storage=storage,
    )
    remote_parts_map = map_remote_parts_by_number(remote_parts)
    if set(remote_parts_map.keys()) != uploaded_part_numbers:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Object storage part list does not match local uploaded parts",
        )

    complete_parts_payload: List[Dict[str, object]] = []
    for part in uploaded_local_parts:
        remote_part = remote_parts_map[int(part.part_number)]
        remote_etag = normalize_etag(remote_part.get("etag"))
        if remote_etag != normalize_etag(part.etag):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Object storage part etag does not match local record",
            )
        complete_parts_payload.append(
            {
                "part_number": int(part.part_number),
                "etag": remote_etag,
            }
        )

    try:
        storage.complete_multipart_upload(
            bucket_name=settings.oss_bucket,
            object_key=upload_task.object_key,
            upload_id=upload_task.storage_upload_id,
            parts=complete_parts_payload,
        )
    except StorageAdapterError as exc:
        upload_task.status = UPLOAD_STATUS_FAILED
        upload_task.error_message = "Failed to complete object storage multipart upload"
        upload_task.updated_at = datetime.utcnow()
        session.add(upload_task)
        session.commit()
        session.refresh(upload_task)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to complete object storage multipart upload",
        ) from exc

    upload_task.status = UPLOAD_STATUS_COMPLETED
    upload_task.completed_parts = len(uploaded_local_parts)
    upload_task.updated_at = datetime.utcnow()
    session.add(upload_task)
    session.commit()
    session.refresh(upload_task)

    log_upload_event(
        session=session,
        upload_task_id=upload_task.id,
        actor=upload_task.created_by,
        event_type="upload_task_completed",
        detail={"completed_parts": upload_task.completed_parts},
    )
    session.commit()

    postprocess_result = enqueue_processing_job(
        upload_task,
        session=session,
        settings=settings,
    )
    session.refresh(upload_task)

    return (
        upload_task.status,
        "Multipart upload completed successfully",
        upload_task.completed_parts,
        postprocess_result,
    )


def abort_upload_task(
    upload_task: UploadTask,
    *,
    session: Session,
    settings: Settings,
    storage: ObjectStorageAdapter,
) -> Tuple[str, str]:
    """取消上传任务，并通知 OSS 终止 multipart upload。"""

    ensure_upload_not_expired(upload_task, session)
    if upload_task.status == UPLOAD_STATUS_COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Completed upload task cannot be aborted",
        )

    if upload_task.status == UPLOAD_STATUS_CANCELLED:
        return (
            UPLOAD_STATUS_CANCELLED,
            "Upload task is already cancelled",
        )

    try:
        storage.abort_multipart_upload(
            bucket_name=settings.oss_bucket,
            object_key=upload_task.object_key,
            upload_id=upload_task.storage_upload_id,
        )
    except StorageAdapterError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to abort object storage multipart upload",
        ) from exc

    upload_task.status = UPLOAD_STATUS_CANCELLED
    upload_task.updated_at = datetime.utcnow()
    session.add(upload_task)
    session.commit()
    session.refresh(upload_task)
    return (
        upload_task.status,
        "Upload task aborted successfully",
    )


def cleanup_expired_upload_tasks(
    *,
    session: Session,
    settings: Settings,
    storage: ObjectStorageAdapter,
    organization_id: Optional[int] = None,
) -> Tuple[int, int]:
    """清理已过期且未结束的上传任务。"""

    statement = select(UploadTask).where(
        UploadTask.expires_at < datetime.utcnow(),
        UploadTask.status.in_(
            [
                UPLOAD_STATUS_INITIATED,
                UPLOAD_STATUS_UPLOADING,
                UPLOAD_STATUS_UPLOADED,
            ]
        ),
    )
    if organization_id is not None:
        statement = statement.where(UploadTask.organization_id == organization_id)
    tasks = list(session.exec(statement).all())
    expired_count = 0
    aborted_remote_count = 0
    for task in tasks:
        try:
            storage.abort_multipart_upload(
                bucket_name=settings.oss_bucket,
                object_key=task.object_key,
                upload_id=task.storage_upload_id,
            )
            aborted_remote_count += 1
        except StorageAdapterError:
            pass

        task.status = UPLOAD_STATUS_EXPIRED
        task.updated_at = datetime.utcnow()
        session.add(task)
        expired_count += 1

    if tasks:
        session.commit()

    return expired_count, aborted_remote_count
