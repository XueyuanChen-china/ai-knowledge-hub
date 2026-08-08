"""add organization ownership to business resources

Revision ID: f3a8d9e45c10
Revises: 31d5c2a841ef
"""

from alembic import op
import sqlalchemy as sa
from typing import Optional, Tuple


revision = "f3a8d9e45c10"
down_revision = "31d5c2a841ef"
branch_labels = None
depends_on = None

RESOURCE_TABLES = (
    "knowledge_bases",
    "documents",
    "knowledge_items",
    "chunks",
    "conversations",
    "upload_tasks",
)


def _default_organization_and_creator(connection) -> Tuple[int, Optional[int]]:
    organization_id = connection.execute(
        sa.text("SELECT id FROM organizations WHERE slug = 'default' LIMIT 1")
    ).scalar_one_or_none()
    if organization_id is None:
        raise RuntimeError("The default organization is required before U4 migration")

    creator_id = connection.execute(
        sa.text(
            """
            SELECT om.user_id
            FROM organization_memberships AS om
            JOIN users AS u ON u.id = om.user_id
            WHERE om.organization_id = :organization_id
              AND u.is_active = true
            ORDER BY CASE om.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END,
                     om.id
            LIMIT 1
            """
        ),
        {"organization_id": organization_id},
    ).scalar_one_or_none()
    return int(organization_id), int(creator_id) if creator_id is not None else None


def _require_creator_for_existing_rows(connection, creator_id: Optional[int]) -> None:
    if creator_id is not None:
        return
    for table_name in RESOURCE_TABLES:
        has_rows = connection.execute(
            sa.text(f"SELECT EXISTS (SELECT 1 FROM {table_name})")
        ).scalar_one()
        if has_rows:
            raise RuntimeError(
                "Cannot backfill resource ownership without an active member in the "
                "default organization. Create an owner with scripts/seed_admin.py and retry."
            )


def upgrade() -> None:
    for table_name in RESOURCE_TABLES:
        op.add_column(table_name, sa.Column("organization_id", sa.Integer(), nullable=True))
        if table_name != "chunks":
            op.add_column(
                table_name,
                sa.Column("created_by_user_id", sa.Integer(), nullable=True),
            )

    connection = op.get_bind()
    organization_id, creator_id = _default_organization_and_creator(connection)
    _require_creator_for_existing_rows(connection, creator_id)

    if creator_id is not None:
        connection.execute(
            sa.text(
                "UPDATE knowledge_bases SET organization_id = :org, "
                "created_by_user_id = :creator WHERE organization_id IS NULL"
            ),
            {"org": organization_id, "creator": creator_id},
        )
        for table_name in ("documents", "knowledge_items", "conversations", "upload_tasks"):
            connection.execute(
                sa.text(
                    f"UPDATE {table_name} AS resource "
                    "SET organization_id = kb.organization_id, "
                    "created_by_user_id = kb.created_by_user_id "
                    "FROM knowledge_bases AS kb "
                    "WHERE resource.knowledge_base_id = kb.id "
                    "AND resource.organization_id IS NULL"
                )
            )
        connection.execute(
            sa.text(
                "UPDATE chunks AS chunk SET organization_id = kb.organization_id "
                "FROM knowledge_bases AS kb "
                "WHERE chunk.knowledge_base_id = kb.id AND chunk.organization_id IS NULL"
            )
        )

    for table_name in RESOURCE_TABLES:
        op.alter_column(table_name, "organization_id", nullable=False)
        op.create_foreign_key(
            f"fk_{table_name}_organization_id",
            table_name,
            "organizations",
            ["organization_id"],
            ["id"],
        )
        op.create_index(
            f"ix_{table_name}_organization_id",
            table_name,
            ["organization_id"],
            unique=False,
        )
        if table_name != "chunks":
            op.alter_column(table_name, "created_by_user_id", nullable=False)
            op.create_foreign_key(
                f"fk_{table_name}_created_by_user_id",
                table_name,
                "users",
                ["created_by_user_id"],
                ["id"],
            )
            op.create_index(
                f"ix_{table_name}_created_by_user_id",
                table_name,
                ["created_by_user_id"],
                unique=False,
            )


def downgrade() -> None:
    for table_name in reversed(RESOURCE_TABLES):
        if table_name != "chunks":
            op.drop_index(
                f"ix_{table_name}_created_by_user_id",
                table_name=table_name,
            )
            op.drop_constraint(
                f"fk_{table_name}_created_by_user_id",
                table_name,
                type_="foreignkey",
            )
            op.drop_column(table_name, "created_by_user_id")
        op.drop_index(f"ix_{table_name}_organization_id", table_name=table_name)
        op.drop_constraint(
            f"fk_{table_name}_organization_id",
            table_name,
            type_="foreignkey",
        )
        op.drop_column(table_name, "organization_id")
