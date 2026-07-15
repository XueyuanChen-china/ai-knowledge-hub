import os
from uuid import uuid4

from sqlalchemy import text
from sqlmodel import SQLModel, create_engine

TEST_DATABASE_ADMIN_URL = os.getenv(
    "TEST_DATABASE_ADMIN_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
)
TEST_DATABASE_NAME_PREFIX = os.getenv(
    "TEST_DATABASE_NAME_PREFIX",
    "ai_knowledge_hub_test_",
)


class PostgresTestDatabase:
    """为单个测试用例创建独立 PostgreSQL 数据库。"""

    def __init__(self) -> None:
        self.database_name = f"{TEST_DATABASE_NAME_PREFIX}{uuid4().hex[:8]}"
        self.admin_engine = create_engine(
            TEST_DATABASE_ADMIN_URL,
            isolation_level="AUTOCOMMIT",
            pool_pre_ping=True,
        )
        self.database_url = self._build_database_url(self.database_name)
        self.engine = None

    def _build_database_url(self, database_name: str) -> str:
        prefix, _, _ = TEST_DATABASE_ADMIN_URL.rpartition("/")
        return f"{prefix}/{database_name}"

    def create_engine(self):
        with self.admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{self.database_name}"'))

        self.engine = create_engine(
            self.database_url,
            pool_pre_ping=True,
        )
        SQLModel.metadata.create_all(self.engine)
        return self.engine

    def dispose(self) -> None:
        if self.engine is not None:
            self.engine.dispose()

        with self.admin_engine.connect() as connection:
            connection.execute(
                text(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = :database_name
                      AND pid <> pg_backend_pid()
                    """
                ),
                {"database_name": self.database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{self.database_name}"'))

        self.admin_engine.dispose()
