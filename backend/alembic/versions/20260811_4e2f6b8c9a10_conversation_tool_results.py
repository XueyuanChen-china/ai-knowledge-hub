"""persist tool results outside the LLM context

Revision ID: 4e2f6b8c9a10
Revises: c1d7a4e9b2f0
"""

from alembic import op
import sqlalchemy as sa


revision = "4e2f6b8c9a10"
down_revision = "c1d7a4e9b2f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_tool_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.String(length=255), nullable=False),
        sa.Column("result_ref", sa.String(length=100), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source_ids_json", sa.Text(), nullable=False),
        sa.Column("full_result_json", sa.Text(), nullable=False),
        sa.Column("used_in_answer", sa.Boolean(), nullable=False),
        sa.Column("citation_used", sa.Boolean(), nullable=False),
        sa.Column("importance", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("result_ref"),
    )
    for name, column in (
        ("organization_id", "organization_id"),
        ("conversation_id", "conversation_id"),
        ("thread_id", "thread_id"),
        ("result_ref", "result_ref"),
        ("tool_name", "tool_name"),
        ("used_in_answer", "used_in_answer"),
        ("citation_used", "citation_used"),
        ("importance", "importance"),
        ("created_at", "created_at"),
        ("expires_at", "expires_at"),
    ):
        op.create_index(
            f"ix_conversation_tool_results_{name}",
            "conversation_tool_results",
            [column],
            unique=False,
        )


def downgrade() -> None:
    for name in (
        "expires_at",
        "created_at",
        "importance",
        "citation_used",
        "used_in_answer",
        "tool_name",
        "result_ref",
        "thread_id",
        "conversation_id",
        "organization_id",
    ):
        op.drop_index(f"ix_conversation_tool_results_{name}", table_name="conversation_tool_results")
    op.drop_table("conversation_tool_results")
