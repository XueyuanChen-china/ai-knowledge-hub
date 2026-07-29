import unittest

from fastapi.testclient import TestClient

from app.api import health
from app.main import app
from app.observability.metrics import get_metrics


class HealthApiTests(unittest.TestCase):
    """验证存活检查与按职责拆分的 readiness 语义。"""

    def setUp(self) -> None:
        self.client = TestClient(app)
        get_metrics().reset()
        self.original_postgresql = health.check_postgresql
        self.original_elasticsearch = health.check_elasticsearch
        self.original_rabbitmq = health.check_rabbitmq

    def tearDown(self) -> None:
        health.check_postgresql = self.original_postgresql
        health.check_elasticsearch = self.original_elasticsearch
        health.check_rabbitmq = self.original_rabbitmq

    def test_live_does_not_require_dependencies_and_returns_request_id(self) -> None:
        health.check_postgresql = lambda: (False, "database unavailable")

        response = self.client.get("/health/live", headers={"X-Request-ID": "req-live-1"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(response.headers["X-Request-ID"], "req-live-1")
        self.assertEqual(response.headers["X-Trace-ID"], "req-live-1")

    def test_general_ready_fails_only_when_postgresql_is_unavailable(self) -> None:
        health.check_postgresql = lambda: (False, "OperationalError")

        response = self.client.get("/health/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["components"]["postgresql"]["status"], "degraded")

    def test_search_ready_reports_elasticsearch_degradation(self) -> None:
        health.check_postgresql = lambda: (True, "")
        health.check_elasticsearch = lambda: (False, "ConnectionError")

        response = self.client.get("/health/ready/search")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "degraded")
        self.assertEqual(response.json()["components"]["elasticsearch"]["status"], "degraded")

    def test_upload_ready_reports_rabbitmq_degradation(self) -> None:
        health.check_postgresql = lambda: (True, "")
        health.check_rabbitmq = lambda: (False, "ConnectionRefusedError")

        response = self.client.get("/health/ready/uploads")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["components"]["rabbitmq"]["status"], "degraded")

    def test_metrics_exports_prometheus_text(self) -> None:
        get_metrics().record_operation("semantic_search", 0.12, outcome="success")

        response = self.client.get("/health/metrics")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.headers["content-type"])
        self.assertIn("ai_knowledge_hub_operations_total", response.text)
        self.assertIn('operation="semantic_search"', response.text)


if __name__ == "__main__":
    unittest.main()
