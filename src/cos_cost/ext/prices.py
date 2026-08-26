"""单价：优先账单 RealTotalCost / 用量，否则 刊例。"""

from __future__ import annotations

from dataclasses import dataclass

from cos_cost.billing_items import classify_product
from cos_cost.models import CollectSnapshot, MonitorBucketMetrics

GB = 1_000_000_000.0
WAN = 10_000.0

# 中国大陆 COS 刊例（元/GB/月）。用于账单拆不出用量时，证据里标注「刊例」。
LIST_CNY_PER_GB_MONTH = {
    "STANDARD": 0.118,
    "STANDARD_IA": 0.080,
    "ARCHIVE": 0.033,
    "DEEP_ARCHIVE": 0.012,
}
# 读请求刊例：元 / 万次
LIST_GET_CNY_PER_WAN = 0.01


@dataclass(frozen=True)
class Price:
    value: float
    basis: str  # "bill" | "刊例"

    def label(self) -> str:
        return "账单单价" if self.basis == "bill" else "刊例"


@dataclass
class BucketPrices:
    p_std: Price
    p_ia: Price
    p_archive: Price
    p_deep: Price
    p_get_wan: Price
    p_traffic_gb: Price | None = None


def bytes_to_gb(nbytes: float | None) -> float | None:
    if nbytes is None:
        return None
    return float(nbytes) / GB


def list_price(storage_class: str) -> Price:
    key = (storage_class or "STANDARD").upper().replace(" ", "_")
    if key in {"IA", "SIA", "STANDARD-IA"}:
        key = "STANDARD_IA"
    if key in {"ARC", "ARCHIVE_STORAGE"}:
        key = "ARCHIVE"
    if key in {"DEEP", "DEEP_ARC", "DEEPARCHIVE"}:
        key = "DEEP_ARCHIVE"
    return Price(LIST_CNY_PER_GB_MONTH.get(key, LIST_CNY_PER_GB_MONTH["STANDARD"]), "刊例")


def prices_for(snapshot: CollectSnapshot, bucket: str) -> BucketPrices:
    rows = [r for r in snapshot.bill_resources if r.resource_id == bucket]
    metrics = snapshot.monitor.by_bucket.get(bucket) if snapshot.monitor else None
    std_gb = bytes_to_gb(metrics.standard_bytes) if metrics else None
    ia_gb = None
    if metrics:
        ia_bytes = (metrics.sia_storage_bytes or 0) + (metrics.maz_ia_storage_bytes or 0)
        ia_gb = ia_bytes / GB if ia_bytes else None
        if ia_gb == 0:
            ia_gb = None
    arc_gb = bytes_to_gb(metrics.arc_storage_bytes) if metrics else None
    deep_gb = bytes_to_gb(metrics.deep_arc_storage_bytes) if metrics else None
    traffic_gb = bytes_to_gb(metrics.internet_traffic_bytes) if metrics else None

    std_cost = _sum_cost(rows, ("storage",), name_needles=("std", "标准"))
    ia_cost = _sum_cost(rows, ("storage",), name_needles=("sia", "ia", "低频"))
    # 避免把标准行算进低频：标准优先
    if ia_cost and std_cost and ia_cost == std_cost:
        ia_cost = None
    arc_cost = _sum_cost(rows, ("storage",), name_needles=("arc", "归档"))
    deep_cost = _sum_cost(rows, ("storage",), name_needles=("deep", "深度"))
    req_cost = _sum_cost(rows, ("request",))
    traffic_cost = _sum_cost(rows, ("traffic",))

    p_std = _unit(std_cost, std_gb, "STANDARD")
    p_ia = _unit(ia_cost, ia_gb, "STANDARD_IA")
    p_arc = _unit(arc_cost, arc_gb, "ARCHIVE")
    p_deep = _unit(deep_cost, deep_gb, "DEEP_ARCHIVE")
    p_get = _get_price(req_cost, metrics)
    p_traffic = _unit(traffic_cost, traffic_gb, "STANDARD")
    if traffic_cost is None or traffic_gb is None or traffic_gb <= 0:
        p_traffic_opt = None
    else:
        p_traffic_opt = Price(traffic_cost / traffic_gb, "bill")
    _ = p_traffic
    return BucketPrices(
        p_std=p_std,
        p_ia=p_ia,
        p_archive=p_arc,
        p_deep=p_deep,
        p_get_wan=p_get,
        p_traffic_gb=p_traffic_opt,
    )


def _unit(cost: float | None, usage_gb: float | None, storage_class: str) -> Price:
    if cost is not None and usage_gb and usage_gb > 0 and cost >= 0:
        return Price(cost / usage_gb, "bill")
    return list_price(storage_class)


def _get_price(req_cost: float | None, metrics: MonitorBucketMetrics | None) -> Price:
    if req_cost is not None and metrics:
        total = (metrics.get_requests or 0) + (metrics.put_requests or 0)
        if total > 0:
            return Price(req_cost / (total / WAN), "bill")
    return Price(LIST_GET_CNY_PER_WAN, "刊例")


def _sum_cost(
    rows, categories: tuple[str, ...], name_needles: tuple[str, ...] | None = None
) -> float | None:
    total = 0.0
    saw = False
    for row in rows:
        if row.real_total_cost is None:
            continue
        cat = classify_product(row.product_code, row.product_code_name)
        if cat not in categories:
            continue
        blob = f"{row.product_code or ''} {row.product_code_name or ''}".lower()
        if name_needles:
            if cat == "storage" and not any(n in blob for n in name_needles):
                continue
            # 标准存储不要命中 ia/arc
            if "std" in name_needles or "标准" in name_needles:
                if any(n in blob for n in ("sia", "低频", "arc", "归档", "deep", "深度")):
                    if "std" not in blob and "标准" not in blob:
                        continue
        total += row.real_total_cost
        saw = True
    return total if saw else None
