from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
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
from app.security.dependencies import Principal, get_current_principal
from app.security.passwords import DUMMY_PASSWORD_HASH, verify_password
from app.security.rate_limit import (
    check_login_rate_limit,
    clear_login_failures,
    record_login_failure,
)
from app.security.tokens import create_access_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


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
        raise generic_login_error()

    membership = session.exec(
        select(OrganizationMembership)
        .where(OrganizationMembership.user_id == user.id)
        .order_by(OrganizationMembership.id)
    ).first()
    if membership is None:
        # 用户存在但没有组织关系时不允许进入业务系统。
        raise generic_login_error()

    organization = session.get(Organization, membership.organization_id)
    if organization is None:
        raise generic_login_error()

    clear_login_failures(rate_key)
    user.last_login_at = datetime.utcnow()
    user.updated_at = datetime.utcnow()
    session.add(user)
    session.commit()
    session.refresh(user)

    try:
        access_token, expires_in = create_access_token(
            user_id=user.id,
            organization_id=organization.id,
            role=membership.role,
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
