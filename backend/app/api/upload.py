from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.config import Settings, get_settings
from app.db.models import UploadProcessingJob, UploadTask
from app.db.database import get_session
from app.schemas.upload import (
    UploadAbortResponse,
    UploadBatchPresignRequest,
    UploadBatchPresignResponse,
    UploadCeleryHelloRequest,
    UploadCeleryHelloResponse,
    UploadCleanupExpiredResponse,
    UploadCompleteRequest,
    UploadCompleteResponse,
    UploadInitRequest,
    UploadInitResponse,
    UploadPartCompleteRequest,
    UploadPartCompleteResponse,
    UploadPartPresignRequest,
    UploadPartPresignResponse,
    UploadPartsListResponse,
    UploadTaskRead,
)
from app.services.storage.base import ObjectStorageAdapter
from app.services.storage.provider import get_object_storage_adapter
from app.services.upload_service import (
    abort_upload_task,
    cleanup_expired_upload_tasks,
    complete_upload_part,
    complete_upload_task,
    generate_batch_presigned_urls,
    generate_part_presigned_url,
    get_upload_parts_view,
    init_upload_task,
)
from app.services.upload_celery_service import dispatch_upload_hello_task
from app.security.dependencies import Principal, require_permission
from app.security.policies import PERMISSION_UPLOAD, PERMISSION_USER_MANAGE
from app.security.resource_access import get_upload_task_or_404 as get_scoped_upload_task_or_404

router = APIRouter(prefix="/uploads", tags=["uploads"])
upload_dependency = require_permission(PERMISSION_UPLOAD)
admin_dependency = require_permission(PERMISSION_USER_MANAGE)


@router.post("/init", response_model=UploadInitResponse)
def initialize_upload(
    payload: UploadInitRequest,
    principal: Principal = Depends(upload_dependency),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    storage: ObjectStorageAdapter = Depends(get_object_storage_adapter),
) -> UploadInitResponse:
    """初始化上传任务。"""

    upload_task = init_upload_task(
        payload,
        organization_id=principal.organization_id,
        created_by_user_id=principal.user_id,
        session=session,
        settings=settings,
        storage=storage,
    )
    return UploadInitResponse(
        upload_id=upload_task.upload_id,
        storage_provider=upload_task.storage_provider,
        bucket_name=upload_task.bucket_name,
        object_key=upload_task.object_key,
        part_size=upload_task.part_size,
        total_parts=upload_task.total_parts,
        status=upload_task.status,
        expires_at=upload_task.expires_at,
    )


@router.post("/cleanup-expired", response_model=UploadCleanupExpiredResponse)
def cleanup_expired_uploads(
    principal: Principal = Depends(admin_dependency),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    storage: ObjectStorageAdapter = Depends(get_object_storage_adapter),
) -> UploadCleanupExpiredResponse:
    """清理过期上传任务。"""

    expired_count, aborted_remote_count = cleanup_expired_upload_tasks(
        session=session,
        settings=settings,
        storage=storage,
        organization_id=principal.organization_id,
    )
    return UploadCleanupExpiredResponse(
        expired_count=expired_count,
        aborted_remote_count=aborted_remote_count,
    )


@router.get("/{upload_id}", response_model=UploadTaskRead)
def get_upload_task(
    upload_id: str,
    principal: Principal = Depends(upload_dependency),
    session: Session = Depends(get_session),
) -> UploadTaskRead:
    """查询上传任务详情。"""

    return get_scoped_upload_task_or_404(upload_id, principal, session)


@router.post(
    "/processing-jobs/{processing_job_id}/celery/hello",
    response_model=UploadCeleryHelloResponse,
)
def dispatch_processing_job_hello_task(
    processing_job_id: int,
    payload: UploadCeleryHelloRequest,
    principal: Principal = Depends(admin_dependency),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> UploadCeleryHelloResponse:
    """投递 Phase B 的 Celery hello task。

    这个接口只用于验证：
    FastAPI -> Celery -> RabbitMQ -> Worker -> PostgreSQL。
    """

    job = session.exec(
        select(UploadProcessingJob)
        .join(UploadTask, UploadProcessingJob.upload_task_id == UploadTask.id)
        .where(
            UploadProcessingJob.id == processing_job_id,
            UploadTask.organization_id == principal.organization_id,
        )
    ).first()
    if job is None:
        # 与其他资源接口一致，跨组织资源按不存在处理，避免 ID 枚举。
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Upload processing job not found",
        )

    result = dispatch_upload_hello_task(
        processing_job_id=processing_job_id,
        message=payload.message,
        session=session,
        settings=settings,
    )
    return UploadCeleryHelloResponse(
        processing_job_id=result.processing_job_id,
        celery_task_id=result.celery_task_id,
        queue=result.queue,
        status=result.status,
        current_step=result.current_step,
        detail="Celery hello task dispatched",
    )


