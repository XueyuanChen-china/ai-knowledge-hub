#!/usr/bin/env python
"""显式创建开发管理员。

密码只从 SEED_ADMIN_PASSWORD 环境变量读取，生产启动不会自动执行这个脚本。
"""

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlmodel import Session, select

from app.config import get_settings
from app.db.database import check_database_ready, engine
from app.db.models import Organization, OrganizationMembership, User
from app.security.passwords import hash_password


def main() -> None:
    settings = get_settings()
    check_database_ready()
    email = os.getenv("SEED_ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("SEED_ADMIN_PASSWORD", "")
    if not email or not password:
        raise SystemExit("SEED_ADMIN_EMAIL and SEED_ADMIN_PASSWORD are required")

    with Session(engine) as session:
        organization = session.exec(
            select(Organization).where(
                Organization.slug == settings.auth_default_organization_slug
            )
        ).first()
        if organization is None:
            raise SystemExit("Default organization is missing; run migrations first")

        user = session.exec(select(User).where(User.email == email)).first()
        if user is None:
            user = User(email=email, password_hash=hash_password(password))
            session.add(user)
            session.commit()
            session.refresh(user)

        membership = session.exec(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization.id,
                OrganizationMembership.user_id == user.id,
            )
        ).first()
        if membership is None:
            session.add(
                OrganizationMembership(
                    organization_id=organization.id,
                    user_id=user.id,
                    role="owner",
                )
            )
        else:
            membership.role = "owner"
            session.add(membership)
        session.commit()
    print(f"Admin seed ready: {email}")


if __name__ == "__main__":
    main()
