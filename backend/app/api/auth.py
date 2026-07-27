from datetime import datetime
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session, select

from app.config import Settings, get_settings
from app.db.database import get_session
from app.db.models import Organization, OrganizationMembership, User
from app.schemas.auth import (
    AuthTokenResponse,
    AuthUserRead,
    LoginRequest,
    MeResponse,
    OrganizationRead,
)
from app.security.dependencies import (
    Principal,
    authentication_error,
    get_current_principal,
)
from app.security.passwords import DUMMY_PASSWORD_HASH, verify_password
from app.security.rate_limit import (
    check_login_rate_limit,
    clear_login_failures,
    record_login_failure,
)
from app.security.revocation import TokenRevocationUnavailable, revoke_token
from app.security.tokens import create_access_token, decode_access_token
from app.services.security_audit_service import add_security_audit_log

router = APIRouter(prefix="/api/auth", tags=["auth"])
bearer_scheme = HTTPBearer(auto_error=False)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def generic_login_error() -> HTTPException:
    """统一登录失败文案，避免暴露账号是否存在。"""

    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password",
        headers={"WWW-Authenticate": "Bearer"},
    )


def build_user_read(user: User) -> AuthUserRead:
    return AuthUserRead(
        id=user.id,
        email=user.email,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


def build_organization_read(organization: Organization) -> OrganizationRead:
    return OrganizationRead(
        id=organization.id,
        name=organization.name,
        slug=organization.slug,
    )


@router.post("/login", response_model=AuthTokenResponse)
def login(
    payload: LoginRequest,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AuthTokenResponse:
    """验证账号并签发短期 access token。"""

    email = normalize_email(payload.email)
    client_host = request.client.host if request.client else "unknown"
    rate_key = f"{client_host}:{email}"
    retry_after = check_login_rate_limit(rate_key, settings)
    if retry_after:
        add_security_audit_log(
            session,
            action="auth.login.failed",
            outcome="rate_limited",
            target_type="email",
            request=request,
            details={"email": email},
        )
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    user = session.exec(select(User).where(User.email == email)).first()
    password_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    password_valid = verify_password(payload.password, password_hash)
    if user is None or not user.is_active or not password_valid:
        record_login_failure(rate_key, settings)
        add_security_audit_log(
            session,
            action="auth.login.failed",
            outcome="failure",
            target_type="user" if user is not None else "email",
            target_id=str(user.id) if user is not None else "",
            request=request,
            details={"email": email},
        )
        session.commit()
        raise generic_login_error()

    membership = session.exec(
        select(OrganizationMembership)
        .where(OrganizationMembership.user_id == user.id)
        .order_by(OrganizationMembership.id)
    ).first()
    if membership is None:
        # 用户存在但没有组织关系时不允许进入业务系统。
        add_security_audit_log(
            session,
            action="auth.login.failed",
            outcome="failure",
            target_type="user",
            target_id=str(user.id),
            request=request,
            details={"reason": "membership_missing"},
        )
        session.commit()
        raise generic_login_error()

    organization = session.get(Organization, membership.organization_id)
    if organization is None:
        add_security_audit_log(
            session,
            action="auth.login.failed",
            outcome="failure",
            target_type="user",
            target_id=str(user.id),
            request=request,
            details={"reason": "organization_missing"},
        )
        session.commit()
        raise generic_login_error()

    clear_login_failures(rate_key)
    user.last_login_at = datetime.utcnow()
    user.updated_at = datetime.utcnow()
    session.add(user)
    add_security_audit_log(
        session,
        organization_id=organization.id,
        actor_user_id=user.id,
        action="auth.login.success",
        target_type="user",
        target_id=str(user.id),
        request=request,
    )
    session.commit()
    session.refresh(user)

    try:
        access_token, expires_in = create_access_token(
            user_id=user.id,
            organization_id=organization.id,
            role=membership.role,
            token_version=user.token_version,
            settings=settings,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication service is not configured",
        ) from exc

    return AuthTokenResponse(
        access_token=access_token,
        expires_in=expires_in,
        user=build_user_read(user),
        organization=build_organization_read(organization),
        role=membership.role,
    )


@router.get("/me", response_model=MeResponse)
def get_me(
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> MeResponse:
    """返回当前 token 对应的用户、组织和角色。"""

    user = session.get(User, principal.user_id)
    organization = session.get(Organization, principal.organization_id)
    if user is None or organization is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    return MeResponse(
        user=build_user_read(user),
        organization=build_organization_read(organization),
        role=principal.role,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_session),
) -> None:
    """撤销当前 access token。

    前端随后还会删除 sessionStorage 中的 token；服务端黑名单用于阻止已复制的
    token 在自然过期前继续访问接口。
    """

    if credentials is None:
        raise authentication_error()

    try:
        payload = decode_access_token(credentials.credentials, settings)
        token_id = str(payload["jti"])
        expires_at = int(payload["exp"])
        user_id = int(payload["sub"])
        organization_id = int(payload["org_id"])
        revoke_token(
            token_id=token_id,
            expires_at=expires_at,
            settings=settings,
        )
        add_security_audit_log(
            session,
            organization_id=organization_id,
            actor_user_id=user_id,
            action="auth.logout",
            target_type="user",
            target_id=str(user_id),
            request=request,
        )
        session.commit()
    except TokenRevocationUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is temporarily unavailable",
        ) from exc
    except (ValueError, KeyError, TypeError, jwt.PyJWTError, RuntimeError) as exc:
        raise authentication_error() from exc
