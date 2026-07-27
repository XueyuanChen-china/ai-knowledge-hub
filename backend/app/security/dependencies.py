"""FastAPI 认证依赖和权限依赖。"""

from dataclasses import dataclass
from typing import Callable, Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session, select

from app.config import Settings, get_settings
from app.db.database import get_session
from app.db.models import OrganizationMembership, User
from app.security.policies import has_permission
from app.security.revocation import TokenRevocationUnavailable, is_token_revoked
from app.security.tokens import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    """当前请求的可信身份。"""

    user_id: int
    organization_id: int
    role: str
    email: str
    token_id: str


def authentication_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_principal(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Principal:
    """校验 Bearer token，并确认用户和 membership 仍然有效。"""

    if credentials is None:
        raise authentication_error()

    try:
        payload = decode_access_token(credentials.credentials, settings)
        user_id = int(payload["sub"])
        organization_id = int(payload["org_id"])
        token_id = str(payload["jti"])
        token_version = int(payload["ver"])
    except (ValueError, KeyError, TypeError, jwt.PyJWTError, RuntimeError) as exc:
        raise authentication_error() from exc

    try:
        if is_token_revoked(token_id, settings):
            raise authentication_error()
    except TokenRevocationUnavailable as exc:
        # 黑名单存储不可用时拒绝鉴权，避免撤销机制失效后继续放行 token。
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is temporarily unavailable",
        ) from exc

    user = session.get(User, user_id)
    membership = session.exec(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.organization_id == organization_id,
        )
    ).first()
    if (
        user is None
        or not user.is_active
        or membership is None
        or user.token_version != token_version
    ):
        raise authentication_error()

    return Principal(
        user_id=user.id,
        organization_id=organization_id,
        role=membership.role,
        email=user.email,
        token_id=token_id,
    )


def require_permission(permission: str) -> Callable:
    """生成一个 FastAPI dependency，集中检查角色权限。"""

    def dependency(
        principal: Principal = Depends(get_current_principal),
    ) -> Principal:
        if not has_permission(principal.role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return principal

    return dependency
