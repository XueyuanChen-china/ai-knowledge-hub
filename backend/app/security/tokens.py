"""JWT access token 的签发和校验。"""

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import jwt

from app.config import Settings

JWT_ALGORITHM = "HS256"
REQUIRED_CLAIMS = ["sub", "exp", "iss", "aud", "jti"]


def _require_secret(settings: Settings) -> str:
    secret = settings.auth_jwt_secret.strip()
    if not secret:
        raise RuntimeError("AUTH_JWT_SECRET must be configured before issuing tokens")
    return secret


def create_access_token(
    *,
    user_id: int,
    organization_id: int,
    role: str,
    settings: Settings,
) -> tuple[str, int]:
    """签发短期 access token。"""

    secret = _require_secret(settings)
    expires_in = max(60, settings.auth_access_token_ttl_seconds)
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "org_id": organization_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
        "iss": settings.auth_jwt_issuer,
        "aud": settings.auth_jwt_audience,
        "jti": uuid4().hex,
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM), expires_in


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    """校验签名、算法、issuer、audience、exp 和必需 claims。"""

    secret = _require_secret(settings)
    payload = jwt.decode(
        token,
        secret,
        algorithms=[JWT_ALGORITHM],
        issuer=settings.auth_jwt_issuer,
        audience=settings.auth_jwt_audience,
        options={"require": REQUIRED_CLAIMS},
    )
    if not isinstance(payload, dict):
        raise jwt.InvalidTokenError("JWT payload must be an object")
    return payload
