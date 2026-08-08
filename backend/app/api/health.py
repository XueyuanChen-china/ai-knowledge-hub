"""面向容器编排和排障的存活、就绪与指标接口。"""

from __future__ import annotations

import socket
from typing import Callable
from urllib.parse import urlparse

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.config import get_settings
from app.db.database import engine
from app.observability.metrics import get_metrics
from app.services.vector_service import get_elasticsearch_client

router = APIRouter(prefix="/health", tags=["health"])


def _component(status_text: str, detail: str = "") -> dict[str, str]:
    payload = {"status": status_text}
    if detail:
        payload["detail"] = detail
    return payload


def check_postgresql() -> tuple[bool, str]:
    """检查 PostgreSQL 可连接；它是所有业务 API 的必要依赖。"""

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True, ""
    except Exception as exc:
        return False, type(exc).__name__


def check_elasticsearch() -> tuple[bool, str]:
    """检查 ES；只有搜索和索引路径把它当作必要依赖。"""

    try:
        if get_elasticsearch_client().ping():
            return True, ""
        return False, "ping returned false"
    except Exception as exc:
        return False, type(exc).__name__


def check_rabbitmq() -> tuple[bool, str]:
    """检查 RabbitMQ TCP 可达性；上传后处理使用它作为 broker。"""

    try:
        broker_url = get_settings().celery_broker_url
        parsed = urlparse(broker_url)
        if not parsed.hostname:
            return False, "broker host is missing"
        with socket.create_connection((parsed.hostname, parsed.port or 5672), timeout=2.0):
            return True, ""
    except Exception as exc:
        return False, type(exc).__name__


def build_readiness(required_checks: dict[str, Callable[[], tuple[bool, str]]]) -> tuple[bool, dict[str, dict[str, str]]]:
    """运行一组必需依赖检查，返回统一 JSON 结构。"""

    components: dict[str, dict[str, str]] = {}
    is_ready = True
    for name, check in required_checks.items():
        is_component_ready, detail = check()
        components[name] = _component("ready" if is_component_ready else "degraded", detail)
        is_ready = is_ready and is_component_ready
    return is_ready, components


@router.get("/live")
def live() -> dict[str, str]:
    """只说明 Python/FastAPI 进程仍存活，不探测外部依赖。"""

    return {"status": "ok"}


@router.get("/ready")
def ready(response: Response) -> dict[str, object]:
    """通用业务 API readiness：只把 PostgreSQL 作为硬依赖。"""

    is_ready, components = build_readiness({"postgresql": check_postgresql})
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if is_ready else "not_ready", "components": components}


@router.get("/ready/search")
def search_ready(response: Response) -> dict[str, object]:
    """搜索/索引路径 readiness：PostgreSQL 与 ES 都必须就绪。"""

    is_ready, components = build_readiness(
        {"postgresql": check_postgresql, "elasticsearch": check_elasticsearch}
    )
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if is_ready else "degraded", "components": components}


@router.get("/ready/uploads")
def uploads_ready(response: Response) -> dict[str, object]:
    """上传处理 readiness：Celery broker 不可用时明确降级。"""

    is_ready, components = build_readiness(
        {"postgresql": check_postgresql, "rabbitmq": check_rabbitmq}
    )
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if is_ready else "degraded", "components": components}


@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    """导出 Prometheus text exposition 格式的进程内低基数指标。"""

    return Response(
        content=get_metrics().render_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
