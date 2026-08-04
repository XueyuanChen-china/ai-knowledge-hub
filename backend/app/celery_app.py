from celery import Celery
from kombu import Exchange, Queue

from app.config import get_settings


def create_celery_app() -> Celery:
    """创建 Celery 应用实例。

    Phase B 先只连接 RabbitMQ 并跑 hello task。
    后续上传流水线阶段会继续复用这个 app。
    """

    settings = get_settings()
    result_backend = settings.celery_result_backend.strip() or None
    task_exchange = Exchange(
        settings.celery_task_default_queue,
        type="direct",
        durable=True,
    )
    dead_letter_exchange = Exchange(
        settings.celery_dead_letter_exchange,
        type="direct",
        durable=True,
    )
    task_queue = Queue(
        settings.celery_task_default_queue,
        exchange=task_exchange,
        routing_key=settings.celery_task_default_queue,
        durable=True,
        queue_arguments={
            "x-dead-letter-exchange": settings.celery_dead_letter_exchange,
            "x-dead-letter-routing-key": settings.celery_dead_letter_routing_key,
        },
    )
    embed_queue = Queue(
        settings.celery_embed_queue,
        exchange=task_exchange,
        routing_key=settings.celery_embed_queue,
        durable=True,
        queue_arguments={
            "x-dead-letter-exchange": settings.celery_dead_letter_exchange,
            "x-dead-letter-routing-key": settings.celery_dead_letter_routing_key,
        },
    )
    dead_letter_queue = Queue(
        settings.celery_dead_letter_queue,
        exchange=dead_letter_exchange,
        routing_key=settings.celery_dead_letter_routing_key,
        durable=True,
    )
    celery_app = Celery(
        "ai_knowledge_hub",
        broker=settings.celery_broker_url,
        backend=result_backend,
        include=["app.tasks.upload_tasks"],
    )
    celery_app.conf.update(
        task_default_queue=settings.celery_task_default_queue,
        task_default_exchange=settings.celery_task_default_queue,
        task_default_exchange_type="direct",
        task_default_routing_key=settings.celery_task_default_queue,
        task_queues=(task_queue, embed_queue, dead_letter_queue),
        task_routes={
            "uploads.embed": {
                "queue": settings.celery_embed_queue,
                "routing_key": settings.celery_embed_queue,
            },
        },
        broker_transport_options={
            "confirm_publish": settings.celery_publisher_confirm,
        },
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_acks_on_failure_or_timeout=False,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="Asia/Shanghai",
        enable_utc=True,
        task_track_started=True,
    )
    return celery_app


celery_app = create_celery_app()
