from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException, status
from sqlmodel import Session

from app.config import Settings
from app.db.models import UploadProcessingJob, UploadTask
from app.services.upload_audit_service import log_upload_event
from app.tasks.upload_tasks import (
    upload_download_stage_task,
    upload_embed_stage_task,
    upload_hello_task,
    upload_index_stage_task,
    upload_parse_stage_task,
    upload_split_stage_task,
    upload_validate_stage_task,
)


@dataclass
class UploadCeleryHelloDispatchResult:
    """hello task 投递结果。"""

    processing_job_id: int
    celery_task_id: str
    queue: str
    status: str
    current_step: str


@dataclass
class UploadCeleryDownloadDispatchResult:
    """download 阶段 task 投递结果。"""

    processing_job_id: int
    celery_task_id: str
    queue: str
    status: str
    current_step: str


STAGE_TASKS = {
    "download": upload_download_stage_task,
    "validate": upload_validate_stage_task,
    "parse": upload_parse_stage_task,
    "split": upload_split_stage_task,
    "embed": upload_embed_stage_task,
    "index": upload_index_stage_task,
}


def dispatch_upload_hello_task(
    *,
    processing_job_id: int,
    message: str,
    session: Session,
    settings: Settings,
) -> UploadCeleryHelloDispatchResult:
    """投递 Phase B 的 Celery hello task，并回写 celery_task_id。"""

    job = session.get(UploadProcessingJob, processing_job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Upload processing job not found",
        )

    upload_task = session.get(UploadTask, job.upload_task_id)
    if upload_task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Upload task not found for processing job",
        )

    async_result = upload_hello_task.apply_async(
        args=[job.id],
        kwargs={"message": message},
        queue=settings.celery_task_default_queue,
    )

    now = datetime.utcnow()
    job.celery_task_id = str(async_result.id)
    job.current_step = "celery_hello_dispatched"
    job.updated_at = now
    session.add(job)
    log_upload_event(
        session=session,
        upload_task_id=upload_task.id,
        processing_job_id=job.id,
        actor=upload_task.created_by,
        event_type="processing_job_celery_hello_dispatched",
        detail={
            "celery_task_id": job.celery_task_id,
            "queue": settings.celery_task_default_queue,
            "stage": job.stage,
        },
    )
    session.commit()
    session.refresh(job)

    return UploadCeleryHelloDispatchResult(
        processing_job_id=job.id,
        celery_task_id=job.celery_task_id,
        queue=settings.celery_task_default_queue,
        status=job.status,
        current_step=job.current_step,
    )


def dispatch_upload_download_stage_job(
    *,
    processing_job_id: int,
    session: Session,
    settings: Settings,
) -> UploadCeleryDownloadDispatchResult:
    """把一个 download 阶段 job 投递给 Celery worker。

    这里暂时只负责发消息和记录 task id，不在 API 进程里下载对象。
    真正的下载、magic number 校验和 SHA256 校验由 worker 执行。
    """

    job = session.get(UploadProcessingJob, processing_job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Upload processing job not found",
        )
    if job.stage != "download":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only download stage jobs can be dispatched by this task",
        )

    upload_task = session.get(UploadTask, job.upload_task_id)
    if upload_task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Upload task not found for processing job",
        )

    async_result = upload_download_stage_task.apply_async(
        args=[job.id],
        queue=settings.celery_task_default_queue,
    )

    now = datetime.utcnow()
    job.celery_task_id = str(async_result.id)
    job.current_step = "celery_download_dispatched"
    job.updated_at = now
    session.add(job)
    log_upload_event(
        session=session,
        upload_task_id=upload_task.id,
        processing_job_id=job.id,
        actor=upload_task.created_by,
        event_type="processing_job_celery_download_dispatched",
        detail={
            "celery_task_id": job.celery_task_id,
            "queue": settings.celery_task_default_queue,
            "stage": job.stage,
        },
    )
    session.commit()
    session.refresh(job)

    return UploadCeleryDownloadDispatchResult(
        processing_job_id=job.id,
        celery_task_id=job.celery_task_id,
        queue=settings.celery_task_default_queue,
        status=job.status,
        current_step=job.current_step,
    )


def dispatch_upload_stage_job(
    *,
    processing_job_id: int,
    session: Session,
    settings: Settings,
) -> UploadCeleryDownloadDispatchResult:
    """按 job.stage 投递任意一个阶段 task。"""

    job = session.get(UploadProcessingJob, processing_job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Upload processing job not found",
        )
    task = STAGE_TASKS.get(job.stage)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Unsupported upload processing stage: {job.stage}",
        )

    upload_task = session.get(UploadTask, job.upload_task_id)
    if upload_task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Upload task not found for processing job",
        )

    async_result = task.apply_async(
        args=[job.id],
        queue=settings.celery_task_default_queue,
    )
    now = datetime.utcnow()
    job.celery_task_id = str(async_result.id)
    job.current_step = f"celery_{job.stage}_dispatched"
    job.updated_at = now
    session.add(job)
    log_upload_event(
        session=session,
        upload_task_id=upload_task.id,
        processing_job_id=job.id,
        actor=upload_task.created_by,
        event_type="processing_job_celery_stage_dispatched",
        detail={
            "celery_task_id": job.celery_task_id,
            "queue": settings.celery_task_default_queue,
            "stage": job.stage,
        },
    )
    session.commit()
    session.refresh(job)

    return UploadCeleryDownloadDispatchResult(
        processing_job_id=job.id,
        celery_task_id=job.celery_task_id,
        queue=settings.celery_task_default_queue,
        status=job.status,
        current_step=job.current_step,
    )
