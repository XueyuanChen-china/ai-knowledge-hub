import json
import logging
import unittest
from types import SimpleNamespace

from app.observability.context import bind_context
from app.observability.logging import JsonLogFormatter, redact_value
from app.observability.metrics import get_metrics
from app.services.upload_celery_service import build_upload_task_headers
from app.tasks.upload_tasks import get_task_context


class ObservabilityTests(unittest.TestCase):
    """验证关联字段、敏感值脱敏和低基数指标。"""

    def setUp(self) -> None:
        get_metrics().reset()

    def test_json_formatter_includes_context_and_redacts_sensitive_fields(self) -> None:
        record = logging.LogRecord(
            "test.observability",
            logging.INFO,
            __file__,
            1,
            "Authorization: Bearer secret-token",
            (),
            None,
        )
        record.log_fields = {
            "llm_api_key": "should-not-appear",
            "presigned_url": "https://example.com/a?Signature=secret",
        }

        with bind_context(request_id="req-1", trace_id="trace-1"):
            payload = json.loads(JsonLogFormatter().format(record))

        self.assertEqual(payload["request_id"], "req-1")
        self.assertEqual(payload["trace_id"], "trace-1")
        self.assertNotIn("secret-token", payload["message"])
        self.assertEqual(payload["llm_api_key"], "[REDACTED]")
        self.assertNotIn("Signature=secret", payload["presigned_url"])

    def test_nested_sensitive_fields_are_redacted(self) -> None:
        value = redact_value({"nested": {"oss_access_key_secret": "secret"}})
        self.assertEqual(value["nested"]["oss_access_key_secret"], "[REDACTED]")

    def test_formatter_redacts_sensitive_text_from_exception(self) -> None:
        try:
            raise RuntimeError("request failed: Bearer exception-token")
        except RuntimeError:
            record = logging.getLogger("test.observability").makeRecord(
                "test.observability",
                logging.ERROR,
                __file__,
                1,
                "operation failed",
                (),
                exc_info=__import__("sys").exc_info(),
            )

        payload = json.loads(JsonLogFormatter().format(record))
        self.assertNotIn("exception-token", payload["exception"])

    def test_metrics_do_not_use_trace_id_as_label(self) -> None:
        metrics = get_metrics()
        metrics.record_http(
            method="GET",
            endpoint="/uploads/{upload_id}",
            status_code=200,
            duration_seconds=0.1,
        )
        metrics.record_upload_job("download", "retry_scheduled")

        rendered = metrics.render_prometheus()
        self.assertIn('endpoint="/uploads/{upload_id}"', rendered)
        self.assertIn("ai_knowledge_hub_upload_retries_total", rendered)
        self.assertNotIn("trace_id", rendered)

    def test_celery_headers_preserve_http_trace_context(self) -> None:
        job = SimpleNamespace(id=17)
        upload_task = SimpleNamespace(upload_id="upl_001")
        with bind_context(request_id="req-17", trace_id="trace-17"):
            headers = build_upload_task_headers(job, upload_task)

        task = SimpleNamespace(request=SimpleNamespace(headers=headers, id="celery-17"))
        task_context = get_task_context(task, 17)

        self.assertEqual(task_context["request_id"], "req-17")
        self.assertEqual(task_context["trace_id"], "trace-17")
        self.assertEqual(task_context["upload_id"], "upl_001")
        self.assertEqual(task_context["celery_task_id"], "celery-17")


if __name__ == "__main__":
    unittest.main()
