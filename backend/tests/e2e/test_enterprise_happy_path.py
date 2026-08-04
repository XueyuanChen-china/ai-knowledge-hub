"""U10 happy path：登录 -> 五格式 OSS 上传 -> 索引 -> 搜索 -> Chat。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from .e2e_support import E2EClient, e2e_enabled


class EnterpriseHappyPathTest(unittest.TestCase):
    def test_multiformat_index_search_and_chat(self) -> None:
        if not e2e_enabled():
            self.skipTest("set RUN_ENTERPRISE_E2E=1 to run external-service E2E")

        base_url = os.getenv("E2E_BASE_URL", "http://127.0.0.1:8000")
        token = os.getenv("E2E_ACCESS_TOKEN", "")
        knowledge_base_id = os.getenv("E2E_KNOWLEDGE_BASE_ID", "")
        if not knowledge_base_id:
            self.skipTest("E2E_KNOWLEDGE_BASE_ID is required")

        # 优先使用显式 token；没有 token 时用演示账号登录，覆盖真正的 login 入口。
        client = E2EClient(base_url, token)
        if not token:
            email = os.getenv("E2E_EMAIL", "")
            password = os.getenv("E2E_PASSWORD", "")
            if not email or not password:
                self.skipTest(
                    "provide E2E_ACCESS_TOKEN or both E2E_EMAIL and E2E_PASSWORD"
                )
            status, login_response = client.request(
                "/api/auth/login",
                "POST",
                {"email": email, "password": password},
            )
            self.assertEqual(status, 200)
            token = str(login_response["access_token"])
            client = E2EClient(base_url, token)

        report_path = Path(
            os.getenv(
                "E2E_REPORT_PATH",
                "data/retrieval_benchmarks/multiformat-e2e-u10.json",
            )
        )
        command = [
            sys.executable,
            str(Path(__file__).parents[2] / "scripts/test_multiformat_e2e.py"),
            "--base-url",
            base_url,
            "--knowledge-base-id",
            knowledge_base_id,
            "--access-token",
            token,
            "--report",
            str(report_path),
        ]
        completed = subprocess.run(command, cwd=Path(__file__).parents[2], check=False)
        self.assertEqual(completed.returncode, 0)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        search = report.get("search", {})
        self.assertEqual(search.get("failures"), [])
        self.assertGreater(search.get("passed_query_count", 0), 0)

        status, response = client.request(
            "/api/chat",
            "POST",
            {
                "knowledge_base_id": int(knowledge_base_id),
                "question": "普通员工报销必须提交哪三项基础材料？",
                "retrieve_top_k": 5,
            },
        )
        self.assertIn(status, {200, 202})
        self.assertTrue(response.get("answer") or response.get("review_payload"))
        if status == 200 and response.get("answer"):
            self.assertTrue(response.get("citations"))


if __name__ == "__main__":
    unittest.main()
