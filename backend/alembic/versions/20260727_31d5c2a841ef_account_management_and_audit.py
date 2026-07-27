"""add account lifecycle and security audit

Revision ID: 31d5c2a841ef
Revises: b6c4e91f2a77
"""

from alembic import op
import sqlalchemy as sa

revision = "31d5c2a841ef"
down_revision = "b6c4e91f2a77"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "token_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.alter_column("users", "token_version", server_default=None)

    op.create_table(
        "security_audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("target_type", sa.String(length=50), nullable=False),
        sa.Column("target_id", sa.String(length=100), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=500), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_security_audit_logs_organization_id",
        "security_audit_logs",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_security_audit_logs_actor_user_id",
        "security_audit_logs",
        ["actor_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_security_audit_logs_action",
        "security_audit_logs",
        ["action"],
        unique=False,
    )
    op.create_index(
        "ix_security_audit_logs_outcome",
        "security_audit_logs",
        ["outcome"],
        unique=False,
    )
    op.create_index(
        "ix_security_audit_logs_target_type",
        "security_audit_logs",
        ["target_type"],
        unique=False,
    )
    op.create_index(
        "ix_security_audit_logs_created_at",
        "security_audit_logs",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_security_audit_logs_created_at",
        table_name="security_audit_logs",
    )
    op.drop_index(
        "ix_security_audit_logs_target_type",
        table_name="security_audit_logs",
    )
    op.drop_index(
        "ix_security_audit_logs_outcome",
        table_name="security_audit_logs",
    )
    op.drop_index(
        "ix_security_audit_logs_action",
        table_name="security_audit_logs",
    )
    op.drop_index(
        "ix_security_audit_logs_actor_user_id",
        table_name="security_audit_logs",
    )
    op.drop_index(
        "ix_security_audit_logs_organization_id",
        table_name="security_audit_logs",
    )
    op.drop_table("security_audit_logs")
    op.drop_column("users", "token_version")
