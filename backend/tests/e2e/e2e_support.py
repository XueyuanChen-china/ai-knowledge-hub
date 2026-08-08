"""U10 E2E 的最小 HTTP 客户端和环境开关。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass
class E2EClient:
    base_url: str
    access_token: str

    def request(
        self,
        path: str,
        method: str = "GET",
        payload: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, Any]:
        body = None
        headers = {"Accept": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read().decode("utf-8")
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                detail = json.loads(raw)
            except json.JSONDecodeError:
                detail = raw
            return exc.code, detail


def e2e_enabled() -> bool:
    return os.getenv("RUN_ENTERPRISE_E2E", "").lower() in {"1", "true", "yes"}


def required_env(*names: str) -> dict[str, str]:
    return {name: os.environ[name] for name in names}
