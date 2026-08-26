"""配置灯：由只读生命周期 / 版本 / 监控推导，禁止 List Objects。"""

from __future__ import annotations

from typing import Any, Protocol

from cos_cost.ext.rules import is_backup_bucket
from cos_cost.models import CollectSnapshot, ConfigLights, MonitorBucketMetrics

LIGHT_LABELS = (
    ("lifecycle", "生命周期"),
    ("fragments", "碎片"),
    ("cdn", "CDN"),
    ("versioning", "版本"),
    ("backup", "备份"),
)

GB = 1_000_000_000.0


class ConfigLightProvider(Protocol):
    def lights_for(self, bucket: str) -> ConfigLights:
        """只读探测桶配置。禁止 List Objects。"""


class UnknownConfigLights:
    def lights_for(self, bucket: str) -> ConfigLights:
        return ConfigLights()

    def health_for(self, bucket: str) -> list[dict[str, Any]]:
        return []


class SnapshotConfigLights:
    def __init__(self, snapshot: CollectSnapshot) -> None:
        self.snapshot = snapshot

    def lights_for(self, bucket: str) -> ConfigLights:
        cfg = self.snapshot.config.by_bucket.get(bucket) if self.snapshot.config else None
        metrics = self.snapshot.monitor.by_bucket.get(bucket) if self.snapshot.monitor else None
        lifecycle = "unknown"
        fragments = "unknown"
        versioning = "unknown"
        cdn = "unknown"
        backup = "ok" if is_backup_bucket(bucket) else "none"

        if cfg is not None:
            std_gb = _std_gb(metrics)
            mpu_gb = _mpu_gb(metrics)
            if not cfg.rules or not cfg.has_storage_transition():
                lifecycle = "risk" if std_gb >= 1024 else "none"
            else:
                lifecycle = "on"
            if mpu_gb >= 1.0 and not cfg.has_abort():
                fragments = "risk"
            elif cfg.has_abort():
                fragments = "ok"
            else:
                fragments = "none" if mpu_gb < 1.0 else "risk"
            if cfg.versioning_enabled() and not cfg.has_noncurrent_rule():
                versioning = "risk"
            elif cfg.versioning_enabled():
                versioning = "on"
            else:
                versioning = "ok"

        if metrics and metrics.internet_traffic_bytes and metrics.internet_traffic_bytes >= GB:
            cdn_bytes = metrics.cdn_traffic_bytes or 0.0
            cdn = "risk" if cdn_bytes < 0.5 * metrics.internet_traffic_bytes else "on"

        return ConfigLights(
            lifecycle=lifecycle,
            fragments=fragments,
            cdn=cdn,
            versioning=versioning,
            backup=backup,
        )

    def health_for(self, bucket: str) -> list[dict[str, Any]]:
        cfg = self.snapshot.config.by_bucket.get(bucket) if self.snapshot.config else None
        metrics = self.snapshot.monitor.by_bucket.get(bucket) if self.snapshot.monitor else None
        std_gb = _std_gb(metrics)
        mpu_gb = _mpu_gb(metrics)
        has_inv = bool(cfg and cfg.inventory_ids)

        if cfg is None:
            lifecycle_status, lifecycle_summary, lifecycle_rules = (
                "none",
                "未探测到生命周期（无 GetBucketLifecycle 结果）。",
                [],
            )
            abort_status, abort_summary = "none", "未探测碎片中止规则。"
            ver_status, ver_summary = "none", "未探测版本控制。"
        else:
            if not cfg.has_storage_transition() and std_gb >= 1:
                lifecycle_status = "risk"
                lifecycle_summary = f"无 Transition，标准约 {std_gb:.0f} GB → R01/R02"
                lifecycle_rules = ["R01", "R02"]
            elif cfg.has_storage_transition():
                lifecycle_status = "ok"
                lifecycle_summary = f"已配置 {len(cfg.rules)} 条生命周期规则"
                lifecycle_rules = []
            else:
                lifecycle_status = "none"
                lifecycle_summary = "无 Transition 规则"
                lifecycle_rules = ["R02"]
            if mpu_gb >= 1 and not cfg.has_abort():
                abort_status = "risk"
                abort_summary = f"无 Abort，碎片 {mpu_gb:.1f} GB → R03"
            elif cfg.has_abort():
                abort_status = "ok"
                abort_summary = "已配置 AbortIncompleteMultipartUpload"
            else:
                abort_status = "none"
                abort_summary = "无碎片或未配置 Abort"
            if cfg.versioning_enabled() and not cfg.has_noncurrent_rule():
                ver_status = "risk"
                ver_summary = "已开版本控制，无 Noncurrent* 规则 → R04"
            elif cfg.versioning_enabled():
                ver_status = "ok"
                ver_summary = "版本控制已开，且有非当前版本规则"
            else:
                ver_status = "ok"
                ver_summary = "未开启版本控制"

        return [
            {
                "key": "lifecycle",
                "title": "生命周期 Transition",
                "status": lifecycle_status,
                "summary": lifecycle_summary,
                "rule_ids": lifecycle_rules,
            },
            {
                "key": "abort",
                "title": "删除碎片 Abort",
                "status": abort_status,
                "summary": abort_summary,
                "rule_ids": ["R03"] if abort_status == "risk" else [],
            },
            {
                "key": "versioning",
                "title": "非当前版本",
                "status": ver_status,
                "summary": ver_summary,
                "rule_ids": ["R04"] if ver_status == "risk" else [],
            },
            {
                "key": "inventory",
                "title": "清单",
                "status": "ok" if has_inv else "none",
                "summary": (
                    "已开清单配置；M3 不读 CSV，对象级建议仍关闭"
                    if has_inv
                    else "无清单，对象级建议不可用。不要对全桶 List Objects。"
                ),
                "rule_ids": [],
            },
            {
                "key": "replication",
                "title": "复制",
                "status": "none",
                "summary": "M3 未探测 CRR（未调用写接口）。",
                "rule_ids": [],
            },
            {
                "key": "access",
                "title": "访问追踪",
                "status": "none",
                "summary": "未开启或未探测；R01 按桶级保守观察，置信度下调。",
                "rule_ids": ["R01"],
            },
            {
                "key": "intelligent",
                "title": "智能分层",
                "status": "none",
                "summary": "未开启。R08 金额不计入可优化 KPI。",
                "rule_ids": [],
            },
        ]


def _std_gb(metrics: MonitorBucketMetrics | None) -> float:
    if metrics is None or not metrics.standard_bytes:
        return 0.0
    return float(metrics.standard_bytes) / GB


def _mpu_gb(metrics: MonitorBucketMetrics | None) -> float:
    if metrics is None or not metrics.multipart_storage_bytes:
        return 0.0
    return float(metrics.multipart_storage_bytes) / GB
