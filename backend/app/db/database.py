from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

# 这里导入 models 的目的不是直接使用变量，而是让 SQLModel 注册所有表模型。
# 如果不导入，SQLModel.metadata 可能不知道有哪些表需要创建。
from app.db import models  # noqa: F401

settings = get_settings()

# SQLite 默认不允许同一个连接跨线程使用。
# FastAPI 在处理请求时可能跨线程，所以本地 SQLite 开发环境要加 check_same_thread=False。
connect_args = {"check_same_thread": False}

# engine 是应用连接数据库的核心对象。
# 后续所有 Session 都会基于这个 engine 创建。
engine = create_engine(settings.database_url, connect_args=connect_args)


def create_db_and_tables() -> None:
    """创建数据库目录和数据表。

    SQLite 是文件型数据库，第一次启动时需要确保 data/sqlite 目录存在。
    SQLModel.metadata.create_all 会根据 models.py 中的表模型创建表。
    """

    db_path = settings.database_url.removeprefix("sqlite:///")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)
    ensure_document_columns()


def ensure_document_columns() -> None:
    """补齐开发期新增的 documents 字段。

    create_all 只会创建不存在的表，不会自动给已有表添加新字段。
    Day 6 新增 extracted_text 后，旧的本地 SQLite 数据库需要补一次列。
    正式项目后面应该改用 Alembic 管理迁移。
    """

    inspector = inspect(engine)
    if "documents" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("documents")}
    if "extracted_text" in columns:
        return

    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE documents ADD COLUMN extracted_text TEXT DEFAULT ''")
        )


def get_session():
    """FastAPI 依赖函数：为每个请求提供数据库 Session。

    后续写 CRUD 接口时，可以通过 Depends(get_session) 拿到 session。
    with 语句会在请求结束后自动关闭数据库会话。
    """

    with Session(engine) as session:
        yield session
