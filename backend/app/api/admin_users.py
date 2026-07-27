"""组织成员管理和安全审计 API。"""

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlmodel import Session, select

from app.db.database import get_session
from app.db.models import (
    OrganizationMembership,
    SecurityAuditLog,
    User,
)
from app.schemas.account import (
    MemberCreateRequest,
    MemberRoleUpdateRequest,
    MemberStatusUpdateRequest,
    OrganizationMemberRead,
    PasswordResetRequest,
    SecurityAuditLogListResponse,
    SecurityAuditLogRead,
)
from app.schemas.auth import AuthUserRead
from app.security.dependencies import Principal, require_permission
from app.security.passwords import hash_password
from app.security.policies import (
    PERMISSION_USER_MANAGE,
    ROLE_OWNER,
)
from app.services.security_audit_service import add_security_audit_log

router = APIRouter(prefix="/api/admin", tags=["admin"])
manager_dependency = require_permission(PERMISSION_USER_MANAGE)


def build_user_read(user: User) -> AuthUserRead:
    return AuthUserRead(
        id=user.id,
        email=user.email,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


def build_member_read(
    user: User,
    membership: OrganizationMembership,
) -> OrganizationMemberRead:
    return OrganizationMemberRead(
        membership_id=membership.id,
        role=membership.role,
        joined_at=membership.created_at,
        user=build_user_read(user),
    )


def get_member_or_404(
    session: Session,
    *,
    organization_id: int,
    user_id: int,
) -> tuple[User, OrganizationMembership]:
    result = session.exec(
        select(User, OrganizationMembership)
        .join(
            OrganizationMembership,
            OrganizationMembership.user_id == User.id,
        )
        .where(
            User.id == user_id,
            OrganizationMembership.organization_id == organization_id,
        )
    ).first()
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    return result


def ensure_owner_operation_allowed(
    principal: Principal,
    target_membership: Optional[OrganizationMembership] = None,
    requested_role: Optional[str] = None,
) -> None:
    """admin 不能创建、降级或修改 owner，防止权限提升和组织失控。"""

    touches_owner = (
        requested_role == ROLE_OWNER
        or (
            target_membership is not None
            and target_membership.role == ROLE_OWNER
        )
    )
    if touches_owner and principal.role != ROLE_OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an owner can manage the owner role",
        )


def ensure_not_last_active_owner(
    session: Session,
    *,
    organization_id: int,
    target_user: User,
    target_membership: OrganizationMembership,
) -> None:
    if target_membership.role != ROLE_OWNER or not target_user.is_active:
        return

    # 锁住当前组织所有 owner 关系行，避免两个并发请求各自看到“还有两个 owner”，
    # 随后又同时禁用/移除其中一个，最终把组织置于没有有效 owner 的状态。
    owner_rows = session.exec(
        select(User, OrganizationMembership)
        .join(User, User.id == OrganizationMembership.user_id)
        .where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.role == ROLE_OWNER,
        )
        .with_for_update()
    ).all()
    active_owner_count = sum(1 for owner, _ in owner_rows if owner.is_active)
    if active_owner_count <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The last active owner cannot be changed or removed",
        )


def add_admin_audit(
    session: Session,
    *,
    principal: Principal,
    request: Request,
    action: str,
    target_user: User,
    details: Optional[dict[str, object]] = None,
) -> None:
    add_security_audit_log(
        session,
        organization_id=principal.organization_id,
        actor_user_id=principal.user_id,
        action=action,
        target_type="user",
        target_id=str(target_user.id),
        request=request,
        details=details,
    )


@router.get("/users", response_model=list[OrganizationMemberRead])
def list_members(
    principal: Principal = Depends(manager_dependency),
    session: Session = Depends(get_session),
) -> list[OrganizationMemberRead]:
    rows = session.exec(
        select(User, OrganizationMembership)
        .join(
            OrganizationMembership,
            OrganizationMembership.user_id == User.id,
        )
        .where(
            OrganizationMembership.organization_id == principal.organization_id
        )
        .order_by(User.created_at.desc())
    ).all()
    return [build_member_read(user, membership) for user, membership in rows]


@router.post(
    "/users",
    response_model=OrganizationMemberRead,
    status_code=status.HTTP_201_CREATED,
)
def create_member(
    payload: MemberCreateRequest,
    request: Request,
    principal: Principal = Depends(manager_dependency),
    session: Session = Depends(get_session),
) -> OrganizationMemberRead:
    ensure_owner_operation_allowed(principal, requested_role=payload.role)
    existing_user = session.exec(
        select(User).where(User.email == payload.email)
    ).first()
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.initial_password),
    )
    session.add(user)
    session.flush()
    membership = OrganizationMembership(
        organization_id=principal.organization_id,
        user_id=user.id,
        role=payload.role,
    )
    session.add(membership)
    session.flush()
    add_admin_audit(
        session,
        principal=principal,
        request=request,
        action="admin.user.create",
        target_user=user,
        details={"role": payload.role},
    )
    session.commit()
    session.refresh(user)
    session.refresh(membership)
    return build_member_read(user, membership)


