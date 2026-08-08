"""LangGraph PostgreSQL checkpoint 的生命周期管理。

checkpoint 是图执行状态，不是用户可见的聊天记录。它使用独立 Psycopg pool，
不能复用 FastAPI 请求中的 SQLModel Session。
"""

import atexit
from threading import Lock
from typing import Optional

from app.config import Settings, get_settings

_CHECKPOINTER = None
_POOL = None
_DATABASE_URL = ""
_LOCK = Lock()


def to_psycopg_database_url(database_url: str) -> str:
    """把 SQLAlchemy URL 转成 Psycopg 3 可直接使用的 URL。"""

    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def resolve_checkpoint_database_url(settings: Settings) -> str:
    """解析 checkpoint 的数据库连接地址。"""

    return settings.graph_checkpoint_database_url.strip() or settings.database_url


def initialize_graph_checkpointer(settings: Optional[Settings] = None):
    """创建进程级同步 PostgresSaver，但不执行第三方表初始化。

    FastAPI lifespan 调用此函数。表结构必须显式执行 setup 脚本创建，避免每个
    API/worker 启动时对数据库执行隐式 DDL。
    """

    global _CHECKPOINTER, _POOL, _DATABASE_URL

    resolved_settings = settings or get_settings()
    database_url = resolve_checkpoint_database_url(resolved_settings)
    with _LOCK:
        if _CHECKPOINTER is not None and _DATABASE_URL == database_url:
            return _CHECKPOINTER

        close_graph_checkpointer()

        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg_pool import ConnectionPool
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "langgraph-checkpoint-postgres and psycopg pool are required for "
                "persistent graph checkpoints"
            ) from exc

        _POOL = ConnectionPool(
            conninfo=to_psycopg_database_url(database_url),
            min_size=resolved_settings.graph_checkpoint_pool_min_size,
            max_size=resolved_settings.graph_checkpoint_pool_max_size,
            timeout=resolved_settings.graph_checkpoint_pool_timeout_seconds,
            kwargs={"autocommit": True, "prepare_threshold": 0},
            open=True,
        )
        _CHECKPOINTER = PostgresSaver(_POOL)
        _DATABASE_URL = database_url
        return _CHECKPOINTER


def get_graph_checkpointer():
    """取得当前进程的 PostgresSaver。

    正常 API 服务在 lifespan 中已初始化；这里的延迟初始化仅用于 CLI 和直接
    调用图的测试，不会自动调用 PostgresSaver.setup()。
    """

    if _CHECKPOINTER is None:
        return initialize_graph_checkpointer()
    return _CHECKPOINTER


def close_graph_checkpointer() -> None:
    """关闭独立连接池。应用退出时调用。"""

    global _CHECKPOINTER, _POOL, _DATABASE_URL

    pool = _POOL
    _CHECKPOINTER = None
    _POOL = None
    _DATABASE_URL = ""
    if pool is not None:
        pool.close()


def setup_graph_checkpoint_schema(settings: Optional[Settings] = None) -> None:
    """显式创建 langgraph-checkpoint-postgres 所需表。

    这是唯一允许的第三方工具表 DDL 入口。业务表仍由 Alembic 管理。
    """

    resolved_settings = settings or get_settings()
    database_url = resolve_checkpoint_database_url(resolved_settings)
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg_pool import ConnectionPool
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "langgraph-checkpoint-postgres and psycopg pool are required for "
            "persistent graph checkpoints"
        ) from exc

    pool = ConnectionPool(
        conninfo=to_psycopg_database_url(database_url),
        min_size=1,
        max_size=1,
        timeout=resolved_settings.graph_checkpoint_pool_timeout_seconds,
        kwargs={"autocommit": True, "prepare_threshold": 0},
        open=True,
    )
    try:
        PostgresSaver(pool).setup()
    finally:
        pool.close()


# CLI、单元测试等不会经过 FastAPI lifespan 的调用路径也要释放 pool，
# 否则 Psycopg 的后台维护线程会延迟 Python 进程退出。
atexit.register(close_graph_checkpointer)
