from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

# 这里导入 models 的目的不是直接使用变量，而是让 SQLModel 注册所有表模型。
# 如果不导入，SQLModel.metadata 可能不知道有哪些表需要创建。
from app.db import models  # noqa: F401

POSTGRESQL_PREFIXES = (
    "postgresql://",
    "postgresql+psycopg://",
    "postgresql+psycopg2://",
)

settings = get_settings()


def is_postgresql_url(database_url: str) -> bool:
    """判断当前连接串是否为 PostgreSQL。"""

    return database_url.startswith(POSTGRESQL_PREFIXES)


def validate_database_url(database_url: str) -> None:
    """当前项目只接受 PostgreSQL 连接串。"""

    if is_postgresql_url(database_url):
        return

    raise RuntimeError(
        "DATABASE_URL must be a PostgreSQL URL, "
        f"got: {database_url}"
    )


def build_engine(database_url: str) -> Engine:
    """根据 PostgreSQL 连接串创建 SQLAlchemy Engine。"""

    validate_database_url(database_url)
    return create_engine(
        database_url,
        pool_pre_ping=True,
    )


# engine 是应用连接数据库的核心对象。
# 后续所有 Session 都会基于这个 engine 创建。
engine = build_engine(settings.database_url)


def create_db_and_tables() -> None:
    """在 PostgreSQL 中创建缺失的数据表。"""

    SQLModel.metadata.create_all(engine)
    ensure_document_columns(engine)
    ensure_conversation_columns(engine)
    ensure_upload_task_columns(engine)
    ensure_upload_part_columns(engine)
    ensure_upload_processing_job_columns(engine)
    ensure_chunk_columns(engine)


def ensure_chunk_columns(current_engine: Engine) -> None:
    """补齐 chunks 的阶段级 embedding 暂存字段。"""

    inspector = inspect(current_engine)
    if "chunks" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("chunks")}
    if "embedding_json" in columns:
        return

    with current_engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE chunks ADD COLUMN embedding_json TEXT NOT NULL DEFAULT ''")
        )


def ensure_document_columns(current_engine: Engine) -> None:
    """补齐开发期遗留的 documents 字段。

    create_all 只会创建不存在的表，不会自动给已有表添加新字段。
    这里继续保留一次兼容补列逻辑，避免旧 PostgreSQL 库缺字段时直接启动失败。
    """

    inspector = inspect(current_engine)
    if "documents" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("documents")}
    if "extracted_text" in columns:
        return

    with current_engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE documents ADD COLUMN extracted_text TEXT DEFAULT ''")
        )


def ensure_conversation_columns(current_engine: Engine) -> None:
    """补齐开发期新增的 conversations 字段。"""

    inspector = inspect(current_engine)
    if "conversations" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "is_pinned" in columns:
        return

    with current_engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE conversations "
                "ADD COLUMN is_pinned BOOLEAN NOT NULL DEFAULT FALSE"
            )
        )


