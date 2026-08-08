"""跨 HTTP、业务服务和 Celery task 传播关联 ID。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator, Optional
from uuid import uuid4

_request_id: ContextVar[str] = ContextVar("request_id", default="")
_trace_id: ContextVar[str] = ContextVar("trace_id", default="")
_upload_id: ContextVar[str] = ContextVar("upload_id", default="")
_processing_job_id: ContextVar[str] = ContextVar("processing_job_id", default="")
_celery_task_id: ContextVar[str] = ContextVar("celery_task_id", default="")


def new_correlation_id() -> str:
    """生成不包含业务语义的随机关联 ID。"""

    return uuid4().hex


def get_request_id() -> str:
    return _request_id.get()


def get_trace_id() -> str:
    return _trace_id.get()


def get_observability_context() -> dict[str, str]:
    """返回当前可安全写入日志和审计记录的关联字段。"""

    values = {
        "request_id": _request_id.get(),
        "trace_id": _trace_id.get(),
        "upload_id": _upload_id.get(),
        "processing_job_id": _processing_job_id.get(),
        "celery_task_id": _celery_task_id.get(),
    }
    return {key: value for key, value in values.items() if value}


@contextmanager
def bind_context(
    *,
    request_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    upload_id: Optional[str] = None,
    processing_job_id: Optional[int | str] = None,
    celery_task_id: Optional[str] = None,
) -> Iterator[None]:
    """在当前调用范围内绑定关联字段，并在退出后恢复上层上下文。"""

    tokens: list[tuple[ContextVar[str], Token[str]]] = []
    values: list[tuple[ContextVar[str], Optional[str]]] = [
        (_request_id, request_id),
        (_trace_id, trace_id),
        (_upload_id, upload_id),
        (
            _processing_job_id,
            str(processing_job_id) if processing_job_id is not None else None,
        ),
        (_celery_task_id, celery_task_id),
    ]
    try:
        for variable, value in values:
            if value is not None:
                tokens.append((variable, variable.set(value)))
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)
