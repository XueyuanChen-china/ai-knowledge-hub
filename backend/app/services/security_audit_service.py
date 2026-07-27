"""账号与身份操作的安全审计封装。"""

import json
from typing import Any, Optional

from fastapi import Request
from sqlmodel import Session

from app.db.models import SecurityAuditLog


def request_ip(request: Request) -> str:
    """提取请求 IP；反向代理可信头处理留到部署层统一配置。"""

    return request.client.host if request.client else ""


def request_user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")[:500]


def add_security_audit_log(
    session: Session,
    *,
    action: str,
    outcome: str = "success",
    organization_id: Optional[int] = None,
    actor_user_id: Optional[int] = None,
    target_type: str = "",
    target_id: str = "",
    request: Optional[Request] = None,
    details: Optional[dict[str, Any]] = None,
) -> SecurityAuditLog:
    """把审计记录加入当前事务，是否 commit 由调用方决定。"""

    record = SecurityAuditLog(
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        action=action,
        outcome=outcome,
        target_type=target_type,
        target_id=target_id,
        ip_address=request_ip(request) if request is not None else "",
        user_agent=request_user_agent(request) if request is not None else "",
        details_json=json.dumps(details or {}, ensure_ascii=False),
    )
    session.add(record)
    return record
