"""align existing development schemas after the baseline

Revision ID: 7a3d2e1f4c88
Revises: c544b5601674
"""

from alembic import op
import sqlalchemy as sa

revision = "7a3d2e1f4c88"
down_revision = "c544b5601674"
branch_labels = None
depends_on = None


INDEXES = (
    ("ix_conversations_is_pinned", "conversations", ["is_pinned"]),
    ("ix_upload_tasks_document_id", "upload_tasks", ["document_id"]),
    ("ix_upload_tasks_processing_status", "upload_tasks", ["processing_status"]),
    ("ix_upload_processing_jobs_celery_task_id", "upload_processing_jobs", ["celery_task_id"]),
    ("ix_upload_processing_jobs_claim_token", "upload_processing_jobs", ["claim_token"]),
    ("ix_upload_processing_jobs_depends_on_job_id", "upload_processing_jobs", ["depends_on_job_id"]),
    ("ix_upload_processing_jobs_lease_expires_at", "upload_processing_jobs", ["lease_expires_at"]),
    ("ix_upload_processing_jobs_locked_by", "upload_processing_jobs", ["locked_by"]),
    ("ix_upload_processing_jobs_next_run_at", "upload_processing_jobs", ["next_run_at"]),
    ("ix_upload_processing_jobs_stage", "upload_processing_jobs", ["stage"]),
)


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(item["name"] == index_name for item in inspector.get_indexes(table_name))


def _has_foreign_key(
    inspector: sa.Inspector,
    table_name: str,
    constrained_column: str,
    referred_table: str,
) -> bool:
    return any(
        item.get("referred_table") == referred_table
        and item.get("constrained_columns") == [constrained_column]
        for item in inspector.get_foreign_keys(table_name)
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for index_name, table_name, columns in INDEXES:
        if not _has_index(inspector, table_name, index_name):
            op.create_index(index_name, table_name, columns, unique=False)

    if not _has_foreign_key(
        inspector,
        "upload_tasks",
        "document_id",
        "documents",
    ):
        op.create_foreign_key(
            "upload_tasks_document_id_fkey",
            "upload_tasks",
            "documents",
            ["document_id"],
            ["id"],
        )

    if not _has_foreign_key(
        inspector,
        "upload_processing_jobs",
        "depends_on_job_id",
        "upload_processing_jobs",
    ):
        op.create_foreign_key(
            "upload_processing_jobs_depends_on_job_id_fkey",
            "upload_processing_jobs",
            "upload_processing_jobs",
            ["depends_on_job_id"],
            ["id"],
        )


def downgrade() -> None:
    # 这是对已有 baseline 的兼容补齐。故意不删除对象，避免 downgrade
    # 后再由 baseline downgrade 重复删除同一索引或外键。
    pass
