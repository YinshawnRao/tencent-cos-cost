"""拉取并缓存：桶列表、账单汇总、按资源汇总、监控。"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from cos_cost.cache import (
    BUCKET_LIST_TTL,
    CONFIG_TTL,
    ESTIMATED_BILL_TTL,
    MONITOR_TTL,
    FileCache,
)
from cos_cost.clients.errors import PermissionDeniedError
from cos_cost.clients.parse import (
    config_snapshot_to_payload,
    parse_bill_resource,
    parse_buckets,
    parse_bucket_config,
    parse_cached_bill_summary,
    parse_cached_config,
)
from cos_cost.clients.protocols import ClientBundle
from cos_cost.models import (
    BillResourceRow,
    BillSummary,
    BucketInfo,
    CollectSnapshot,
    ConfigSnapshot,
    MonitorBucketMetrics,
    MonitorSnapshot,
)
from cos_cost.monthutil import shift_month
from cos_cost.secrets import Credentials


def collect(
    bundle: ClientBundle,
    month: str,
    cache: FileCache,
    *,
    force: bool = False,
    creds: Credentials | None = None,
) -> CollectSnapshot:
    notes: list[str] = []
    cache_hits: list[str] = []
    secret_key = creds.secret_key if creds else None

    account_key, buckets, bucket_note, bucket_hit = _load_buckets(
        bundle, cache, force=force, secret_key=secret_key
    )
    if bucket_note:
        notes.append(bucket_note)
    if bucket_hit:
        cache_hits.append("buckets")
    if bundle.account_key and bundle.account_key != "unknown":
        account_key = bundle.account_key
    env_appid = (os.environ.get("COS_APPID") or "").strip()
    if (not account_key or account_key == "unknown") and env_appid:
        account_key = env_appid

    bill_summary, bill_note, bill_hit = _load_bill_summary(
        bundle, cache, account_key, month, force=force, secret_key=secret_key
    )
    if bill_note:
        notes.append(bill_note)
    if bill_hit:
        cache_hits.append(f"bill_summary:{month}")

    prev_month = shift_month(month, -1)
    prev_summary, _, prev_sum_hit = _load_bill_summary(
        bundle, cache, account_key, prev_month, force=force, secret_key=secret_key
    )
    if prev_sum_hit:
        cache_hits.append(f"bill_summary:{prev_month}")

    yoy_month = shift_month(month, -12)
    yoy_summary, _, yoy_hit = _load_bill_summary(
        bundle, cache, account_key, yoy_month, force=force, secret_key=secret_key
    )
    if yoy_hit:
        cache_hits.append(f"bill_summary:{yoy_month}")

    resources, res_note, res_hit = _load_resources(
        bundle, cache, account_key, month, force=force, secret_key=secret_key
    )
    if res_note:
        notes.append(res_note)
    if res_hit:
        cache_hits.append(f"bill_resources:{month}")

    prev_resources, _, prev_res_hit = _load_resources(
        bundle, cache, account_key, prev_month, force=force, secret_key=secret_key
    )
    if prev_res_hit:
        cache_hits.append(f"bill_resources:{prev_month}")

    names = [b.name for b in buckets]
    extra_ids = _resource_ids_not_in_buckets(resources, names)

    config, cfg_note, cfg_hit = _load_config(
        bundle,
        cache,
        account_key,
        buckets,
        force=force,
        secret_key=secret_key,
    )
    if cfg_note:
        notes.append(cfg_note)
    if cfg_hit:
        cache_hits.append("bucket_config")

    monitor_names = list(dict.fromkeys(names + extra_ids + [b.name for b in config.extra_buckets]))

    monitor, mon_note, mon_hit = _load_monitor(
        bundle,
        cache,
        account_key,
        month,
        monitor_names,
        force=force,
        secret_key=secret_key,
    )
    if mon_note:
        notes.append(mon_note)
    if mon_hit:
        cache_hits.append(f"monitor:{month}")

    return CollectSnapshot(
        account_key=account_key,
        month=month,
        buckets=buckets,
        bill_summary=bill_summary,
        prev_bill_summary=prev_summary,
        yoy_bill_summary=yoy_summary,
        bill_resources=resources,
        prev_bill_resources=prev_resources,
        monitor=monitor,
        notes=notes,
        collected_at=datetime.now(timezone.utc).isoformat(),
        mock=bundle.mock,
        cache_hits=cache_hits,
        config=config,
    )


def _load_buckets(
    bundle: ClientBundle,
    cache: FileCache,
    *,
    force: bool,
    secret_key: str | None,
) -> tuple[str, list[BucketInfo], str | None, bool]:
    account_hint = bundle.account_key
    if not force:
        hit = cache.get(account_hint, "buckets", None, ttl=BUCKET_LIST_TTL)
        if hit and isinstance(hit.payload, dict):
            owner, buckets = parse_buckets(hit.payload)
            return owner or account_hint, buckets, None, True
    try:
        owner, buckets = bundle.cos.list_buckets()
    except PermissionDeniedError as exc:
        return account_hint, [], f"缺少 COS 列表权限（GetService）：桶列表为空。{exc}", False
    payload = {
        "Owner": {"ID": owner},
        "Buckets": {
            "Bucket": [
                {"Name": b.name, "Location": b.region, "CreationDate": b.creation_date}
                for b in buckets
            ]
        },
    }
    key = owner or account_hint
    cache.put(key, "buckets", None, payload, secret_key=secret_key)
    filled: list[BucketInfo] = []
    for bucket in buckets:
        region = bucket.region
        if not region:
            region = bundle.cos.head_bucket_region(bucket.name, None)
        filled.append(
            BucketInfo(
                name=bucket.name,
                region=region,
                creation_date=bucket.creation_date,
                raw=bucket.raw,
            )
        )
    return key, filled, None, False


def _load_bill_summary(
    bundle: ClientBundle,
    cache: FileCache,
    account_key: str,
    month: str,
    *,
    force: bool,
    secret_key: str | None,
) -> tuple[BillSummary | None, str | None, bool]:
    if not force:
        hit = cache.get(
            account_key,
            "bill_summary",
            month,
            ttl=ESTIMATED_BILL_TTL,
            immutable_if_ready=True,
        )
        if hit and isinstance(hit.payload, dict):
            return parse_cached_bill_summary(month, hit.payload), None, True
    try:
        summary = bundle.billing.describe_bill_summary_by_product(month)
    except PermissionDeniedError as exc:
        return None, f"缺少账单权限：应付列为空。{exc}", False
    cache.put(
        account_key,
        "bill_summary",
        month,
        summary.raw,
        ready=summary.ready,
        immutable=summary.ready == 1,
        secret_key=secret_key,
    )
    return summary, None, False


def _load_resources(
    bundle: ClientBundle,
    cache: FileCache,
    account_key: str,
    month: str,
    *,
    force: bool,
    secret_key: str | None,
) -> tuple[list[BillResourceRow], str | None, bool]:
    if not force:
        hit = cache.get(
            account_key,
            "bill_resources",
            month,
            ttl=ESTIMATED_BILL_TTL,
            immutable_if_ready=True,
        )
        if hit and isinstance(hit.payload, dict):
            items = hit.payload.get("ResourceSummarySet") or []
            rows = [parse_bill_resource(i) for i in items if isinstance(i, dict)]
            return rows, None, True
    try:
        rows = bundle.billing.describe_bill_resource_summary(month)
    except PermissionDeniedError as exc:
        return [], f"缺少 DescribeBillResourceSummary 权限：按桶应付为空。{exc}", False
    ready = None
    # 资源汇总接口本身无 Ready；沿用同月 summary 缓存的 Ready。
    summary_hit = cache.get(
        account_key, "bill_summary", month, ttl=None, immutable_if_ready=True
    )
    if summary_hit:
        ready = summary_hit.ready
    payload = {"ResourceSummarySet": [r.raw for r in rows], "Total": len(rows)}
    cache.put(
        account_key,
        "bill_resources",
        month,
        payload,
        ready=ready,
        immutable=ready == 1,
        secret_key=secret_key,
    )
    return rows, None, False


def _load_config(
    bundle: ClientBundle,
    cache: FileCache,
    account_key: str,
    buckets: list[BucketInfo],
    *,
    force: bool,
    secret_key: str | None,
) -> tuple[ConfigSnapshot, str | None, bool]:
    if not force:
        hit = cache.get(account_key, "bucket_config", None, ttl=CONFIG_TTL)
        if hit and isinstance(hit.payload, dict):
            by_bucket = parse_cached_config(hit.payload)
            extra = _extra_from_configs(by_bucket, {b.name for b in buckets})
            return ConfigSnapshot(by_bucket=by_bucket, extra_buckets=extra), None, True

    by_bucket: dict[str, Any] = {}
    notes: list[str] = []
    getter_lc = getattr(bundle.cos, "get_bucket_lifecycle", None)
    getter_ver = getattr(bundle.cos, "get_bucket_versioning", None)
    getter_log = getattr(bundle.cos, "get_bucket_logging", None)
    getter_inv = getattr(bundle.cos, "list_bucket_inventory", None)
    if not callable(getter_lc):
        snap = ConfigSnapshot(notes=["客户端无 GetBucketLifecycle，配置灯/R02–R10 降级。"])
        return snap, snap.notes[0], False

    for bucket in buckets:
        lc: Any = {}
        ver: Any = {}
        log: Any = {}
        inv: Any = []
        local_notes: list[str] = []
        try:
            lc = getter_lc(bucket.name, bucket.region)
        except PermissionDeniedError as exc:
            local_notes.append(f"无生命周期权限: {exc}")
            lc = {}
        except Exception as exc:  # noqa: BLE001 — 单桶降级，不中断采集
            local_notes.append(f"生命周期读取失败: {exc}")
            lc = {}
        if callable(getter_ver):
            try:
                ver = getter_ver(bucket.name, bucket.region)
            except PermissionDeniedError:
                local_notes.append("无版本控制读取权限")
                ver = {}
            except Exception as exc:  # noqa: BLE001
                local_notes.append(f"版本控制读取失败: {exc}")
                ver = {}
        if callable(getter_log):
            try:
                log = getter_log(bucket.name, bucket.region)
            except PermissionDeniedError:
                local_notes.append("无日志配置读取权限")
                log = {}
            except Exception as exc:  # noqa: BLE001
                local_notes.append(f"日志配置读取失败: {exc}")
                log = {}
        if callable(getter_inv):
            try:
                inv = getter_inv(bucket.name, bucket.region)
            except PermissionDeniedError:
                local_notes.append("无清单配置读取权限")
                inv = []
            except Exception as exc:  # noqa: BLE001
                local_notes.append(f"清单配置读取失败: {exc}")
                inv = []
        if not isinstance(lc, dict):
            lc = {}
        if not isinstance(ver, dict):
            ver = {}
        if not isinstance(log, dict):
            log = {}
        if not isinstance(inv, list):
            inv = [inv] if isinstance(inv, dict) else []
        by_bucket[bucket.name] = parse_bucket_config(
            bucket.name,
            bucket.region,
            lifecycle=lc,
            versioning=ver,
            logging=log,
            inventory=inv,
            notes=local_notes,
        )

    extra = _extra_from_configs(by_bucket, {b.name for b in buckets})
    snap = ConfigSnapshot(by_bucket=by_bucket, extra_buckets=extra, notes=notes)
    cache.put(
        account_key,
        "bucket_config",
        None,
        config_snapshot_to_payload(by_bucket),
        secret_key=secret_key,
    )
    note = None
    if extra:
        names = "、".join(b.name for b in extra)
        note = f"R12 清单/日志目标桶已纳入账号视图: {names}"
    return snap, note, False


def _extra_from_configs(by_bucket: dict, known: set[str]) -> list[BucketInfo]:
    extra: list[BucketInfo] = []
    seen = set(known)
    for cfg in by_bucket.values():
        dests = list(cfg.inventory_dest_buckets)
        if cfg.logging_dest_bucket:
            dests.append(cfg.logging_dest_bucket)
        for name in dests:
            if not name or name in seen:
                continue
            seen.add(name)
            extra.append(
                BucketInfo(
                    name=name,
                    region=cfg.region,
                    raw={"source": "inventory_or_logging", "via": cfg.bucket},
                )
            )
    return extra


def _load_monitor(
    bundle: ClientBundle,
    cache: FileCache,
    account_key: str,
    month: str,
    buckets: list[str],
    *,
    force: bool,
    secret_key: str | None,
) -> tuple[MonitorSnapshot | None, str | None, bool]:
    if not force:
        hit = cache.get(account_key, "monitor", month, ttl=MONITOR_TTL)
        if hit and isinstance(hit.payload, dict):
            return _monitor_from_payload(hit.payload), None, True
    try:
        snap = bundle.monitor.pull_cos_metrics(month, buckets)
    except PermissionDeniedError as exc:
        return (
            None,
            f"缺少监控权限（GetMonitorData）：容量 / 标准% / 外网列为空。{exc}",
            False,
        )
    payload = _monitor_to_payload(snap)
    cache.put(account_key, "monitor", month, payload, secret_key=secret_key)
    extra = "; ".join(snap.notes) if snap.notes else None
    return snap, extra, False


def _monitor_to_payload(snap: MonitorSnapshot) -> dict[str, Any]:
    out: dict[str, Any] = {"by_bucket": {}, "notes": snap.notes}
    for name, metrics in snap.by_bucket.items():
        out["by_bucket"][name] = {
            "std_storage_bytes": metrics.std_storage_bytes,
            "maz_std_storage_bytes": metrics.maz_std_storage_bytes,
            "sia_storage_bytes": metrics.sia_storage_bytes,
            "maz_ia_storage_bytes": metrics.maz_ia_storage_bytes,
            "arc_storage_bytes": metrics.arc_storage_bytes,
            "deep_arc_storage_bytes": metrics.deep_arc_storage_bytes,
            "internet_traffic_bytes": metrics.internet_traffic_bytes,
            "multipart_storage_bytes": metrics.multipart_storage_bytes,
            "internal_traffic_bytes": metrics.internal_traffic_bytes,
            "cdn_traffic_bytes": metrics.cdn_traffic_bytes,
            "get_requests": metrics.get_requests,
            "put_requests": metrics.put_requests,
            "err_4xx": metrics.err_4xx,
            "err_5xx": metrics.err_5xx,
            "dates": metrics.dates,
            "daily": metrics.daily,
        }
    return out


def _monitor_from_payload(payload: dict[str, Any]) -> MonitorSnapshot:
    by_bucket: dict[str, MonitorBucketMetrics] = {}
    raw_map = payload.get("by_bucket") or {}
    if isinstance(raw_map, dict):
        for name, item in raw_map.items():
            if not isinstance(item, dict):
                continue
            by_bucket[str(name)] = MonitorBucketMetrics(
                bucket=str(name),
                std_storage_bytes=_f(item.get("std_storage_bytes")),
                maz_std_storage_bytes=_f(item.get("maz_std_storage_bytes")),
                sia_storage_bytes=_f(item.get("sia_storage_bytes")),
                maz_ia_storage_bytes=_f(item.get("maz_ia_storage_bytes")),
                arc_storage_bytes=_f(item.get("arc_storage_bytes")),
                deep_arc_storage_bytes=_f(item.get("deep_arc_storage_bytes")),
                internet_traffic_bytes=_f(item.get("internet_traffic_bytes")),
                multipart_storage_bytes=_f(item.get("multipart_storage_bytes")),
                internal_traffic_bytes=_f(item.get("internal_traffic_bytes")),
                cdn_traffic_bytes=_f(item.get("cdn_traffic_bytes")),
                get_requests=_f(item.get("get_requests")),
                put_requests=_f(item.get("put_requests")),
                err_4xx=_f(item.get("err_4xx")),
                err_5xx=_f(item.get("err_5xx")),
                dates=list(item.get("dates") or []),
                daily=dict(item.get("daily") or {}),
            )
    notes = payload.get("notes") or []
    if not isinstance(notes, list):
        notes = []
    return MonitorSnapshot(by_bucket=by_bucket, notes=[str(n) for n in notes])


def _resource_ids_not_in_buckets(rows: list[BillResourceRow], names: list[str]) -> list[str]:
    known = set(names)
    extra: list[str] = []
    for row in rows:
        rid = row.resource_id
        if rid and rid not in known and rid not in extra:
            extra.append(rid)
    return extra


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
