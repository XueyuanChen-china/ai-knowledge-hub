"""U4 跨组织授权测试的共享夹具。"""

import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, select

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.database import get_session
from app.db.models import KnowledgeBase, Organization, OrganizationMembership, User
from app.main import app
from app.security.dependencies import Principal, get_current_principal
from app.security.passwords import hash_password
try:
    from tests.postgres_test_utils import PostgresTestDatabase
except ModuleNotFoundError:
    from postgres_test_utils import PostgresTestDatabase


def create_test_identity(
    session: Session,
    *,
    email: str = "owner@example.com",
    role: str = "owner",
) -> Principal:
    """在已迁移的临时库中创建一组最小可用的组织身份。

    业务资源从 U4 起必须有组织和创建人，这个辅助函数让旧业务测试也按真实
    请求上下文准备数据，而不是通过 nullable 字段绕过授权模型。
    """

    organization = session.exec(
        select(Organization).where(Organization.slug == "default")
    ).one()
    user = User(email=email, password_hash=hash_password("test-password"))
    session.add(user)
    session.commit()
    session.refresh(user)
    session.add(
        OrganizationMembership(
            organization_id=organization.id,
            user_id=user.id,
            role=role,
        )
    )
    session.commit()
    return Principal(
        user_id=user.id,
        organization_id=organization.id,
        role=role,
        email=user.email,
        token_id="test-token",
    )


class ResourceAuthorizationTestCase:
    """为每个用例准备两个组织、两个用户和各自知识库。"""

    def setUp_resource_authorization(self) -> None:
        self.database = PostgresTestDatabase()
        self.engine = self.database.create_engine()

        with Session(self.engine) as session:
            organization_a = session.exec(
                select(Organization).where(Organization.slug == "default")
            ).one()
            organization_b = Organization(name="Organization B", slug="organization-b")
            session.add(organization_b)
            session.commit()
            session.refresh(organization_b)

            user_a = User(email="user-a@example.com", password_hash=hash_password("password-a"))
            user_b = User(email="user-b@example.com", password_hash=hash_password("password-b"))
            session.add(user_a)
            session.add(user_b)
            session.commit()
            session.refresh(user_a)
            session.refresh(user_b)
            session.add(
                OrganizationMembership(
                    organization_id=organization_a.id,
                    user_id=user_a.id,
                    role="owner",
                )
            )
            session.add(
                OrganizationMembership(
                    organization_id=organization_b.id,
                    user_id=user_b.id,
                    role="viewer",
                )
            )
            session.commit()

            knowledge_base_a = KnowledgeBase(
                organization_id=organization_a.id,
                created_by_user_id=user_a.id,
                name="Knowledge Base A",
            )
            knowledge_base_b = KnowledgeBase(
                organization_id=organization_b.id,
                created_by_user_id=user_b.id,
                name="Knowledge Base B",
            )
            session.add(knowledge_base_a)
            session.add(knowledge_base_b)
            session.commit()
            session.refresh(knowledge_base_a)
            session.refresh(knowledge_base_b)

            self.organization_a_id = organization_a.id
            self.organization_b_id = organization_b.id
            self.user_a_id = user_a.id
            self.user_b_id = user_b.id
            self.knowledge_base_a_id = knowledge_base_a.id
            self.knowledge_base_b_id = knowledge_base_b.id

        self.current_principal = Principal(
            user_id=self.user_a_id,
            organization_id=self.organization_a_id,
            role="owner",
            email="user-a@example.com",
            token_id="test-token-a",
        )

        def override_get_session():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = override_get_session
        app.dependency_overrides[get_current_principal] = lambda: self.current_principal
        self.client = TestClient(app)

    def use_organization_a(self) -> None:
        self.current_principal = Principal(
            user_id=self.user_a_id,
            organization_id=self.organization_a_id,
            role="owner",
            email="user-a@example.com",
            token_id="test-token-a",
        )

    def use_organization_b(self, role: str = "viewer") -> None:
        self.current_principal = Principal(
            user_id=self.user_b_id,
            organization_id=self.organization_b_id,
            role=role,
            email="user-b@example.com",
            token_id="test-token-b",
        )

    def tearDown_resource_authorization(self) -> None:
        app.dependency_overrides.clear()
        self.database.dispose()
