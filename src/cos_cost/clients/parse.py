"""把腾讯云 SDK / JSON 字段防御性映射到领域模型。不确定的字段保留 raw。"""

from __future__ import annotations

from typing import Any

from cos_cost import COS_BUSINESS_CODE
from cos_cost.models import (
    BillResourceRow,
    BillSummary,
    BucketConfig,
    BucketInfo,
    LifecycleRule,
    LifecycleTransition,
)


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


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    lower = {str(k).lower(): v for k, v in data.items()}
    for key in keys:
        if key.lower() in lower and lower[key.lower()] not in (None, ""):
            return lower[key.lower()]
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def parse_dest_bucket(raw: str | None) -> str | None:
    """从 TargetBucket / qcs ARN 取出桶名。不含对象 Key。"""
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if "::" in text:
        text = text.split("::")[-1]
    text = text.split("/")[0].strip()
    return text or None


def parse_lifecycle_payload(payload: Any) -> list[LifecycleRule]:
    if not isinstance(payload, dict):
        return []
    rules_node = _first(payload, "Rule", "Rules", "rule", "rules") or payload.get("Rule")
    rules: list[LifecycleRule] = []
    for item in _as_list(rules_node):
        if not isinstance(item, dict):
            continue
        filt = _first(item, "Filter", "filter") or {}
        prefix = None
        if isinstance(filt, dict):
            prefix = _as_str(_first(filt, "Prefix", "prefix"))
            and_node = _first(filt, "And", "and")
            if prefix is None and isinstance(and_node, dict):
                prefix = _as_str(_first(and_node, "Prefix", "prefix"))
        abort = _first(item, "AbortIncompleteMultipartUpload", "abortIncompleteMultipartUpload") or {}
        abort_days = None
        if isinstance(abort, dict):
            abort_days = parse_int(
                _first(abort, "DaysAfterInitiation", "Days", "daysAfterInitiation")
            )
        transitions: list[LifecycleTransition] = []
        for node in _as_list(_first(item, "Transition", "Transitions")):
            if isinstance(node, dict):
                transitions.append(
                    LifecycleTransition(
                        days=parse_int(_first(node, "Days", "days")),
                        storage_class=_as_str(_first(node, "StorageClass", "storageClass")),
                    )
                )
        noncurrent_transitions: list[LifecycleTransition] = []
        for node in _as_list(
            _first(item, "NoncurrentVersionTransition", "NoncurrentVersionTransitions")
        ):
            if isinstance(node, dict):
                noncurrent_transitions.append(
                    LifecycleTransition(
                        days=parse_int(
                            _first(node, "NoncurrentDays", "NoncurrentDays", "Days", "days")
                        ),
                        storage_class=_as_str(_first(node, "StorageClass", "storageClass")),
                    )
                )
        ncv_exp = _first(item, "NoncurrentVersionExpiration", "noncurrentVersionExpiration")
        ncv_days = None
        if isinstance(ncv_exp, dict):
            ncv_days = parse_int(_first(ncv_exp, "NoncurrentDays", "Days"))
        exp = _first(item, "Expiration", "expiration")
        exp_days = None
        if isinstance(exp, dict):
            exp_days = parse_int(_first(exp, "Days", "days"))
        rules.append(
            LifecycleRule(
                rule_id=_as_str(_first(item, "ID", "Id", "id")),
                status=_as_str(_first(item, "Status", "status")),
                prefix=prefix,
                abort_days=abort_days,
                transitions=transitions,
                noncurrent_transitions=noncurrent_transitions,
                noncurrent_expiration_days=ncv_days,
                expiration_days=exp_days,
                raw=item,
            )
        )
    return rules


