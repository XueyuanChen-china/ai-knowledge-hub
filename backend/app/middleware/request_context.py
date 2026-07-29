"""HTTP 请求关联 ID、结构化开始/结束日志和基础请求指标。"""

from __future__ import annotations

import logging
import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.observability.context import bind_context, new_correlation_id
from app.observability.logging import log_event
from app.observability.metrics import get_metrics

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
TRACE_ID_HEADER = "X-Trace-ID"
MAX_CORRELATION_ID_LENGTH = 128


def normalize_incoming_id(value: str | None) -> str:
    """只接受短的可打印 ID，避免把任意请求头直接写入日志。"""

    normalized = (value or "").strip()
    if normalized and len(normalized) <= MAX_CORRELATION_ID_LENGTH and normalized.isprintable():
        return normalized
    return new_correlation_id()


def resolve_endpoint(request: Request) -> str:
    """优先使用 FastAPI route 模板，避免把资源 ID 写成高基数指标标签。"""

    route = request.scope.get("route")
    route_path = getattr(route, "path", "")
    return str(route_path or request.url.path)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """为每个 HTTP 请求建立 request_id / trace_id 上下文。"""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = normalize_incoming_id(request.headers.get(REQUEST_ID_HEADER))
        trace_id = normalize_incoming_id(request.headers.get(TRACE_ID_HEADER) or request_id)
        started_at = time.perf_counter()
        status_code = 500

        with bind_context(request_id=request_id, trace_id=trace_id):
            log_event(logger, "http_request_started", method=request.method, path=request.url.path)
            try:
                response = await call_next(request)
                status_code = response.status_code
                response.headers[REQUEST_ID_HEADER] = request_id
                response.headers[TRACE_ID_HEADER] = trace_id
                return response
            except Exception:
                log_event(logger, "http_request_failed", level=logging.ERROR, method=request.method, path=request.url.path)
                logger.exception("http_request_exception")
                raise
            finally:
                duration_seconds = time.perf_counter() - started_at
                endpoint = resolve_endpoint(request)
                get_metrics().record_http(
                    method=request.method,
                    endpoint=endpoint,
                    status_code=status_code,
                    duration_seconds=duration_seconds,
                )
                log_event(
                    logger,
                    "http_request_completed",
                    method=request.method,
                    endpoint=endpoint,
                    status_code=status_code,
                    duration_ms=round(duration_seconds * 1000, 2),
                )