@router.patch("/users/{user_id}/role", response_model=OrganizationMemberRead)
def update_member_role(
    user_id: int,
    payload: MemberRoleUpdateRequest,
    request: Request,
    principal: Principal = Depends(manager_dependency),
    session: Session = Depends(get_session),
) -> OrganizationMemberRead:
    user, membership = get_member_or_404(
        session,
        organization_id=principal.organization_id,
        user_id=user_id,
    )
    ensure_owner_operation_allowed(
        principal,
        target_membership=membership,
        requested_role=payload.role,
    )
    if membership.role == ROLE_OWNER and payload.role != ROLE_OWNER:
        ensure_not_last_active_owner(
            session,
            organization_id=principal.organization_id,
            target_user=user,
            target_membership=membership,
        )

    previous_role = membership.role
    membership.role = payload.role
    user.token_version += 1
    user.updated_at = datetime.utcnow()
    session.add(membership)
    session.add(user)
    add_admin_audit(
        session,
        principal=principal,
        request=request,
        action="admin.user.role.update",
        target_user=user,
        details={"previous_role": previous_role, "role": payload.role},
    )
    session.commit()
    session.refresh(user)
    session.refresh(membership)
    return build_member_read(user, membership)


@router.patch("/users/{user_id}/status", response_model=OrganizationMemberRead)
def update_member_status(
    user_id: int,
    payload: MemberStatusUpdateRequest,
    request: Request,
    principal: Principal = Depends(manager_dependency),
    session: Session = Depends(get_session),
) -> OrganizationMemberRead:
    user, membership = get_member_or_404(
        session,
        organization_id=principal.organization_id,
        user_id=user_id,
    )
    ensure_owner_operation_allowed(principal, target_membership=membership)
    if user.id == principal.user_id and not payload.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You cannot disable your own account",
        )
    if not payload.is_active:
        ensure_not_last_active_owner(
            session,
            organization_id=principal.organization_id,
            target_user=user,
            target_membership=membership,
        )

    previous_status = user.is_active
    user.is_active = payload.is_active
    if previous_status != payload.is_active:
        user.token_version += 1
    user.updated_at = datetime.utcnow()
    session.add(user)
    add_admin_audit(
        session,
        principal=principal,
        request=request,
        action="admin.user.status.update",
        target_user=user,
        details={"is_active": payload.is_active},
    )
    session.commit()
    session.refresh(user)
    return build_member_read(user, membership)


@router.post(
    "/users/{user_id}/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
)
def reset_member_password(
    user_id: int,
    payload: PasswordResetRequest,
    request: Request,
    principal: Principal = Depends(manager_dependency),
    session: Session = Depends(get_session),
) -> None:
    user, membership = get_member_or_404(
        session,
        organization_id=principal.organization_id,
        user_id=user_id,
    )
    ensure_owner_operation_allowed(principal, target_membership=membership)
    user.password_hash = hash_password(payload.new_password)
    user.token_version += 1
    user.updated_at = datetime.utcnow()
    session.add(user)
    add_admin_audit(
        session,
        principal=principal,
        request=request,
        action="admin.user.password.reset",
        target_user=user,
    )
    session.commit()


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    user_id: int,
    request: Request,
    principal: Principal = Depends(manager_dependency),
    session: Session = Depends(get_session),
) -> None:
    user, membership = get_member_or_404(
        session,
        organization_id=principal.organization_id,
        user_id=user_id,
    )
    ensure_owner_operation_allowed(principal, target_membership=membership)
    if user.id == principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You cannot remove your own membership",
        )
    ensure_not_last_active_owner(
        session,
        organization_id=principal.organization_id,
        target_user=user,
        target_membership=membership,
    )

    membership_count = session.exec(
        select(func.count())
        .select_from(OrganizationMembership)
        .where(OrganizationMembership.user_id == user.id)
    ).one()
    session.delete(membership)
    if membership_count <= 1:
        user.is_active = False
        user.token_version += 1
        user.updated_at = datetime.utcnow()
        session.add(user)
    add_admin_audit(
        session,
        principal=principal,
        request=request,
        action="admin.user.membership.remove",
        target_user=user,
    )
    session.commit()


@router.get("/audit-logs", response_model=SecurityAuditLogListResponse)
def list_security_audit_logs(
    principal: Principal = Depends(manager_dependency),
    session: Session = Depends(get_session),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> SecurityAuditLogListResponse:
    filters = SecurityAuditLog.organization_id == principal.organization_id
    total = session.exec(
        select(func.count())
        .select_from(SecurityAuditLog)
        .where(filters)
    ).one()
    records = session.exec(
        select(SecurityAuditLog)
        .where(filters)
        .order_by(SecurityAuditLog.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    actor_ids = {
        record.actor_user_id
        for record in records
        if record.actor_user_id is not None
    }
    actors = {}
    if actor_ids:
        actors = {
            user.id: user.email
            for user in session.exec(
                select(User).where(User.id.in_(actor_ids))
            ).all()
        }

    items = []
    for record in records:
        try:
            details = json.loads(record.details_json or "{}")
        except json.JSONDecodeError:
            details = {}
        items.append(
            SecurityAuditLogRead(
                id=record.id,
                organization_id=record.organization_id,
                actor_user_id=record.actor_user_id,
                actor_email=actors.get(record.actor_user_id, ""),
                action=record.action,
                outcome=record.outcome,
                target_type=record.target_type,
                target_id=record.target_id,
                ip_address=record.ip_address,
                details=details,
                created_at=record.created_at,
            )
        )
    return SecurityAuditLogListResponse(
        items=items,
        total=total,
        offset=offset,
        limit=limit,
    )
