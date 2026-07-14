import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db import database


class DatabaseSupportTests(unittest.TestCase):
    def test_is_postgresql_url(self) -> None:
        self.assertTrue(
            database.is_postgresql_url(
                "postgresql+psycopg://postgres:postgres@localhost:5432/test"
            )
        )
        self.assertFalse(database.is_postgresql_url("sqlite:///./data/sqlite/test.db"))

    def test_validate_database_url_accepts_postgres(self) -> None:
        database.validate_database_url(
            "postgresql+psycopg://postgres:postgres@localhost:5432/test"
        )

    def test_validate_database_url_rejects_sqlite(self) -> None:
        with self.assertRaises(RuntimeError) as context:
            database.validate_database_url("sqlite:///./data/sqlite/test.db")

        self.assertIn("DATABASE_URL must be a PostgreSQL URL", str(context.exception))
