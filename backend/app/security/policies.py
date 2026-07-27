"""集中式 RBAC 角色和权限策略。"""

from typing import FrozenSet

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"

PERMISSION_KNOWLEDGE_BASE_READ = "knowledge_base:read"
PERMISSION_KNOWLEDGE_BASE_WRITE = "knowledge_base:write"
PERMISSION_KNOWLEDGE_BASE_DELETE = "knowledge_base:delete"
PERMISSION_CONTENT_READ = "content:read"
PERMISSION_CONTENT_WRITE = "content:write"
PERMISSION_CONTENT_DELETE = "content:delete"
PERMISSION_SEARCH = "search:use"
PERMISSION_CHAT = "chat:use"
PERMISSION_UPLOAD = "upload:create"
PERMISSION_USER_MANAGE = "user:manage"

ROLE_PERMISSIONS: dict[str, FrozenSet[str]] = {
    ROLE_OWNER: frozenset(
        {
            PERMISSION_KNOWLEDGE_BASE_READ,
            PERMISSION_KNOWLEDGE_BASE_WRITE,
            PERMISSION_KNOWLEDGE_BASE_DELETE,
            PERMISSION_CONTENT_READ,
            PERMISSION_CONTENT_WRITE,
            PERMISSION_CONTENT_DELETE,
            PERMISSION_SEARCH,
            PERMISSION_CHAT,
            PERMISSION_UPLOAD,
            PERMISSION_USER_MANAGE,
        }
    ),
    ROLE_ADMIN: frozenset(
        {
            PERMISSION_KNOWLEDGE_BASE_READ,
            PERMISSION_KNOWLEDGE_BASE_WRITE,
            PERMISSION_KNOWLEDGE_BASE_DELETE,
            PERMISSION_CONTENT_READ,
            PERMISSION_CONTENT_WRITE,
            PERMISSION_CONTENT_DELETE,
            PERMISSION_SEARCH,
            PERMISSION_CHAT,
            PERMISSION_UPLOAD,
            PERMISSION_USER_MANAGE,
        }
    ),
    ROLE_EDITOR: frozenset(
        {
            PERMISSION_KNOWLEDGE_BASE_READ,
            PERMISSION_KNOWLEDGE_BASE_WRITE,
            PERMISSION_CONTENT_READ,
            PERMISSION_CONTENT_WRITE,
            PERMISSION_SEARCH,
            PERMISSION_CHAT,
            PERMISSION_UPLOAD,
        }
    ),
    ROLE_VIEWER: frozenset(
        {
            PERMISSION_KNOWLEDGE_BASE_READ,
            PERMISSION_CONTENT_READ,
            PERMISSION_SEARCH,
            PERMISSION_CHAT,
        }
    ),
}


def has_permission(role: str, permission: str) -> bool:
    """判断角色是否拥有指定权限。未知角色默认无权限。"""

    return permission in ROLE_PERMISSIONS.get(role, frozenset())