def parse_versioning_status(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    status = _first(payload, "Status", "status")
    return _as_str(status)


def parse_logging_dest(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    enabled = _first(payload, "LoggingEnabled", "loggingEnabled") or payload
    if not isinstance(enabled, dict):
        return None
    return parse_dest_bucket(_as_str(_first(enabled, "TargetBucket", "targetBucket")))


def parse_inventory_configs(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    node = (
        _first(
            payload,
            "InventoryConfiguration",
            "InventoryConfigurations",
            "InventoryConfigurationList",
        )
        or payload.get("InventoryConfiguration")
    )
    if isinstance(node, dict) and _first(node, "Id", "ID", "Destination"):
        return [node]
    return [item for item in _as_list(node) if isinstance(item, dict)]


def inventory_dest_buckets(configs: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    ids: list[str] = []
    dests: list[str] = []
    for item in configs:
        inv_id = _as_str(_first(item, "Id", "ID", "id"))
        if inv_id and inv_id not in ids:
            ids.append(inv_id)
        dest = _first(item, "Destination", "destination") or {}
        bucket_raw = None
        if isinstance(dest, dict):
            cos_dest = _first(dest, "COSBucketDestination", "BucketDestination", "COSBucket") or dest
            if isinstance(cos_dest, dict):
                bucket_raw = _first(cos_dest, "Bucket", "bucket", "AccountId")
                if bucket_raw and str(bucket_raw).isdigit():
                    bucket_raw = _first(cos_dest, "Bucket", "bucket")
            elif isinstance(cos_dest, str):
                bucket_raw = cos_dest
        name = parse_dest_bucket(_as_str(bucket_raw) if bucket_raw is not None else None)
        if name and name not in dests:
            dests.append(name)
    return ids, dests


def parse_bucket_config(
    bucket: str,
    region: str | None,
    *,
    lifecycle: Any = None,
    versioning: Any = None,
    logging: Any = None,
    inventory: Any = None,
    notes: list[str] | None = None,
) -> BucketConfig:
    ids, dests = inventory_dest_buckets(parse_inventory_configs(inventory))
    return BucketConfig(
        bucket=bucket,
        region=region,
        versioning=parse_versioning_status(versioning),
        rules=parse_lifecycle_payload(lifecycle),
        inventory_ids=ids,
        inventory_dest_buckets=dests,
        logging_dest_bucket=parse_logging_dest(logging),
        notes=list(notes or []),
    )


def config_snapshot_to_payload(by_bucket: dict[str, BucketConfig]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, cfg in by_bucket.items():
        out[name] = {
            "bucket": cfg.bucket,
            "region": cfg.region,
            "versioning": cfg.versioning,
            "inventory_ids": cfg.inventory_ids,
            "inventory_dest_buckets": cfg.inventory_dest_buckets,
            "logging_dest_bucket": cfg.logging_dest_bucket,
            "notes": cfg.notes,
            "lifecycle": {"Rule": [rule.raw for rule in cfg.rules if rule.raw]},
            "versioning_raw": {"Status": cfg.versioning} if cfg.versioning else {},
            "logging_raw": (
                {"LoggingEnabled": {"TargetBucket": cfg.logging_dest_bucket}}
                if cfg.logging_dest_bucket
                else {}
            ),
            "inventory_raw": [
                {"Id": i, "Destination": {"Bucket": d}}
                for i, d in zip(cfg.inventory_ids, cfg.inventory_dest_buckets)
            ]
            or [{"Destination": {"Bucket": d}} for d in cfg.inventory_dest_buckets],
        }
        if cfg.rules and not out[name]["lifecycle"]["Rule"]:
            # 无 raw 时仍要能从缓存还原规则字段。
            out[name]["lifecycle_rules"] = [
                {
                    "ID": r.rule_id,
                    "Status": r.status,
                    "Filter": {"Prefix": r.prefix or ""},
                    "AbortIncompleteMultipartUpload": (
                        {"DaysAfterInitiation": r.abort_days} if r.abort_days is not None else None
                    ),
                    "Transition": [
                        {"Days": t.days, "StorageClass": t.storage_class} for t in r.transitions
                    ],
                    "NoncurrentVersionExpiration": (
                        {"NoncurrentDays": r.noncurrent_expiration_days}
                        if r.noncurrent_expiration_days is not None
                        else None
                    ),
                    "NoncurrentVersionTransition": [
                        {"NoncurrentDays": t.days, "StorageClass": t.storage_class}
                        for t in r.noncurrent_transitions
                    ],
                    "Expiration": (
                        {"Days": r.expiration_days} if r.expiration_days is not None else None
                    ),
                }
                for r in cfg.rules
            ]
    return {"by_bucket": out}


def parse_cached_config(payload: dict[str, Any]) -> dict[str, BucketConfig]:
    raw_map = payload.get("by_bucket") or {}
    if not isinstance(raw_map, dict):
        return {}
    out: dict[str, BucketConfig] = {}
    for name, item in raw_map.items():
        if not isinstance(item, dict):
            continue
        lifecycle = item.get("lifecycle") or {}
        if item.get("lifecycle_rules") and not (isinstance(lifecycle, dict) and lifecycle.get("Rule")):
            lifecycle = {"Rule": [r for r in item.get("lifecycle_rules") or [] if isinstance(r, dict)]}
        out[str(name)] = parse_bucket_config(
            str(item.get("bucket") or name),
            _as_str(item.get("region")),
            lifecycle=lifecycle,
            versioning=item.get("versioning_raw") or {"Status": item.get("versioning")},
            logging=item.get("logging_raw")
            or (
                {"LoggingEnabled": {"TargetBucket": item.get("logging_dest_bucket")}}
                if item.get("logging_dest_bucket")
                else {}
            ),
            inventory=item.get("inventory_raw")
            or [
                {"Id": i, "Destination": {"Bucket": d}}
                for i, d in zip(item.get("inventory_ids") or [], item.get("inventory_dest_buckets") or [])
            ],
            notes=[str(n) for n in (item.get("notes") or [])],
        )
    return out
