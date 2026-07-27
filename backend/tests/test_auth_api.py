import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
for path in (BACKEND_DIR, TESTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.config import Settings
from app.db.database import get_session
from app.db.models import Organization, OrganizationMembership, User
from app.main import app
from app.security.passwords import hash_password
from app.security.tokens import JWT_ALGORITHM
from postgres_test_utils import PostgresTestDatabase


class AuthApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = PostgresTestDatabase()
        self.engine = self.database.create_engine()
        self.settings = Settings(
            database_url=self.database.database_url,
            auth_jwt_secret="test-secret-that-is-long-enough",
            auth_jwt_issuer="test-issuer",
            auth_jwt_audience="test-audience",
            auth_access_token_ttl_seconds=900,
            auth_login_rate_limit_max_attempts=2,
            auth_login_rate_limit_window_seconds=60,
        )

        with Session(self.engine) as session:
            organization = session.exec(
                select(Organization).where(Organization.slug == "default")
            ).one()
            user = User(
                email="admin@example.com",
                password_hash=hash_password("correct-password"),
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            session.add(
                OrganizationMembership(
                    organization_id=organization.id,
                    user_id=user.id,
                    role="owner",
                )
            )
            session.commit()

        def override_get_session():
            with Session(self.engine) as session:
                yield session

        from app.config import get_settings

        app.dependency_overrides[get_session] = override_get_session
        app.dependency_overrides[get_settings] = lambda: self.settings
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.database.dispose()

    def test_login_and_me_return_identity(self) -> None:
        response = self.client.post(
            "/api/auth/login",
            json={"email": "ADMIN@example.com", "password": "correct-password"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["user"]["email"], "admin@example.com")
        self.assertEqual(payload["organization"]["slug"], "default")
        self.assertEqual(payload["role"], "owner")

        with patch("app.security.dependencies.is_token_revoked", return_value=False):
            me = self.client.get(
                "/api/auth/me",
                headers={"Authorization": f"Bearer {payload['access_token']}"},
            )
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["user"]["id"], payload["user"]["id"])

    def test_missing_expired_and_invalid_claim_tokens_return_401(self) -> None:
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.assertEqual(
            self.client.get(
                "/api/auth/me",
                headers={"Authorization": "Bearer not-a-jwt"},
            ).status_code,
            401,
        )

        base_claims = {
            "sub": "1",
            "org_id": 1,
            "role": "owner",
            "iat": datetime.now(timezone.utc) - timedelta(minutes=5),
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
            "iss": "test-issuer",
            "aud": "test-audience",
            "jti": "expired-token",
            "ver": 0,
        }
        expired = jwt.encode(
            base_claims,
            self.settings.auth_jwt_secret,
            algorithm=JWT_ALGORITHM,
        )
        with patch("app.security.dependencies.is_token_revoked", return_value=False):
            self.assertEqual(
                self.client.get(
                    "/api/auth/me",
                    headers={"Authorization": f"Bearer {expired}"},
                ).status_code,
                401,
            )

        base_claims["exp"] = datetime.now(timezone.utc) + timedelta(minutes=5)
        base_claims["iss"] = "wrong-issuer"
        invalid_issuer = jwt.encode(
            base_claims,
            self.settings.auth_jwt_secret,
            algorithm=JWT_ALGORITHM,
        )
        with patch("app.security.dependencies.is_token_revoked", return_value=False):
            self.assertEqual(
                self.client.get(
                    "/api/auth/me",
                    headers={"Authorization": f"Bearer {invalid_issuer}"},
                ).status_code,
                401,
            )

    def test_logout_revokes_current_token(self) -> None:
        login_response = self.client.post(
            "/api/auth/login",
            json={"email": "admin@example.com", "password": "correct-password"},
        )
        token = login_response.json()["access_token"]
        with patch("app.api.auth.revoke_token", return_value=600) as revoke:
            response = self.client.post(
                "/api/auth/logout",
                headers={"Authorization": f"Bearer {token}"},
            )
        self.assertEqual(response.status_code, 204)
        revoke.assert_called_once()
        self.assertEqual(revoke.call_args.kwargs["settings"], self.settings)

    def test_revoked_token_is_rejected(self) -> None:
        login_response = self.client.post(
            "/api/auth/login",
            json={"email": "admin@example.com", "password": "correct-password"},
        )
        token = login_response.json()["access_token"]
        with patch("app.security.dependencies.is_token_revoked", return_value=True):
            response = self.client.get(
                "/api/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
        self.assertEqual(response.status_code, 401)

    def test_wrong_credentials_are_generic_and_rate_limited(self) -> None:
        first = self.client.post(
            "/api/auth/login",
            json={"email": "missing@example.com", "password": "wrong-password"},
        )
        second = self.client.post(
            "/api/auth/login",
            json={"email": "missing@example.com", "password": "wrong-password"},
        )
        third = self.client.post(
            "/api/auth/login",
            json={"email": "missing@example.com", "password": "wrong-password"},
        )
        self.assertEqual(first.status_code, 401)
        self.assertEqual(second.status_code, 401)
        self.assertEqual(third.status_code, 429)
        self.assertEqual(first.json()["detail"], "Invalid email or password")
        self.assertEqual(second.json()["detail"], first.json()["detail"])
        self.assertIn("Retry-After", third.headers)


if __name__ == "__main__":
    unittest.main()
