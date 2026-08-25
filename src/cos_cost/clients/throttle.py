"""DescribeBillResourceSummary / DescribeBillDetail：5 次/秒。"""

from __future__ import annotations

import time


class RateLimiter:
    def __init__(self, qps: float = 5.0) -> None:
        if qps <= 0:
            raise ValueError("qps 必须为正")
        self.min_interval = 1.0 / qps
        self._last = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last = time.monotonic()
