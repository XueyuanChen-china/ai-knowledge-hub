"""add conversation-level persistent memories

Revision ID: c1d7a4e9b2f0
Revises: 8b1c4e7d9a21
"""

from alembic import op
import sqlalchemy as sa


revision = "c1d7a4e9b2f0"
down_revision = "8b1c4e7d9a21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_memories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("memory_type", sa.String(length=50), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_message_id", sa.Integer(), nullable=True),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["source_message_id"], ["messages.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversation_memories_organization_id",
        "conversation_memories",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_memories_conversation_id",
        "conversation_memories",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_memories_user_id",
        "conversation_memories",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_memories_memory_type",
        "conversation_memories",
        ["memory_type"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_memories_source_message_id",
        "conversation_memories",
        ["source_message_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_memories_importance",
        "conversation_memories",
        ["importance"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_memories_status",
        "conversation_memories",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    for index_name in (
        "ix_conversation_memories_status",
        "ix_conversation_memories_importance",
        "ix_conversation_memories_source_message_id",
        "ix_conversation_memories_memory_type",
        "ix_conversation_memories_user_id",
        "ix_conversation_memories_conversation_id",
        "ix_conversation_memories_organization_id",
    ):
        op.drop_index(index_name, table_name="conversation_memories")
    op.drop_table("conversation_memories")
