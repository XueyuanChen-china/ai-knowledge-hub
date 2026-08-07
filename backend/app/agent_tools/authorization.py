"""Agent 工具权限矩阵。

工具权限和 FastAPI API 权限使用同一套角色策略，避免出现“接口禁止但 Agent 工具允许”
的旁路。资源组织归属仍由具体 handler 在 SQL 查询中再次校验。
"""

from dataclasses import dataclass

from app.agent_tools.schemas import ToolCallRequest, ToolExecutionContext
from app.security.policies import (
    PERMISSION_CONTENT_READ,
    PERMISSION_CHAT,
    PERMISSION_SEARCH,
    has_permission,
)


TOOL_PERMISSIONS = {
    "search_knowledge_base": PERMISSION_SEARCH,
    "get_document": PERMISSION_CONTENT_READ,
    "get_knowledge_item": PERMISSION_CONTENT_READ,
    "get_chunk_neighbors": PERMISSION_CONTENT_READ,
    "list_knowledge_base_documents": PERMISSION_CONTENT_READ,
    "search_conversation_history": PERMISSION_CHAT,
}


@dataclass(frozen=True)
class ToolAuthorizationDecision:
    allowed: bool
    required_permission: str = ""
    reason: str = ""


def authorize_tool_call(
    request: ToolCallRequest,
    context: ToolExecutionContext,
) -> ToolAuthorizationDecision:
    permission = TOOL_PERMISSIONS.get(request.name)
    if permission is None:
        return ToolAuthorizationDecision(
            allowed=False,
            reason="tool is not registered in the permission matrix",
        )

    if not has_permission(context.role, permission):
        return ToolAuthorizationDecision(
            allowed=False,
            required_permission=permission,
            reason="current role does not have the required tool permission",
        )

    if context.organization_id <= 0 or context.knowledge_base_id <= 0:
        return ToolAuthorizationDecision(
            allowed=False,
            required_permission=permission,
            reason="organization and knowledge base scope are required",
        )

    if request.name == "search_conversation_history":
        if context.conversation_id is None or context.user_id is None:
            return ToolAuthorizationDecision(
                allowed=False,
                required_permission=permission,
                reason="conversation and user scope are required",
            )

    return ToolAuthorizationDecision(
        allowed=True,
        required_permission=permission,
        reason="authorized",
    )
