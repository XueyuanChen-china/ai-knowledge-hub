from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.auth import AuthUserRead

OrganizationRole = Literal["owner", "admin", "editor", "viewer"]


def normalize_email_value(value: str) -> str:
    return value.strip().lower()


class MemberCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    initial_password: str = Field(min_length=8, max_length=256)
    role: OrganizationRole = "viewer"

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return normalize_email_value(value)


class MemberRoleUpdateRequest(BaseModel):
    role: OrganizationRole


class MemberStatusUpdateRequest(BaseModel):
    is_active: bool


class PasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class OrganizationMemberRead(BaseModel):
    membership_id: int
    role: OrganizationRole
    joined_at: datetime
    user: AuthUserRead


class SecurityAuditLogRead(BaseModel):
    id: int
    organization_id: Optional[int]
    actor_user_id: Optional[int]
    actor_email: str
    action: str
    outcome: str
    target_type: str
    target_id: str
    ip_address: str
    details: dict[str, object]
    created_at: datetime


class SecurityAuditLogListResponse(BaseModel):
    items: list[SecurityAuditLogRead]
    total: int
    offset: int
    limit: int