def ensure_upload_task_columns(current_engine: Engine) -> None:
    """补齐 upload_tasks 的新增字段。"""

    inspector = inspect(current_engine)
    if "upload_tasks" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("upload_tasks")}
    statements: list[str] = []

    if "expires_at" not in columns:
        statements.append(
            "ALTER TABLE upload_tasks "
            "ADD COLUMN expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()"
        )
    if "auto_create_document" not in columns:
        statements.append(
            "ALTER TABLE upload_tasks "
            "ADD COLUMN auto_create_document BOOLEAN NOT NULL DEFAULT TRUE"
        )
    if "auto_index_on_complete" not in columns:
        statements.append(
            "ALTER TABLE upload_tasks "
            "ADD COLUMN auto_index_on_complete BOOLEAN NOT NULL DEFAULT TRUE"
        )
    if "document_id" not in columns:
        statements.append(
            "ALTER TABLE upload_tasks "
            "ADD COLUMN document_id INTEGER NULL"
        )
    if "processing_status" not in columns:
        statements.append(
            "ALTER TABLE upload_tasks "
            "ADD COLUMN processing_status VARCHAR(50) NOT NULL DEFAULT ''"
        )
    if "processing_error_message" not in columns:
        statements.append(
            "ALTER TABLE upload_tasks "
            "ADD COLUMN processing_error_message TEXT NOT NULL DEFAULT ''"
        )

    if not statements:
        return

    with current_engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def ensure_upload_part_columns(current_engine: Engine) -> None:
    """补齐 upload_parts 的新增字段。"""

    inspector = inspect(current_engine)
    if "upload_parts" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("upload_parts")}
    statements: list[str] = []

    if "retry_count" not in columns:
        statements.append(
            "ALTER TABLE upload_parts "
            "ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"
        )
    if "last_error_message" not in columns:
        statements.append(
            "ALTER TABLE upload_parts "
            "ADD COLUMN last_error_message TEXT NOT NULL DEFAULT ''"
        )

    if not statements:
        return

    with current_engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def ensure_upload_processing_job_columns(current_engine: Engine) -> None:
    """补齐 upload_processing_jobs 的新增字段。"""

    inspector = inspect(current_engine)
    if "upload_processing_jobs" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("upload_processing_jobs")}
    statements: list[str] = []

    if "max_retry_count" not in columns:
        statements.append(
            "ALTER TABLE upload_processing_jobs "
            "ADD COLUMN max_retry_count INTEGER NOT NULL DEFAULT 3"
        )
    if "stage" not in columns:
        statements.append(
            "ALTER TABLE upload_processing_jobs "
            "ADD COLUMN stage VARCHAR(50) NOT NULL DEFAULT 'download'"
        )
    if "depends_on_job_id" not in columns:
        statements.append(
            "ALTER TABLE upload_processing_jobs "
            "ADD COLUMN depends_on_job_id INTEGER NULL"
        )
    if "attempt_count" not in columns:
        statements.append(
            "ALTER TABLE upload_processing_jobs "
            "ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0"
        )
    if "max_attempts" not in columns:
        statements.append(
            "ALTER TABLE upload_processing_jobs "
            "ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3"
        )
    if "celery_task_id" not in columns:
        statements.append(
            "ALTER TABLE upload_processing_jobs "
            "ADD COLUMN celery_task_id VARCHAR(255) NOT NULL DEFAULT ''"
        )
    if "next_run_at" not in columns:
        statements.append(
            "ALTER TABLE upload_processing_jobs "
            "ADD COLUMN next_run_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()"
        )
    if "claim_token" not in columns:
        statements.append(
            "ALTER TABLE upload_processing_jobs "
            "ADD COLUMN claim_token VARCHAR(64) NOT NULL DEFAULT ''"
        )
    if "locked_by" not in columns:
        statements.append(
            "ALTER TABLE upload_processing_jobs "
            "ADD COLUMN locked_by VARCHAR(100) NOT NULL DEFAULT ''"
        )
    if "claimed_at" not in columns:
        statements.append(
            "ALTER TABLE upload_processing_jobs "
            "ADD COLUMN claimed_at TIMESTAMP WITHOUT TIME ZONE NULL"
        )
    if "lease_expires_at" not in columns:
        statements.append(
            "ALTER TABLE upload_processing_jobs "
            "ADD COLUMN lease_expires_at TIMESTAMP WITHOUT TIME ZONE NULL"
        )
    if "started_at" not in columns:
        statements.append(
            "ALTER TABLE upload_processing_jobs "
            "ADD COLUMN started_at TIMESTAMP WITHOUT TIME ZONE NULL"
        )
    if "completed_at" not in columns:
        statements.append(
            "ALTER TABLE upload_processing_jobs "
            "ADD COLUMN completed_at TIMESTAMP WITHOUT TIME ZONE NULL"
        )
    if "last_alert_at" not in columns:
        statements.append(
            "ALTER TABLE upload_processing_jobs "
            "ADD COLUMN last_alert_at TIMESTAMP WITHOUT TIME ZONE NULL"
        )
    if "alert_status" not in columns:
        statements.append(
            "ALTER TABLE upload_processing_jobs "
            "ADD COLUMN alert_status VARCHAR(50) NOT NULL DEFAULT ''"
        )

    if not statements:
        return

    with current_engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def get_session():
    """FastAPI 依赖函数：为每个请求提供数据库 Session。"""

    with Session(engine) as session:
        yield session
