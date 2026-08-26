"""DescribeBillResourceSummary / COS 配置：可取消的 QPS 限速。"""

from __future__ import annotations

import threading
import time
from typing import Any

from cos_cost.clients.errors import CollectCancelled, check_cancel

SLICE_SECONDS = 0.2


class RateLimiter:
    def __init__(self, qps: float = 5.0, *, cancel: Any | None = None) -> None:
        if qps <= 0:
            raise ValueError("qps 必须为正")
        self.min_interval = 1.0 / qps
        self._last = 0.0
        self._lock = threading.Lock()
        self._cancel = cancel

    def set_cancel(self, cancel: Any | None) -> None:
        self._cancel = cancel

    def wait(self, cancel: Any | None = None) -> None:
        ev = cancel if cancel is not None else self._cancel
        check_cancel(ev)
        with self._lock:
            now = time.monotonic()
            wait_for = max(0.0, self._last + self.min_interval - now)
            self._last = max(now, self._last + self.min_interval)
        _sleep_cancelable(wait_for, ev)


def _sleep_cancelable(seconds: float, cancel: Any | None) -> None:
    remaining = max(0.0, seconds)
    while remaining > 0:
        check_cancel(cancel)
        slice_s = remaining if remaining < SLICE_SECONDS else SLICE_SECONDS
        time.sleep(slice_s)
        remaining -= slice_s
    check_cancel(cancel)


def sleep_cancelable(seconds: float, cancel: Any | None) -> None:
    _sleep_cancelable(seconds, cancel)
