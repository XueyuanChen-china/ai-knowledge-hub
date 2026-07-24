from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class AuthUserRead(BaseModel):
    id: int
    email: str
    is_active: bool
    created_at: datetime


class OrganizationRead(BaseModel):
    id: int
    name: str
    slug: str


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: AuthUserRead
    organization: OrganizationRead
    role: str


class MeResponse(BaseModel):
    user: AuthUserRead
    organization: OrganizationRead
    role: str
