"""只读工具调用审计。

复用现有 security_audit_logs 表，不保存完整 query、工具正文、JWT、密钥或 URL。
"""

import json
from typing import Any, Dict

from sqlmodel import Session

from app.agent_tools.schemas import ToolCallRequest, ToolExecutionContext, ToolExecutionResult
from app.db.models import SecurityAuditLog
from app.observability.context import get_request_id, get_trace_id


def record_tool_call_audit(
    session: Session,
    *,
    request: ToolCallRequest,
    context: ToolExecutionContext,
    result: ToolExecutionResult,
    allowed: bool,
    duration_seconds: float,
    reason: str,
    required_permission: str = "",
) -> None:
    """记录允许、拒绝、参数错误和执行失败，审计失败不改变业务结果。"""

    details: Dict[str, Any] = {
        "conversation_id": context.conversation_id,
        "request_id": context.request_id or get_request_id(),
        "trace_id": context.trace_id or get_trace_id(),
        "allowed": allowed,
        "required_permission": required_permission,
        "duration_ms": round(max(0.0, duration_seconds) * 1000, 3),
        "arguments": sanitize_tool_arguments(request.arguments),
        "result_ok": result.ok,
        "error_code": result.error_code,
        "reason": reason,
    }
    audit = SecurityAuditLog(
        organization_id=context.organization_id if context.organization_id > 0 else None,
        actor_user_id=context.user_id,
        action="agent_tool_call",
        outcome=(
            "success"
            if result.ok
            else ("denied" if not allowed else "failed")
        ),
        target_type="agent_tool",
        target_id=request.name[:100],
        details_json=json.dumps(details, ensure_ascii=False, default=str),
    )
    try:
        session.add(audit)
        session.commit()
    except Exception:
        # 审计系统不能把问答主链路变成不可用；生产环境应由日志和告警发现此异常。
        try:
            session.rollback()
        except Exception:
            pass


def sanitize_tool_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """只保留可审计的参数形状，省略 query 和未知参数的原始内容。"""

    sanitized: dict[str, Any] = {}
    for key, value in arguments.items():
        if key == "query":
            sanitized[key] = "<omitted>"
        elif key in {"document_id", "knowledge_item_id", "chunk_id", "top_k", "radius", "limit"}:
            sanitized[key] = value
        else:
            sanitized[key] = "<omitted>"
    return sanitized
