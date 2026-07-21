import json
from typing import Any, Optional

from sqlmodel import Session

from app.db.models import UploadAuditLog


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
    """记录上传治理事件。"""

    audit_log = UploadAuditLog(
        upload_task_id=upload_task_id,
        processing_job_id=processing_job_id,
        actor=actor,
        event_type=event_type,
        level=level,
        detail_json=json.dumps(detail or {}, ensure_ascii=False),
    )
    session.add(audit_log)
    session.flush()
    return audit_log
