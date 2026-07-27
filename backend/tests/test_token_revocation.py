import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import Settings
from app.security.revocation import is_token_revoked, revoke_token


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def exists(self, key: str) -> int:
        return int(key in self.values)

    def set(self, key: str, value: str, *, ex: int) -> bool:
        self.values[key] = value
        self.ttls[key] = ex
        return True


class TokenRevocationTests(unittest.TestCase):
    def test_revoke_writes_only_jti_with_remaining_ttl(self) -> None:
        fake_redis = FakeRedis()
        settings = Settings(
            auth_redis_url="redis://test/0",
            auth_token_blacklist_prefix="blacklist:",
        )

        with patch("app.security.revocation._redis_client", return_value=fake_redis):
            ttl = revoke_token(
                token_id="token-123",
                expires_at=int(time.time()) + 120,
                settings=settings,
            )
            revoked = is_token_revoked("token-123", settings)

        self.assertTrue(revoked)
        self.assertIn("blacklist:token-123", fake_redis.values)
        self.assertGreaterEqual(ttl, 119)
        self.assertLessEqual(ttl, 120)
        self.assertEqual(fake_redis.ttls["blacklist:token-123"], ttl)

    def test_expired_token_is_not_added_to_blacklist(self) -> None:
        fake_redis = FakeRedis()
        settings = Settings(auth_redis_url="redis://test/0")

        with patch("app.security.revocation._redis_client", return_value=fake_redis):
            ttl = revoke_token(
                token_id="expired-token",
                expires_at=int(time.time()) - 1,
                settings=settings,
            )

        self.assertEqual(ttl, 0)
        self.assertEqual(fake_redis.values, {})


if __name__ == "__main__":
    unittest.main()
