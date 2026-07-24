"""登录接口的单进程失败限流。

多副本场景需要 Redis 等共享存储，U3 只实现本地开发和单进程服务可用的版本。
"""

from collections import defaultdict, deque
from threading import Lock
from time import time

from app.config import Settings

_lock = Lock()
_failures: dict[str, deque[float]] = defaultdict(deque)


def check_login_rate_limit(key: str, settings: Settings) -> int:
    """返回剩余等待秒数，0 表示允许尝试。"""

    now = time()
    window = max(1, settings.auth_login_rate_limit_window_seconds)
    with _lock:
        attempts = _failures[key]
        while attempts and now - attempts[0] >= window:
            attempts.popleft()
        if len(attempts) < settings.auth_login_rate_limit_max_attempts:
            return 0
        return max(1, int(window - (now - attempts[0])))


def record_login_failure(key: str, settings: Settings) -> None:
    now = time()
    window = max(1, settings.auth_login_rate_limit_window_seconds)
    with _lock:
        attempts = _failures[key]
        while attempts and now - attempts[0] >= window:
            attempts.popleft()
        attempts.append(now)


def clear_login_failures(key: str) -> None:
    with _lock:
        _failures.pop(key, None)
