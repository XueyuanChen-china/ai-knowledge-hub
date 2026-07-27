import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlmodel import Session, select

BACKEND_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
for path in (BACKEND_DIR, TESTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.config import Settings, get_settings
from app.db.database import get_session
from app.db.models import Organization, OrganizationMembership, User
from app.main import app
from app.security.passwords import hash_password
from postgres_test_utils import PostgresTestDatabase


class AccountManagementApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = PostgresTestDatabase()
        self.engine = self.database.create_engine()
        self.settings = Settings(
            database_url=self.database.database_url,
            auth_jwt_secret="test-secret-that-is-long-enough",
            auth_jwt_issuer="test-issuer",
            auth_jwt_audience="test-audience",
            auth_access_token_ttl_seconds=900,
        )

        with Session(self.engine) as session:
            organization = session.exec(
                select(Organization).where(Organization.slug == "default")
            ).one()
            self.organization_id = organization.id
            self.owner_id = self._create_member(
                session,
                organization.id,
                email="owner@example.com",
                password="owner-password",
                role="owner",
            )
            self.viewer_id = self._create_member(
                session,
                organization.id,
                email="viewer@example.com",
                password="viewer-password",
                role="viewer",
            )

        def override_get_session():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = override_get_session
        app.dependency_overrides[get_settings] = lambda: self.settings
        self.revocation_patcher = patch(
            "app.security.dependencies.is_token_revoked",
            return_value=False,
        )
        self.revocation_patcher.start()
        self.client = TestClient(app)
        self.owner_token = self._login("owner@example.com", "owner-password")
        self.viewer_token = self._login("viewer@example.com", "viewer-password")

    def tearDown(self) -> None:
        self.revocation_patcher.stop()
        app.dependency_overrides.clear()
        self.database.dispose()

    @staticmethod
    def _create_member(
        session: Session,
        organization_id: int,
        *,
        email: str,
        password: str,
        role: str,
    ) -> int:
        user = User(
            email=email,
            password_hash=hash_password(password),
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        session.add(
            OrganizationMembership(
                organization_id=organization_id,
                user_id=user.id,
                role=role,
            )
        )
        session.commit()
        return user.id

    def _login(self, email: str, password: str) -> str:
        response = self.client.post(
            "/api/auth/login",
            json={"email": email, "password": password},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["access_token"]

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_owner_can_create_list_and_manage_member(self) -> None:
        created = self.client.post(
            "/api/admin/users",
            headers=self._headers(self.owner_token),
            json={
                "email": "editor@example.com",
                "initial_password": "editor-password",
                "role": "editor",
            },
        )
        self.assertEqual(created.status_code, 201)
        member_id = created.json()["user"]["id"]

        listed = self.client.get(
            "/api/admin/users",
            headers=self._headers(self.owner_token),
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 3)

        role_updated = self.client.patch(
            f"/api/admin/users/{member_id}/role",
            headers=self._headers(self.owner_token),
            json={"role": "admin"},
        )
        self.assertEqual(role_updated.status_code, 200)
        self.assertEqual(role_updated.json()["role"], "admin")

        status_updated = self.client.patch(
            f"/api/admin/users/{member_id}/status",
            headers=self._headers(self.owner_token),
            json={"is_active": False},
        )
        self.assertEqual(status_updated.status_code, 200)
        self.assertFalse(status_updated.json()["user"]["is_active"])

        reset = self.client.post(
            f"/api/admin/users/{member_id}/reset-password",
            headers=self._headers(self.owner_token),
            json={"new_password": "reset-password"},
        )
        self.assertEqual(reset.status_code, 204)

        removed = self.client.delete(
            f"/api/admin/users/{member_id}",
            headers=self._headers(self.owner_token),
        )
        self.assertEqual(removed.status_code, 204)

    def test_viewer_cannot_manage_members(self) -> None:
        response = self.client.get(
            "/api/admin/users",
            headers=self._headers(self.viewer_token),
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_cannot_change_an_owner_membership(self) -> None:
        with Session(self.engine) as session:
            admin_id = self._create_member(
                session,
                self.organization_id,
                email="admin@example.com",
                password="admin-password",
                role="admin",
            )
        admin_token = self._login("admin@example.com", "admin-password")

        role_updated = self.client.patch(
            f"/api/admin/users/{self.owner_id}/role",
            headers=self._headers(admin_token),
            json={"role": "viewer"},
        )
        disabled = self.client.patch(
            f"/api/admin/users/{self.owner_id}/status",
            headers=self._headers(admin_token),
            json={"is_active": False},
        )
        self.assertEqual(role_updated.status_code, 403)
        self.assertEqual(disabled.status_code, 403)
        self.assertGreater(admin_id, 0)

    def test_last_owner_cannot_be_disabled_or_removed(self) -> None:
        disabled = self.client.patch(
            f"/api/admin/users/{self.owner_id}/status",
            headers=self._headers(self.owner_token),
            json={"is_active": False},
        )
        removed = self.client.delete(
            f"/api/admin/users/{self.owner_id}",
            headers=self._headers(self.owner_token),
        )
        self.assertEqual(disabled.status_code, 409)
        self.assertEqual(removed.status_code, 409)

    def test_change_password_and_logout_all_invalidate_old_tokens(self) -> None:
        changed = self.client.post(
            "/api/account/change-password",
            headers=self._headers(self.viewer_token),
            json={
                "current_password": "viewer-password",
                "new_password": "viewer-new-password",
            },
        )
        self.assertEqual(changed.status_code, 204)
        self.assertEqual(
            self.client.get(
                "/api/auth/me",
                headers=self._headers(self.viewer_token),
            ).status_code,
            401,
        )

        new_token = self._login("viewer@example.com", "viewer-new-password")
        logout_all = self.client.post(
            "/api/account/logout-all",
            headers=self._headers(new_token),
        )
        self.assertEqual(logout_all.status_code, 204)
        self.assertEqual(
            self.client.get(
                "/api/auth/me",
                headers=self._headers(new_token),
            ).status_code,
            401,
        )

    def test_owner_can_read_security_audit_log(self) -> None:
        response = self.client.get(
            "/api/admin/audit-logs",
            headers=self._headers(self.owner_token),
        )
        self.assertEqual(response.status_code, 200)
        actions = {item["action"] for item in response.json()["items"]}
        self.assertIn("auth.login.success", actions)


if __name__ == "__main__":
    unittest.main()
