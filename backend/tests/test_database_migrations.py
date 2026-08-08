import unittest
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from postgres_test_utils import PostgresTestDatabase
from app.db.database import (
    check_database_ready,
    get_current_database_revision,
    get_expected_database_revision,
)


class DatabaseMigrationTests(unittest.TestCase):
    """验证测试库只能通过 Alembic 建表。"""

    def setUp(self) -> None:
        self.database = PostgresTestDatabase()
        self.engine = self.database.create_engine()

    def tearDown(self) -> None:
        self.database.dispose()

    def _config(self) -> Config:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", self.database.database_url)
        return config

    def test_upgrade_head_creates_all_current_tables_and_revision(self) -> None:
        expected_tables = {
            "alembic_version",
            "knowledge_bases",
            "documents",
            "knowledge_items",
            "knowledge_item_reviews",
            "chunks",
            "conversations",
            "messages",
            "review_tasks",
            "upload_tasks",
            "upload_parts",
            "upload_processing_jobs",
            "upload_audit_logs",
            "organizations",
            "users",
            "organization_memberships",
            "security_audit_logs",
        }
        self.assertTrue(expected_tables.issubset(set(inspect(self.engine).get_table_names())))
        self.assertEqual(
            get_current_database_revision(self.engine),
            get_expected_database_revision(),
        )
        check_database_ready(self.engine)

        inspector = inspect(self.engine)
        for table_name in (
            "knowledge_bases",
            "documents",
            "knowledge_items",
            "chunks",
            "conversations",
            "upload_tasks",
        ):
            column_names = {column["name"] for column in inspector.get_columns(table_name)}
            self.assertIn("organization_id", column_names)
        for table_name in (
            "knowledge_bases",
            "documents",
            "knowledge_items",
            "conversations",
            "upload_tasks",
        ):
            column_names = {column["name"] for column in inspector.get_columns(table_name)}
            self.assertIn("created_by_user_id", column_names)

        conversation_columns = {
            column["name"] for column in inspector.get_columns("conversations")
        }
        self.assertTrue(
            {
                "context_summary",
                "context_summary_version",
                "context_summary_through_message_id",
                "context_summary_updated_at",
            }.issubset(conversation_columns)
        )

    def test_downgrade_and_upgrade_baseline_on_empty_schema(self) -> None:
        command.downgrade(self._config(), "base")
        self.assertEqual(inspect(self.engine).get_table_names(), ["alembic_version"])

        command.upgrade(self._config(), "head")
        self.assertIn("knowledge_bases", inspect(self.engine).get_table_names())
        self.assertEqual(
            get_current_database_revision(self.engine),
            get_expected_database_revision(),
        )


if __name__ == "__main__":
    unittest.main()
