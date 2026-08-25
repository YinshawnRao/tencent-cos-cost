"""把腾讯云 SDK / JSON 字段防御性映射到领域模型。不确定的字段保留 raw。"""

from __future__ import annotations

from typing import Any

from cos_cost import COS_BUSINESS_CODE
from cos_cost.models import BillResourceRow, BillSummary, BucketInfo


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_ready(value: Any) -> int:
    parsed = parse_int(value)
    return 1 if parsed == 1 else 0


def coerce_bucket_entries(raw_buckets: Any) -> list[dict[str, Any]]:
    if raw_buckets is None:
        return []
    if isinstance(raw_buckets, dict):
        inner = raw_buckets.get("Bucket", raw_buckets.get("bucket", raw_buckets))
        return coerce_bucket_entries(inner)
    if isinstance(raw_buckets, list):
        out: list[dict[str, Any]] = []
        for item in raw_buckets:
            if isinstance(item, dict):
                out.append(item)
        return out
    return []


def parse_buckets(payload: dict[str, Any]) -> tuple[str | None, list[BucketInfo]]:
    owner = payload.get("Owner") or payload.get("owner") or {}
    owner_id = None
    if isinstance(owner, dict):
        owner_id = owner.get("ID") or owner.get("Id") or owner.get("id")
        if owner_id is not None:
            owner_id = str(owner_id)
    buckets_node = payload.get("Buckets") or payload.get("buckets") or payload.get("Bucket")
    infos: list[BucketInfo] = []
    for item in coerce_bucket_entries(buckets_node):
        name = item.get("Name") or item.get("name")
        if not name:
            continue
        region = (
            item.get("Location")
            or item.get("location")
            or item.get("Region")
            or item.get("region")
        )
        infos.append(
            BucketInfo(
                name=str(name),
                region=str(region) if region else None,
                creation_date=_as_str(item.get("CreationDate") or item.get("creation_date")),
                raw=item,
            )
        )
    return owner_id, infos


def parse_bill_summary_by_product(month: str, payload: dict[str, Any]) -> BillSummary:
    ready = parse_ready(payload.get("Ready"))
    overview = payload.get("SummaryOverview") or []
    if not isinstance(overview, list):
        overview = []
    cos_item: dict[str, Any] = {}
    for item in overview:
        if not isinstance(item, dict):
            continue
        code = item.get("BusinessCode") or item.get("business_code")
        if str(code) == COS_BUSINESS_CODE:
            cos_item = item
            break
    total = payload.get("SummaryTotal") or {}
    all_total = None
    if isinstance(total, dict):
        all_total = parse_float(total.get("RealTotalCost"))
    return BillSummary(
        month=month,
        ready=ready,
        cos_real_total_cost=parse_float(cos_item.get("RealTotalCost")) if cos_item else None,
        all_products_real_total_cost=all_total,
        source_api="DescribeBillSummaryByProduct",
        raw_cos_item=cos_item,
        raw=payload,
    )


def parse_cached_bill_summary(month: str, payload: dict[str, Any]) -> BillSummary:
    """按缓存里的响应形状选择官方字段解析器。"""
    inner = payload.get("response") if isinstance(payload.get("response"), dict) else payload
    if "SummaryOverview" in inner:
        return parse_bill_summary_by_product(month, inner)
    if "SummaryDetail" in inner:
        return parse_bill_summary_fallback(month, inner)
    return parse_bill_summary_by_product(month, inner)


def parse_bill_summary_fallback(month: str, payload: dict[str, Any]) -> BillSummary:
    """DescribeBillSummary GroupType=business：GroupKey=p_cos。"""
    ready = parse_ready(payload.get("Ready"))
    details = payload.get("SummaryDetail") or []
    if not isinstance(details, list):
        details = []
    cos_item: dict[str, Any] = {}
    all_total = 0.0
    saw_total = False
    for item in details:
        if not isinstance(item, dict):
            continue
        cost = parse_float(item.get("RealTotalCost"))
        if cost is not None:
            all_total += cost
            saw_total = True
        key = item.get("GroupKey") or item.get("BusinessCode")
        if str(key) == COS_BUSINESS_CODE:
            cos_item = item
    return BillSummary(
        month=month,
        ready=ready,
        cos_real_total_cost=parse_float(cos_item.get("RealTotalCost")) if cos_item else None,
        all_products_real_total_cost=all_total if saw_total else None,
        source_api="DescribeBillSummary",
        raw_cos_item=cos_item,
        raw=payload,
    )


def parse_bill_resource(item: dict[str, Any]) -> BillResourceRow:
    resource_id = _as_str(item.get("ResourceId") or item.get("resource_id")) or ""
    return BillResourceRow(
        resource_id=resource_id,
        resource_name=_as_str(item.get("ResourceName") or item.get("resource_name")),
        business_code=_as_str(item.get("BusinessCode") or item.get("business_code")),
        product_code=_as_str(item.get("ProductCode") or item.get("product_code")),
        product_code_name=_as_str(item.get("ProductCodeName") or item.get("product_code_name")),
        region_name=_as_str(item.get("RegionName") or item.get("region_name")),
        region_id=parse_int(item.get("RegionId") if "RegionId" in item else item.get("region_id")),
        real_total_cost=parse_float(item.get("RealTotalCost") or item.get("real_total_cost")),
        total_cost=parse_float(item.get("TotalCost") or item.get("total_cost")),
        cash_pay_amount=parse_float(item.get("CashPayAmount") or item.get("cash_pay_amount")),
        owner_uin=_as_str(item.get("OwnerUin") or item.get("owner_uin")),
        payer_uin=_as_str(item.get("PayerUin") or item.get("payer_uin")),
        raw=item,
    )


def is_cos_business(row: BillResourceRow) -> bool:
    if row.business_code is None:
        # 防御：调用时已按 BusinessCode=p_cos 过滤；空则看 raw / 产品名。
        raw_code = (row.raw.get("BusinessCode") or row.raw.get("business_code") or "")
        name = str(row.raw.get("BusinessCodeName") or "")
        if str(raw_code) == COS_BUSINESS_CODE:
            return True
        return "对象存储" in name or name.upper() == "COS" or "cloud object storage" in name.lower()
    return row.business_code == COS_BUSINESS_CODE


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
