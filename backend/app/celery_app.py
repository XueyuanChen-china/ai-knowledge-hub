from celery import Celery

from app.config import get_settings


def create_celery_app() -> Celery:
    """创建 Celery 应用实例。

    Phase B 先只连接 RabbitMQ 并跑 hello task。
    后续上传流水线阶段会继续复用这个 app。
    """

    settings = get_settings()
    result_backend = settings.celery_result_backend.strip() or None
    celery_app = Celery(
        "ai_knowledge_hub",
        broker=settings.celery_broker_url,
        backend=result_backend,
        include=["app.tasks.upload_tasks"],
    )
    celery_app.conf.update(
        task_default_queue=settings.celery_task_default_queue,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="Asia/Shanghai",
        enable_utc=True,
        task_track_started=True,
    )
    return celery_app


celery_app = create_celery_app()
