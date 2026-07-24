"""Alembic 迁移运行环境。

这里导入 SQLModel metadata 作为迁移的目标结构，但不会调用应用启动初始化。
迁移由 Alembic 明确执行，应用启动只检查当前 revision。
"""

from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from app.config import get_settings
from app.db import models  # noqa: F401 让所有 SQLModel 表注册到 metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def get_database_url() -> str:
    """优先使用环境配置，避免把本地连接串作为真实运行配置。"""

    # 测试会通过 Config.set_main_option 注入临时数据库；正式运行则使用
    # .env / 环境变量中的 DATABASE_URL。
    configured_url = config.get_main_option("sqlalchemy.url")
    default_url = "postgresql+psycopg://postgres:postgres@localhost:5432/ai_knowledge_hub"
    if configured_url and configured_url != default_url:
        return configured_url
    return get_settings().database_url


def run_migrations_offline() -> None:
    """生成离线 SQL，不建立数据库连接。"""

    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """连接 PostgreSQL 后执行迁移。"""

    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
