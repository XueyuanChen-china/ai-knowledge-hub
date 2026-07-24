"""PostgreSQL 连接和会话管理。

表结构由 Alembic 管理。本模块不再在应用启动时调用 ``create_all`` 或执行
``ALTER TABLE``，避免不同实例在启动时偷偷修改生产 schema。
"""

from pathlib import Path
from typing import Optional

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine

from app.config import get_settings

# 导入 models 的目的不是直接使用变量，而是让 SQLModel 注册所有表模型，
# 这样 Alembic env.py 才能拿到完整的 SQLModel.metadata。
from app.db import models  # noqa: F401

POSTGRESQL_PREFIXES = (
    "postgresql://",
    "postgresql+psycopg://",
    "postgresql+psycopg2://",
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG_PATH = PROJECT_ROOT / "alembic.ini"

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


# engine 是应用连接数据库的核心对象，所有请求 Session 都基于它创建。
engine = build_engine(settings.database_url)


def _get_alembic_script_directory() -> ScriptDirectory:
    """读取本地迁移脚本目录，得到当前代码声明的 head revision。"""

    config = Config(str(ALEMBIC_CONFIG_PATH))
    return ScriptDirectory.from_config(config)


def get_current_database_revision(current_engine: Engine = engine) -> Optional[str]:
    """读取数据库已经执行到的 Alembic revision。"""

    with current_engine.connect() as connection:
        return connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()


def get_expected_database_revision() -> str:
    """读取当前代码中的最新迁移 revision。"""

    head = _get_alembic_script_directory().get_current_head()
    if head is None:
        raise RuntimeError("No Alembic head revision is configured")
    return head


def check_database_ready(current_engine: Engine = engine) -> None:
    """验证数据库可连接且 revision 与代码一致。

    这里故意不执行迁移。revision 落后时必须由发布流程显式执行
    ``alembic upgrade head``，应用只给出明确错误。
    """

    try:
        with current_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            current_revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
    except Exception as exc:  # pragma: no cover - 由真实数据库连接失败触发
        raise RuntimeError(
            "Database is not ready. Run 'alembic upgrade head' and check the "
            "PostgreSQL connection."
        ) from exc

    expected_revision = get_expected_database_revision()
    if current_revision != expected_revision:
        raise RuntimeError(
            "Database revision is out of date: "
            f"current={current_revision!r}, expected={expected_revision!r}. "
            "Run 'alembic upgrade head' before starting the application."
        )


def get_session():
    """FastAPI 依赖函数：为每个请求提供数据库 Session。"""

    with Session(engine) as session:
        yield session
