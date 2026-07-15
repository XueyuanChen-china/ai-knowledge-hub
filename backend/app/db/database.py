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


def get_session():
    """FastAPI 依赖函数：为每个请求提供数据库 Session。"""

    with Session(engine) as session:
        yield session
