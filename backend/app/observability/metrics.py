"""无外部依赖的低基数 Prometheus 文本指标。

第一版只在当前进程内聚合，用于本地排障和后续接 Prometheus 的接口契约。
多副本全局聚合与告警平台不在本单元范围内。
"""

from __future__ import annotations

from collections import defaultdict
from threading import Lock
from typing import Iterable


LabelTuple = tuple[tuple[str, str], ...]


def _labels(values: dict[str, str | int]) -> LabelTuple:
    return tuple(sorted((key, str(value)) for key, value in values.items()))


def _render_labels(labels: LabelTuple) -> str:
    if not labels:
        return ""
    escaped = []
    for key, value in labels:
        escaped_value = value.replace("\\", "\\\\").replace('"', '\\"')
        escaped.append(f'{key}="{escaped_value}"')
    return "{" + ",".join(escaped) + "}"


class MetricsRegistry:
    """线程安全的 counter 与 duration 聚合器。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[tuple[str, LabelTuple], float] = defaultdict(float)
        self._durations: dict[tuple[str, LabelTuple], tuple[int, float]] = {}

    def increment(self, name: str, labels: dict[str, str | int] | None = None, value: float = 1.0) -> None:
        with self._lock:
            self._counters[(name, _labels(labels or {}))] += value

    def observe_duration(self, name: str, seconds: float, labels: dict[str, str | int] | None = None) -> None:
        key = (name, _labels(labels or {}))
        with self._lock:
            count, total = self._durations.get(key, (0, 0.0))
            self._durations[key] = (count + 1, total + max(0.0, seconds))

    def record_http(self, *, method: str, endpoint: str, status_code: int, duration_seconds: float) -> None:
        labels = {"method": method, "endpoint": endpoint, "status_code": str(status_code)}
        self.increment("ai_knowledge_hub_http_requests_total", labels)
        self.observe_duration("ai_knowledge_hub_http_request_duration_seconds", duration_seconds, {"method": method, "endpoint": endpoint})
        if status_code >= 500:
            self.increment("ai_knowledge_hub_http_errors_total", {"method": method, "endpoint": endpoint, "status_class": "5xx"})

    def record_operation(self, operation: str, duration_seconds: float, *, outcome: str) -> None:
        labels = {"operation": operation, "outcome": outcome}
        self.increment("ai_knowledge_hub_operations_total", labels)
        self.observe_duration("ai_knowledge_hub_operation_duration_seconds", duration_seconds, labels)

    def record_upload_job(self, stage: str, status: str) -> None:
        self.increment("ai_knowledge_hub_upload_jobs_total", {"stage": stage, "status": status})
        if status == "retry_scheduled":
            self.increment("ai_knowledge_hub_upload_retries_total", {"stage": stage})
        if status == "failed":
            self.increment("ai_knowledge_hub_upload_dead_letter_messages_total", {"stage": stage})

    def record_celery_task(self, stage: str, status: str) -> None:
        self.increment("ai_knowledge_hub_celery_tasks_total", {"stage": stage, "status": status})

    def record_context_pack(self, purpose: str, *, truncated: bool, omitted_count: int) -> None:
        """记录低基数上下文质量指标，不记录问题正文。"""

        self.increment(
            "ai_knowledge_hub_context_packs_total",
            {"purpose": purpose, "truncated": str(bool(truncated)).lower()},
        )
        if omitted_count > 0:
            self.increment(
                "ai_knowledge_hub_context_omitted_items_total",
                {"purpose": purpose},
                value=omitted_count,
            )

    def record_context_recovery(self, outcome: str) -> None:
        """记录历史恢复结果，标签只使用固定状态。"""

        self.increment(
            "ai_knowledge_hub_context_recovery_total",
            {"outcome": outcome},
        )

    def render_prometheus(self) -> str:
        with self._lock:
            counters = list(self._counters.items())
            durations = list(self._durations.items())
        lines: list[str] = []
        for (name, labels), value in sorted(counters):
            lines.append(f"{name}{_render_labels(labels)} {value:g}")
        for (name, labels), (count, total) in sorted(durations):
            lines.append(f"{name}_count{_render_labels(labels)} {count}")
            lines.append(f"{name}_sum{_render_labels(labels)} {total:.6f}")
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._durations.clear()


_METRICS = MetricsRegistry()


def get_metrics() -> MetricsRegistry:
    return _METRICS
