"""add conversation context summary fields

Revision ID: 8b1c4e7d9a21
Revises: f3a8d9e45c10
"""

from alembic import op
import sqlalchemy as sa


revision = "8b1c4e7d9a21"
down_revision = "f3a8d9e45c10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("context_summary", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "conversations",
        sa.Column("context_summary_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "conversations",
        sa.Column("context_summary_through_message_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("context_summary_updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_conversations_context_summary_through_message_id",
        "conversations",
        ["context_summary_through_message_id"],
        unique=False,
    )
    op.alter_column("conversations", "context_summary", server_default=None)
    op.alter_column("conversations", "context_summary_version", server_default=None)


def downgrade() -> None:
    op.drop_index(
        "ix_conversations_context_summary_through_message_id",
        table_name="conversations",
    )
    op.drop_column("conversations", "context_summary_updated_at")
    op.drop_column("conversations", "context_summary_through_message_id")
    op.drop_column("conversations", "context_summary_version")
    op.drop_column("conversations", "context_summary")
