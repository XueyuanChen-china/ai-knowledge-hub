#!/usr/bin/env python3
"""创建可重复使用的本地演示组织、四种角色和知识库。

这是显式 demo seed，不会在应用启动时自动执行。脚本只写 PostgreSQL，
不创建 OSS 对象，也不调用 LLM；文档索引由 U10 的真实 E2E 脚本负责。
"""

from __future__ import annotations

import argparse
import secrets
import string
import sys
from pathlib import Path

from sqlmodel import Session, select

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings
from app.db.database import check_database_ready, engine
from app.db.models import (
    KnowledgeBase,
    KnowledgeItem,
    Organization,
    OrganizationMembership,
    User,
)
from app.security.passwords import hash_password


ROLES = ("owner", "admin", "editor", "viewer")


def generate_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits + "-_!"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def upsert_user(session: Session, email: str, password: str) -> User:
    user = session.exec(select(User).where(User.email == email)).first()
    if user is None:
        user = User(email=email, password_hash=hash_password(password))
        session.add(user)
        session.commit()
        session.refresh(user)
    return user


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-slug", default="u10-demo")
    parser.add_argument("--organization-name", default="U10 Demo Organization")
    parser.add_argument("--knowledge-base-name", default="U10 企业验收知识库")
    parser.add_argument("--email-domain", default="u10-demo.invalid")
    parser.add_argument(
        "--password",
        default="",
        help="可选：为四个 demo 账号统一设置密码；不传则为每个账号随机生成一次",
    )
    args = parser.parse_args()

    check_database_ready()
    passwords: dict[str, str] = {}
    organization_id: int
    organization_slug: str
    knowledge_base_id: int
    knowledge_base_name: str
    with Session(engine) as session:
        organization = session.exec(
            select(Organization).where(Organization.slug == args.organization_slug)
        ).first()
        if organization is None:
            organization = Organization(
                name=args.organization_name,
                slug=args.organization_slug,
            )
            session.add(organization)
            session.commit()
            session.refresh(organization)

        users: dict[str, User] = {}
        for role in ROLES:
            email = f"{role}@{args.email_domain}"
            password = args.password or generate_password()
            user = upsert_user(session, email, password)
            users[role] = user
            passwords[role] = password
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
                        role=role,
                    )
                )
            else:
                membership.role = role
                session.add(membership)

        session.commit()
        knowledge_base = session.exec(
            select(KnowledgeBase).where(
                KnowledgeBase.organization_id == organization.id,
                KnowledgeBase.name == args.knowledge_base_name,
            )
        ).first()
        if knowledge_base is None:
            knowledge_base = KnowledgeBase(
                organization_id=organization.id,
                created_by_user_id=users["owner"].id,
                name=args.knowledge_base_name,
                description="U10 合成企业制度数据，仅用于可重复验收。",
            )
            session.add(knowledge_base)
            session.commit()
            session.refresh(knowledge_base)

        item = session.exec(
            select(KnowledgeItem).where(
                KnowledgeItem.knowledge_base_id == knowledge_base.id,
                KnowledgeItem.title == "U10 演示知识条目",
            )
        ).first()
        if item is None:
            session.add(
                KnowledgeItem(
                    organization_id=organization.id,
                    created_by_user_id=users["owner"].id,
                    knowledge_base_id=knowledge_base.id,
                    title="U10 演示知识条目",
                    content="这是 U10 演示环境的人工知识条目。真实五格式文档由 E2E 脚本上传。",
                    status="active",
                    source_type="manual",
                )
            )
            session.commit()

        # 在 Session 关闭前复制标量值。commit() 默认会让 ORM 属性过期，
        # 离开 Session 后再访问对象属性会触发 DetachedInstanceError。
        organization_id = int(organization.id)
        organization_slug = organization.slug
        knowledge_base_id = int(knowledge_base.id)
        knowledge_base_name = knowledge_base.name

    print(f"organization_id={organization_id} slug={organization_slug}")
    print(f"knowledge_base_id={knowledge_base_id} name={knowledge_base_name}")
    print("demo_accounts:")
    for role in ROLES:
        print(f"  {role}: {role}@{args.email_domain} / {passwords[role]}")
    print("请把上面的凭据仅用于本地演示，不要提交到 Git 或写入 CI。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