@router.post("/{upload_id}/parts/presign", response_model=UploadPartPresignResponse)
def presign_upload_part(
    upload_id: str,
    payload: UploadPartPresignRequest,
    principal: Principal = Depends(upload_dependency),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    storage: ObjectStorageAdapter = Depends(get_object_storage_adapter),
) -> UploadPartPresignResponse:
    """为某个 part 生成预签名上传 URL。"""

    upload_task = get_scoped_upload_task_or_404(upload_id, principal, session)
    presigned_url = generate_part_presigned_url(
        upload_task,
        part_number=payload.part_number,
        session=session,
        settings=settings,
        storage=storage,
    )
    session.refresh(upload_task)
    return UploadPartPresignResponse(
        upload_id=upload_task.upload_id,
        part_number=payload.part_number,
        presigned_url=presigned_url,
        expire_seconds=settings.oss_presign_expire_seconds,
        status=upload_task.status,
    )


@router.post("/{upload_id}/parts/presign-batch", response_model=UploadBatchPresignResponse)
def presign_upload_parts_batch(
    upload_id: str,
    payload: UploadBatchPresignRequest,
    principal: Principal = Depends(upload_dependency),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    storage: ObjectStorageAdapter = Depends(get_object_storage_adapter),
) -> UploadBatchPresignResponse:
    """批量生成多个 part 的预签名上传 URL。"""

    upload_task = get_scoped_upload_task_or_404(upload_id, principal, session)
    items = generate_batch_presigned_urls(
        upload_task,
        part_numbers=payload.part_numbers,
        session=session,
        settings=settings,
        storage=storage,
    )
    session.refresh(upload_task)
    return UploadBatchPresignResponse(
        upload_id=upload_task.upload_id,
        expire_seconds=settings.oss_presign_expire_seconds,
        recommended_parallelism=settings.upload_recommended_parallelism,
        items=items,
        status=upload_task.status,
    )


@router.post("/{upload_id}/parts/complete", response_model=UploadPartCompleteResponse)
def complete_uploaded_part(
    upload_id: str,
    payload: UploadPartCompleteRequest,
    principal: Principal = Depends(upload_dependency),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    storage: ObjectStorageAdapter = Depends(get_object_storage_adapter),
) -> UploadPartCompleteResponse:
    """回写某个 part 已上传完成。"""

    upload_task = get_scoped_upload_task_or_404(upload_id, principal, session)
    upload_part = complete_upload_part(
        upload_task,
        payload,
        session=session,
        settings=settings,
        storage=storage,
    )
    session.refresh(upload_task)
    return UploadPartCompleteResponse(
        upload_id=upload_task.upload_id,
        part_number=upload_part.part_number,
        status=upload_task.status,
        completed_parts=upload_task.completed_parts,
        detail="Part upload confirmed successfully",
    )


@router.get("/{upload_id}/parts", response_model=UploadPartsListResponse)
def get_upload_parts(
    upload_id: str,
    principal: Principal = Depends(upload_dependency),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    storage: ObjectStorageAdapter = Depends(get_object_storage_adapter),
) -> UploadPartsListResponse:
    """查询断点续传所需的 part 状态。"""

    upload_task = get_scoped_upload_task_or_404(upload_id, principal, session)
    parts_view = get_upload_parts_view(
        upload_task,
        session=session,
        settings=settings,
        storage=storage,
    )
    return UploadPartsListResponse(**parts_view)


@router.post("/{upload_id}/complete", response_model=UploadCompleteResponse)
def complete_upload(
    upload_id: str,
    payload: UploadCompleteRequest,
    principal: Principal = Depends(upload_dependency),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    storage: ObjectStorageAdapter = Depends(get_object_storage_adapter),
) -> UploadCompleteResponse:
    """完成整个 multipart upload，并触发上传后处理。"""

    upload_task = get_scoped_upload_task_or_404(upload_id, principal, session)
    status_text, detail, completed_parts, postprocess_result = complete_upload_task(
        upload_task,
        payload,
        session=session,
        settings=settings,
        storage=storage,
    )
    session.refresh(upload_task)
    return UploadCompleteResponse(
        upload_id=upload_task.upload_id,
        status=status_text,
        detail=detail,
        completed_parts=completed_parts,
        document_id=postprocess_result.document_id,
        processing_job_id=postprocess_result.processing_job_id,
        processing_status=postprocess_result.processing_status,
        processing_error_message=postprocess_result.processing_error_message,
    )


@router.post("/{upload_id}/abort", response_model=UploadAbortResponse)
def abort_upload(
    upload_id: str,
    principal: Principal = Depends(upload_dependency),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    storage: ObjectStorageAdapter = Depends(get_object_storage_adapter),
) -> UploadAbortResponse:
    """取消上传任务。"""

    upload_task = get_scoped_upload_task_or_404(upload_id, principal, session)
    status_text, detail = abort_upload_task(
        upload_task,
        session=session,
        settings=settings,
        storage=storage,
    )
    return UploadAbortResponse(
        upload_id=upload_task.upload_id,
        status=status_text,
        detail=detail,
    )
