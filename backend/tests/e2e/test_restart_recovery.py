"""U10 重启恢复路径的可执行检查。

真实 API 重启由 operations 手册控制；本测试验证已有持久化 checkpoint 测试门禁，
避免测试进程自行杀掉开发者正在使用的 API/Worker。
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


class RestartRecoveryTest(unittest.TestCase):
    def test_persistent_checkpoint_regression_suite(self) -> None:
        if os.getenv("RUN_ENTERPRISE_E2E", "").lower() not in {"1", "true", "yes"}:
            self.skipTest("set RUN_ENTERPRISE_E2E=1 to run restart recovery gate")
        backend_dir = Path(__file__).parents[2]
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.test_graph_checkpoint_persistence"],
            cwd=backend_dir,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
