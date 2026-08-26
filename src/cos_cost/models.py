"""M1 领域模型。字段名对齐腾讯云账单 SDK（RealTotalCost / Ready / BusinessCode）。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any


def _to_plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _to_plain(v) for k, v in asdict(value).items()}
    if isinstance(value, list):
        return [_to_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    return value


@dataclass
class BucketInfo:
    name: str
    region: str | None
    creation_date: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class BillSummary:
    """DescribeBillSummaryByProduct / DescribeBillSummary 的归一化结果。"""

    month: str
    ready: int
    cos_real_total_cost: float | None
    all_products_real_total_cost: float | None
    source_api: str
    raw_cos_item: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def estimated(self) -> bool:
        return int(self.ready) != 1


@dataclass
class BillResourceRow:
    """DescribeBillResourceSummary.ResourceSummarySet 的一行。"""

    resource_id: str
    resource_name: str | None
    business_code: str | None
    product_code: str | None
    product_code_name: str | None
    region_name: str | None
    region_id: int | None
    real_total_cost: float | None
    total_cost: float | None
    cash_pay_amount: float | None
    owner_uin: str | None
    payer_uin: str | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class MonitorBucketMetrics:
    """单桶监控聚合。存储类指标取 last，流量类取 sum。单位已换算为字节。"""

    bucket: str
    std_storage_bytes: float | None = None
    maz_std_storage_bytes: float | None = None
    sia_storage_bytes: float | None = None
    maz_ia_storage_bytes: float | None = None
    arc_storage_bytes: float | None = None
    deep_arc_storage_bytes: float | None = None
    internet_traffic_bytes: float | None = None
    multipart_storage_bytes: float | None = None
    internal_traffic_bytes: float | None = None
    cdn_traffic_bytes: float | None = None
    get_requests: float | None = None
    put_requests: float | None = None
    err_4xx: float | None = None
    err_5xx: float | None = None
    dates: list[str] = field(default_factory=list)
    daily: dict[str, list[float | None]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def standard_bytes(self) -> float | None:
        parts = [self.std_storage_bytes, self.maz_std_storage_bytes]
        present = [p for p in parts if p is not None]
        if not present:
            return None
        return float(sum(present))

    @property
    def capacity_bytes(self) -> float | None:
        parts = [
            self.std_storage_bytes,
            self.maz_std_storage_bytes,
            self.sia_storage_bytes,
            self.maz_ia_storage_bytes,
            self.arc_storage_bytes,
            self.deep_arc_storage_bytes,
        ]
        present = [p for p in parts if p is not None]
        if not present:
            return None
        return float(sum(present))

    @property
    def standard_pct(self) -> float | None:
        capacity = self.capacity_bytes
        standard = self.standard_bytes
        if capacity is None or standard is None or capacity <= 0:
            return None
        return 100.0 * standard / capacity


@dataclass
class MonitorSnapshot:
    by_bucket: dict[str, MonitorBucketMetrics] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class LifecycleTransition:
    days: int | None = None
    storage_class: str | None = None


@dataclass
class LifecycleRule:
    rule_id: str | None = None
    status: str | None = None
    prefix: str | None = None
    abort_days: int | None = None
    transitions: list[LifecycleTransition] = field(default_factory=list)
    noncurrent_transitions: list[LifecycleTransition] = field(default_factory=list)
    noncurrent_expiration_days: int | None = None
    expiration_days: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return (self.status or "Enabled").lower() == "enabled"


@dataclass
class BucketConfig:
    """只读配置：GetBucketLifecycle / Versioning / Inventory / Logging。"""

    bucket: str
    region: str | None = None
    versioning: str | None = None
    rules: list[LifecycleRule] = field(default_factory=list)
    inventory_ids: list[str] = field(default_factory=list)
    inventory_dest_buckets: list[str] = field(default_factory=list)
    logging_dest_bucket: str | None = None
    notes: list[str] = field(default_factory=list)

    def has_abort(self) -> bool:
        return any(rule.abort_days is not None for rule in self.rules if rule.enabled)

    def has_storage_transition(self) -> bool:
        classes = {
            "STANDARD_IA",
            "IA",
            "ARCHIVE",
            "DEEP_ARCHIVE",
            "INTELLIGENT_TIERING",
        }
        for rule in self.rules:
            if not rule.enabled:
                continue
            for item in rule.transitions:
                token = (item.storage_class or "").upper().replace(" ", "_")
                if token in classes or token.endswith("_IA"):
                    return True
        return False

    def has_noncurrent_rule(self) -> bool:
        for rule in self.rules:
            if not rule.enabled:
                continue
            if rule.noncurrent_expiration_days is not None or rule.noncurrent_transitions:
                return True
        return False

    def versioning_enabled(self) -> bool:
        return (self.versioning or "").lower() == "enabled"


@dataclass
class ConfigSnapshot:
    by_bucket: dict[str, BucketConfig] = field(default_factory=dict)
    extra_buckets: list[BucketInfo] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class ConfigLights:
    """配置灯：生命周期 / 碎片 / CDN / 版本 / 备份。unknown = 未探测。"""

    lifecycle: str = "unknown"
    fragments: str = "unknown"
    cdn: str = "unknown"
    versioning: str = "unknown"
    backup: str = "unknown"

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class OpportunityHint:
    """优化机会占位（M3 规则引擎）。"""

    amount: float | None = None
    count: int = 0
    items: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RankingRow:
    bucket: str
    region: str | None
    payable: float | None
    mom_pct: float | None
    capacity_bytes: float | None
    standard_pct: float | None
    internet_traffic_bytes: float | None
    opportunity_amount: float | None
    opportunity_count: int
    config_lights: ConfigLights
    raw_resource_ids: list[str] = field(default_factory=list)
    raw_resource_names: list[str] = field(default_factory=list)


@dataclass
class Kpis:
    cos_payable: float | None
    mom_pct: float | None
    yoy_pct: float | None
    optimizable_amount: float | None
    standard_storage_pct: float | None
    internet_traffic_bytes: float | None
    request_fee: float | None
    ready: int | None
    bucket_listed: int
    bucket_with_bill: int


@dataclass
class RankingResult:
    month: str
    account_key: str
    ready: int | None
    estimated: bool
    kpis: Kpis
    rows: list[RankingRow]
    notes: list[str] = field(default_factory=list)
    mock: bool = False

    def to_dict(self) -> dict[str, Any]:
        return _to_plain(self)


@dataclass
class CollectSnapshot:
    account_key: str
    month: str
    buckets: list[BucketInfo]
    bill_summary: BillSummary | None
    prev_bill_summary: BillSummary | None
    yoy_bill_summary: BillSummary | None
    bill_resources: list[BillResourceRow]
    prev_bill_resources: list[BillResourceRow]
    monitor: MonitorSnapshot | None
    notes: list[str]
    collected_at: str
    mock: bool = False
    cache_hits: list[str] = field(default_factory=list)
    config: ConfigSnapshot | None = None

    def to_dict(self) -> dict[str, Any]:
        return _to_plain(self)
