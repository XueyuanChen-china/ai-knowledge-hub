"""JWT 撤销黑名单。

Redis 只保存 token 的 jti，不保存 access token 正文。
黑名单记录的 TTL 与 token 剩余有效期一致，token 自然过期后记录自动清理。
"""

from datetime import datetime, timezone
from functools import lru_cache

import redis

from app.config import Settings


class TokenRevocationUnavailable(RuntimeError):
    """Redis 不可用时抛出，鉴权链路应按 fail-closed 处理。"""


@lru_cache(maxsize=16)
def _redis_client_for_config(
    redis_url: str,
    socket_timeout: float,
) -> redis.Redis:
    return redis.Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=socket_timeout,
        socket_timeout=socket_timeout,
    )


def _redis_client(settings: Settings) -> redis.Redis:
    return _redis_client_for_config(
        settings.auth_redis_url,
        settings.auth_redis_socket_timeout_seconds,
    )


def _blacklist_key(token_id: str, settings: Settings) -> str:
    return f"{settings.auth_token_blacklist_prefix}{token_id}"


def is_token_revoked(token_id: str, settings: Settings) -> bool:
    """查询 jti 是否已经被撤销。

    Redis 故障时不能放行请求，否则已退出的 token 可能继续使用，因此直接失败。
    """

    try:
        return bool(_redis_client(settings).exists(_blacklist_key(token_id, settings)))
    except (redis.RedisError, OSError) as exc:
        raise TokenRevocationUnavailable(
            "Token revocation store is unavailable"
        ) from exc


def revoke_token(
    *,
    token_id: str,
    expires_at: int,
    settings: Settings,
) -> int:
    """把 jti 写入 Redis，并设置到 token 过期时刻的 TTL。"""

    now = int(datetime.now(timezone.utc).timestamp())
    ttl_seconds = expires_at - now
    if ttl_seconds <= 0:
        return 0

    try:
        _redis_client(settings).set(
            _blacklist_key(token_id, settings),
            "1",
            ex=ttl_seconds,
        )
    except (redis.RedisError, OSError) as exc:
        raise TokenRevocationUnavailable(
            "Token revocation store is unavailable"
        ) from exc
    return ttl_seconds
