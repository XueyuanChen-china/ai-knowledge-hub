"""结构化日志与敏感信息脱敏。

日志只保留低风险的关联 ID、事件名和运维字段。密码、JWT、API Key、
OSS secret 与带签名的 URL 无论作为字段还是文本出现都会被替换。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.observability.context import get_observability_context

SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "signature",
    "credential",
)
_BEARER_PATTERN = re.compile(r"(Bearer\s+)[^\s,;]+", re.IGNORECASE)
_JSON_SECRET_PATTERN = re.compile(
    r'("(?:password|secret|token|api[_-]?key)"\s*:\s*")[^"]*(")',
    re.IGNORECASE,
)


def is_sensitive_key(key: str) -> bool:
    """按字段名判断是否属于不应记录的敏感值。"""

    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def redact_text(value: str) -> str:
    """脱敏普通文本中的 Bearer token、JSON 密钥值和预签名 URL。"""

    redacted = _BEARER_PATTERN.sub(r"\1[REDACTED]", value)
    redacted = _JSON_SECRET_PATTERN.sub(r"\1[REDACTED]\2", redacted)
    if "?" in redacted and any(
        marker in redacted.lower()
        for marker in ("signature=", "x-oss-", "x-amz-", "security-token=")
    ):
        parsed = urlsplit(redacted)
        if parsed.scheme and parsed.netloc:
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "[REDACTED]", ""))
        return "[REDACTED_PRESIGNED_URL]"
    return redacted


def redact_value(value: Any, *, key: str = "") -> Any:
    """递归处理日志字段，避免因嵌套 dict 漏掉密钥。"""

    if is_sensitive_key(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): redact_value(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class JsonLogFormatter(logging.Formatter):
    """把标准 logging Record 转成可被日志平台检索的单行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": redact_text(record.getMessage()),
            **get_observability_context(),
        }
        log_fields = getattr(record, "log_fields", None)
        if isinstance(log_fields, dict):
            payload.update(redact_value(log_fields))
        if record.exc_info:
            # SDK 异常文本也可能包含 Authorization、预签名 URL 等内容。
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(log_level: str = "INFO") -> None:
    """配置一次根 logger，避免 FastAPI reload 时重复添加 handler。"""

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    if any(getattr(handler, "_ai_knowledge_hub_json", False) for handler in root_logger.handlers):
        return

    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    handler._ai_knowledge_hub_json = True  # type: ignore[attr-defined]
    root_logger.handlers.clear()
    root_logger.addHandler(handler)


def log_event(logger: logging.Logger, event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    """以统一 event 字段输出一条结构化日志。"""

    logger.log(level, event, extra={"log_fields": fields})
