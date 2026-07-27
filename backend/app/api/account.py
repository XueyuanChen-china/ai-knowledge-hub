"""当前用户的个人账号安全 API。"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session

from app.db.database import get_session
from app.db.models import User
from app.schemas.account import ChangePasswordRequest
from app.security.dependencies import Principal, get_current_principal
from app.security.passwords import hash_password, verify_password
from app.services.security_audit_service import add_security_audit_log

router = APIRouter(prefix="/api/account", tags=["account"])


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> None:
    user = session.get(User, principal.user_id)
    if user is None or not verify_password(
        payload.current_password,
        user.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    if verify_password(payload.new_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from current password",
        )

    user.password_hash = hash_password(payload.new_password)
    user.token_version += 1
    user.updated_at = datetime.utcnow()
    session.add(user)
    add_security_audit_log(
        session,
        organization_id=principal.organization_id,
        actor_user_id=principal.user_id,
        action="account.password.change",
        target_type="user",
        target_id=str(user.id),
        request=request,
    )
    session.commit()


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all_devices(
    request: Request,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> None:
    user = session.get(User, principal.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    user.token_version += 1
    user.updated_at = datetime.utcnow()
    session.add(user)
    add_security_audit_log(
        session,
        organization_id=principal.organization_id,
        actor_user_id=principal.user_id,
        action="account.sessions.revoke_all",
        target_type="user",
        target_id=str(user.id),
        request=request,
    )
    session.commit()
