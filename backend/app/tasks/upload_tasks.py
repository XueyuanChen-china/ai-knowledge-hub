from datetime import datetime

from celery.exceptions import Reject
from sqlmodel import Session

from app.celery_app import celery_app
from app.db.database import engine
from app.db.models import UploadProcessingJob, UploadTask
from app.observability.context import bind_context, new_correlation_id
from app.observability.metrics import get_metrics
from app.services.upload_audit_service import log_upload_event


def get_task_context(task, job_id: int) -> dict[str, str]:
    """从 Celery message headers 恢复 API 投递时建立的关联上下文。"""

    headers = dict(task.request.headers or {})
    trace_id = str(headers.get("trace_id") or new_correlation_id())
    return {
        "request_id": str(headers.get("request_id") or trace_id),
        "trace_id": trace_id,
        "upload_id": str(headers.get("upload_id") or ""),
        "processing_job_id": str(headers.get("processing_job_id") or job_id),
        "celery_task_id": str(task.request.id or ""),
    }


@celery_app.task(name="uploads.hello", bind=True)
def upload_hello_task(self, job_id: int, message: str = "hello") -> dict[str, object]:
    """Celery/RabbitMQ Phase B 验证任务。

    这个 task 只证明 worker 能收到消息，并能回写 PostgreSQL。
    它不执行真实下载、解析、切片或索引。
    """

    with bind_context(**get_task_context(self, job_id)):
        now = datetime.utcnow()
        with Session(engine) as session:
            job = session.get(UploadProcessingJob, job_id)
            if job is None:
                raise ValueError(f"UploadProcessingJob not found: {job_id}")

            upload_task = session.get(UploadTask, job.upload_task_id)
            if upload_task is None:
                raise ValueError(f"UploadTask not found for job: {job_id}")

            job.celery_task_id = job.celery_task_id or str(self.request.id)
            job.current_step = "celery_hello_received"
            job.updated_at = now
            session.add(job)
            log_upload_event(
                session=session,
                upload_task_id=upload_task.id,
                processing_job_id=job.id,
                actor=upload_task.created_by,
                event_type="processing_job_celery_hello_received",
                detail={
                    "celery_task_id": job.celery_task_id,
                    "stage": job.stage,
                    "message": message,
                },
            )
            session.commit()
            get_metrics().record_celery_task(job.stage, "completed")

            return {
                "job_id": job.id,
                "stage": job.stage,
                "celery_task_id": job.celery_task_id,
                "message": message,
            }


@celery_app.task(name="uploads.download", bind=True)
def upload_download_stage_task(self, job_id: int) -> dict[str, object]:
    """消费 upload pipeline 的 download 阶段 job。

    Phase C 只下载对象并完成基础文件校验，暂不创建 document，也不执行解析、切片和索引。
    """

    from app.config import get_settings
    from app.services.upload_postprocess_service import run_download_stage_job

    with bind_context(**get_task_context(self, job_id)):
        settings = get_settings()
        result = run_download_stage_job(
            job_id=job_id,
            settings=settings,
            celery_task_id=str(self.request.id),
        )
        get_metrics().record_celery_task("download", result.processing_status)
        if result.processing_status == "retry_scheduled":
            raise self.retry(
                exc=RuntimeError(result.processing_error_message),
                countdown=max(1, settings.upload_job_retry_backoff_seconds),
            )
        if result.processing_status == "failed":
            # 业务重试已经耗尽。明确 reject 且不重新入主队列，让 RabbitMQ
            # 根据主队列的 DLX 配置把原始消息转入死信队列，方便排查。
            raise Reject(
                result.processing_error_message or "download stage failed",
                requeue=False,
            )
        return {
            "job_id": result.processing_job_id,
            "stage": "download",
            "status": result.processing_status,
            "document_id": result.document_id,
            "error_message": result.processing_error_message,
        }


def _run_pipeline_stage_task(self, job_id: int, stage: str) -> dict[str, object]:
    """统一执行 download 之后的阶段 task，减少 Celery 壳代码重复。"""

    from app.config import get_settings
    from app.services.upload_postprocess_service import run_pipeline_stage_job

    with bind_context(**get_task_context(self, job_id)):
        settings = get_settings()
        result = run_pipeline_stage_job(
            job_id=job_id,
            settings=settings,
            celery_task_id=str(self.request.id),
        )
        get_metrics().record_celery_task(stage, result.processing_status)
        if result.processing_status == "retry_scheduled":
            raise self.retry(
                exc=RuntimeError(result.processing_error_message),
                countdown=max(1, settings.upload_job_retry_backoff_seconds),
            )
        if result.processing_status == "failed":
            raise Reject(
                result.processing_error_message or f"{stage} stage failed",
                requeue=False,
            )
        return {
            "job_id": result.processing_job_id,
            "stage": stage,
            "status": result.processing_status,
            "document_id": result.document_id,
            "error_message": result.processing_error_message,
        }


@celery_app.task(name="uploads.validate", bind=True)
def upload_validate_stage_task(self, job_id: int) -> dict[str, object]:
    """消费 validate 阶段 job。"""

    return _run_pipeline_stage_task(self, job_id, "validate")


@celery_app.task(name="uploads.parse", bind=True)
def upload_parse_stage_task(self, job_id: int) -> dict[str, object]:
    """消费 parse 阶段 job。"""

    return _run_pipeline_stage_task(self, job_id, "parse")


@celery_app.task(name="uploads.split", bind=True)
def upload_split_stage_task(self, job_id: int) -> dict[str, object]:
    """消费 split 阶段 job。"""

    return _run_pipeline_stage_task(self, job_id, "split")


@celery_app.task(name="uploads.embed", bind=True)
def upload_embed_stage_task(self, job_id: int) -> dict[str, object]:
    """消费 embed 阶段 job。"""

    return _run_pipeline_stage_task(self, job_id, "embed")


@celery_app.task(name="uploads.index", bind=True)
def upload_index_stage_task(self, job_id: int) -> dict[str, object]:
    """消费 index 阶段 job。"""

    return _run_pipeline_stage_task(self, job_id, "index")
