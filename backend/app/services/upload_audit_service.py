import json
from typing import Any, Optional

from sqlmodel import Session

from app.db.models import UploadAuditLog
from app.observability.context import get_observability_context
from app.observability.logging import redact_value


def log_upload_event(
    *,
    session: Session,
    event_type: str,
    level: str = "info",
    actor: str = "",
    upload_task_id: Optional[int] = None,
    processing_job_id: Optional[int] = None,
    detail: Optional[dict[str, Any]] = None,
) -> UploadAuditLog:
    """记录上传治理事件，并自动附加当前 request/trace/task 关联字段。"""

    merged_detail = dict(detail or {})
    # 关联字段进入审计表后，可以通过 upload_id / job_id / trace_id 串回 HTTP 和 Worker 日志。
    for key, value in get_observability_context().items():
        merged_detail.setdefault(key, value)

    audit_log = UploadAuditLog(
        upload_task_id=upload_task_id,
        processing_job_id=processing_job_id,
        actor=actor,
        event_type=event_type,
        level=level,
        detail_json=json.dumps(redact_value(merged_detail), ensure_ascii=False),
    )
    session.add(audit_log)
    session.flush()
    return audit_log
