#!/usr/bin/env python3
"""在容器启动前等待本项目需要的本地依赖就绪。

这个脚本只检测 PostgreSQL、Elasticsearch、RabbitMQ 和 Redis 的可连接性，
不会连接阿里云 OSS 或 Qwen，也不会执行任何 DDL。数据库迁移由 API 启动脚本
显式执行，Worker 则只检查 schema 已就绪后再开始消费。
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path
from typing import Callable
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

# 以 ``python scripts/xxx.py`` 直接执行时，sys.path 默认只有 scripts 目录，
# 需要显式加入 backend 根目录，才能导入同级 app 包。
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings


def parse_arguments() -> argparse.Namespace:
    """解析需要等待的服务名称和最大等待时间。"""

    parser = argparse.ArgumentParser(description="Wait for local service dependencies")
    parser.add_argument(
        "--services",
        nargs="+",
        default=["postgres", "elasticsearch", "rabbitmq", "redis"],
        choices=["postgres", "elasticsearch", "rabbitmq", "redis"],
        help="Services that must be available before continuing.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=120.0,
        help="Maximum total wait time for each dependency.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=2.0,
        help="Delay between readiness checks.",
    )
    return parser.parse_args()


def endpoint_from_url(url: str, default_port: int) -> tuple[str, int]:
    """从 PostgreSQL、AMQP、Redis URL 中取出 TCP host 与 port。"""

    parsed = urlparse(url.replace("postgresql+psycopg://", "postgresql://"))
    if not parsed.hostname:
        raise ValueError(f"Cannot determine host from URL: {url!r}")
    return parsed.hostname, parsed.port or default_port


def tcp_ready(host: str, port: int) -> bool:
    """通过 TCP 建连判断服务端口是否已接受连接。"""

    try:
        with socket.create_connection((host, port), timeout=2.0):
            return True
    except OSError:
        return False


def elasticsearch_ready(url: str) -> bool:
    """等待 ES 至少达到 yellow 状态，避免后续索引立即失败。"""

    endpoint = f"{url.rstrip('/')}/_cluster/health?wait_for_status=yellow&timeout=1s"
    try:
        with urlopen(endpoint, timeout=3.0) as response:  # nosec B310: URL is trusted config.
            return 200 <= response.status < 300
    except (URLError, TimeoutError, OSError):
        return False


def wait_for(name: str, check: Callable[[], bool], timeout: float, interval: float) -> None:
    """按固定间隔重试某项检查，超时后以明确错误退出。"""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            print(f"Dependency ready: {name}", flush=True)
            return
        print(f"Waiting for dependency: {name}", flush=True)
        time.sleep(interval)
    raise TimeoutError(f"Dependency did not become ready within {timeout:.0f}s: {name}")


def main() -> int:
    """按指定服务依次等待。"""

    args = parse_arguments()
    settings = get_settings()

    postgres_host, postgres_port = endpoint_from_url(settings.database_url, 5432)
    rabbitmq_host, rabbitmq_port = endpoint_from_url(settings.celery_broker_url, 5672)
    redis_host, redis_port = endpoint_from_url(settings.auth_redis_url, 6379)

    checks: dict[str, Callable[[], bool]] = {
        "postgres": lambda: tcp_ready(postgres_host, postgres_port),
        "elasticsearch": lambda: elasticsearch_ready(settings.elasticsearch_url),
        "rabbitmq": lambda: tcp_ready(rabbitmq_host, rabbitmq_port),
        "redis": lambda: tcp_ready(redis_host, redis_port),
    }

    for service_name in args.services:
        wait_for(
            service_name,
            checks[service_name],
            max(1.0, args.timeout_seconds),
            max(0.1, args.interval_seconds),
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (TimeoutError, ValueError) as exc:
        print(f"Dependency readiness failed: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc
