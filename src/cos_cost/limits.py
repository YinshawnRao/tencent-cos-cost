"""本机采集限速。可用环境变量覆盖。"""

from __future__ import annotations

import os


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def cos_qps() -> float:
    """COS Head / GetBucketLifecycle / Versioning / Logging / Inventory 全局 QPS。"""
    return _env_float("COS_QPS", 8.0)


def cos_max_inflight() -> int:
    """COS 配置/Head 最大并发。"""
    return _env_int("COS_MAX_INFLIGHT", 4)


def billing_qps() -> float:
    return _env_float("COS_BILLING_QPS", 5.0)


def monitor_batch() -> int:
    """GetMonitorData 单次实例数。产品说明 50；被拒时可改小。"""
    return _env_int("COS_MONITOR_BATCH", 50)


def config_top_n() -> int:
    """账号首拉只读 Top N 桶的生命周期等配置。桶页再懒加载。"""
    return _env_int("COS_CONFIG_TOP_N", 30)


# 账号首拉监控：C5 容量 / 标准% / 外网 / 请求，以及规则需要的分块。
ACCOUNT_MONITOR_METRICS = (
    "StdStorage",
    "MazStdStorage",
    "SiaStorage",
    "MazIaStorage",
    "ArcStorage",
    "DeepArcStorage",
    "StdMultipartStorage",
    "InternetTraffic",
    "GetRequests",
    "PutRequests",
    "4xxResponse",
    "5xxResponse",
)

# Top N / 桶页补齐：R06 CDN 备注、内网、日趋势。
BUCKET_EXTRA_MONITOR_METRICS = (
    "InternalTraffic",
    "CdnOriginTraffic",
)
