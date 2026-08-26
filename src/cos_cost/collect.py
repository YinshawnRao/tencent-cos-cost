"""拉取并缓存：桶列表、账单汇总、按资源汇总、监控。账号首拉不扫全桶配置。"""

from __future__ import annotations

import logging
import os
import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from cos_cost.cache import (
    BUCKET_LIST_TTL,
    CONFIG_TTL,
    ESTIMATED_BILL_TTL,
    MONITOR_TTL,
    FileCache,
)
from cos_cost.clients.errors import CollectCancelled, PermissionDeniedError, check_cancel
from cos_cost.clients.parse import (
    config_snapshot_to_payload,
    parse_bill_resource,
    parse_buckets,
    parse_bucket_config,
    parse_cached_bill_summary,
    parse_cached_config,
)
from cos_cost.clients.protocols import ClientBundle
from cos_cost.limits import ACCOUNT_MONITOR_METRICS, BUCKET_EXTRA_MONITOR_METRICS, config_top_n
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

LOG = logging.getLogger("cos_cost.collect")


class CollectProgress:
    """线程安全的采集进度，给 UI poll 用。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.phase = ""
        self.buckets_done = 0
        self.buckets_total = 0
        self.done = True
        self.error: str | None = None
        self.status = "idle"

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, value)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "done": self.done,
                "buckets_done": self.buckets_done,
                "buckets_total": self.buckets_total,
                "phase": self.phase,
                "error": self.error,
                "status": self.status,
            }


def attach_cancel(bundle: ClientBundle, cancel: threading.Event | None) -> None:
    for client in (bundle.cos, bundle.billing, bundle.monitor):
        setter = getattr(client, "set_cancel", None)
        if callable(setter):
            setter(cancel)


def collect(
    bundle: ClientBundle,
    month: str,
    cache: FileCache,
    *,
    force: bool = False,
    creds: Credentials | None = None,
    cancel: threading.Event | None = None,
    progress: CollectProgress | None = None,
    config_limit: int | None = None,
    config_names: list[str] | None = None,
) -> CollectSnapshot:
    notes: list[str] = []
    cache_hits: list[str] = []
    secret_key = creds.secret_key if creds else None
    attach_cancel(bundle, cancel)

    def phase(name: str, *, done: int = 0, total: int = 0) -> None:
        LOG.info("采集 %s %s/%s", name, done, total or "")
        if progress is not None:
            progress.update(phase=name, buckets_done=done, buckets_total=total, status="running")

    phase("列桶")
    check_cancel(cancel)
    account_key, buckets, bucket_note, bucket_hit = _load_buckets(
        bundle, cache, force=force, secret_key=secret_key, cancel=cancel
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
    if progress is not None:
        progress.update(buckets_total=len(buckets), buckets_done=0, phase="列桶")

    phase("账单", total=len(buckets))
    check_cancel(cancel)
    bill_summary, bill_note, bill_hit = _load_bill_summary(
        bundle, cache, account_key, month, force=force, secret_key=secret_key
    )
    if bill_note:
        notes.append(bill_note)
    if bill_hit:
        cache_hits.append(f"bill_summary:{month}")

    prev_month = shift_month(month, -1)
    check_cancel(cancel)
    prev_summary, _, prev_sum_hit = _load_bill_summary(
        bundle, cache, account_key, prev_month, force=force, secret_key=secret_key
    )
    if prev_sum_hit:
        cache_hits.append(f"bill_summary:{prev_month}")

    yoy_month = shift_month(month, -12)
    check_cancel(cancel)
    yoy_summary, _, yoy_hit = _load_bill_summary(
        bundle, cache, account_key, yoy_month, force=force, secret_key=secret_key
    )
    if yoy_hit:
        cache_hits.append(f"bill_summary:{yoy_month}")

    check_cancel(cancel)
    resources, res_note, res_hit = _load_resources(
        bundle, cache, account_key, month, force=force, secret_key=secret_key
    )
    if res_note:
        notes.append(res_note)
    if res_hit:
        cache_hits.append(f"bill_resources:{month}")

    check_cancel(cancel)
    prev_resources, _, prev_res_hit = _load_resources(
        bundle, cache, account_key, prev_month, force=force, secret_key=secret_key
    )
    if prev_res_hit:
        cache_hits.append(f"bill_resources:{prev_month}")

    names = [b.name for b in buckets]
    extra_ids = _resource_ids_not_in_buckets(resources, names)
    monitor_names = list(dict.fromkeys(names + extra_ids))
    limit = config_top_n() if config_limit is None else config_limit
    if config_names is not None:
        wanted_config = list(dict.fromkeys(config_names))
    else:
        wanted_config = _top_names_by_payable(resources, buckets, limit)

    phase("监控", total=len(monitor_names))
    check_cancel(cancel)
    monitor, mon_note, mon_hit = _load_monitor(
        bundle,
        cache,
        account_key,
        month,
        monitor_names,
        force=force,
        secret_key=secret_key,
        metrics=ACCOUNT_MONITOR_METRICS,
        cancel=cancel,
        progress=progress,
    )
    if mon_note:
        notes.append(mon_note)
    if mon_hit:
        cache_hits.append(f"monitor:{month}")

    phase("前N配置", total=len(wanted_config))
    check_cancel(cancel)
    config, cfg_note, cfg_hit = _load_config(
        bundle,
        cache,
        account_key,
        buckets,
        force=force,
        secret_key=secret_key,
        only_names=wanted_config,
        cancel=cancel,
        progress=progress,
    )
    if cfg_note:
        notes.append(cfg_note)
    if cfg_hit:
        cache_hits.append("bucket_config")

    extra_monitor_names = [
        b.name for b in config.extra_buckets if b.name not in monitor_names
    ] + list(wanted_config)
    extra_monitor_names = list(dict.fromkeys(extra_monitor_names))
    if extra_monitor_names and not mon_hit and not bundle.mock:
        check_cancel(cancel)
        extra_mon, extra_note, _ = _load_monitor(
            bundle,
            cache,
            account_key,
            month,
            extra_monitor_names,
            force=True,
            secret_key=secret_key,
            metrics=BUCKET_EXTRA_MONITOR_METRICS,
            cancel=cancel,
            progress=progress,
            merge_into=monitor,
            cache_result=False,
        )
        if extra_mon is not None:
            monitor = extra_mon
            cache.put(
                account_key,
                "monitor",
                month,
                _monitor_to_payload(monitor),
                secret_key=secret_key,
            )
        if extra_note:
            notes.append(extra_note)

    if config.extra_buckets:
        monitor_names = list(dict.fromkeys(monitor_names + [b.name for b in config.extra_buckets]))

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


def load_bucket_configs(
    bundle: ClientBundle,
    cache: FileCache,
    account_key: str,
    buckets: list[BucketInfo],
    names: list[str],
    *,
    force: bool = False,
    creds: Credentials | None = None,
    cancel: threading.Event | None = None,
    progress: CollectProgress | None = None,
) -> ConfigSnapshot:
    """桶页懒加载：只打这些桶的 Lifecycle / Versioning / Logging / Inventory。"""
    attach_cancel(bundle, cancel)
    secret_key = creds.secret_key if creds else None
    snap, _, _ = _load_config(
        bundle,
        cache,
        account_key,
        buckets,
        force=force,
        secret_key=secret_key,
        only_names=list(dict.fromkeys(names)),
        cancel=cancel,
        progress=progress,
    )
    return snap


def _load_buckets(
    bundle: ClientBundle,
    cache: FileCache,
    *,
    force: bool,
    secret_key: str | None,
    cancel: threading.Event | None = None,
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
        check_cancel(cancel)
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
    only_names: list[str] | None = None,
    cancel: threading.Event | None = None,
    progress: CollectProgress | None = None,
) -> tuple[ConfigSnapshot, str | None, bool]:
    cached: dict[str, Any] = {}
    hit = cache.get(account_key, "bucket_config", None, ttl=CONFIG_TTL)
    if hit and isinstance(hit.payload, dict):
        cached = parse_cached_config(hit.payload)

    info = {b.name: b for b in buckets}
    needed = list(only_names) if only_names is not None else [b.name for b in buckets]
    to_fetch = [name for name in needed if force or name not in cached]
    getter_lc = getattr(bundle.cos, "get_bucket_lifecycle", None)
    if to_fetch and not callable(getter_lc):
        snap = ConfigSnapshot(
            by_bucket=cached,
            extra_buckets=_extra_from_configs(cached, set(info)),
            notes=["客户端无 GetBucketLifecycle，配置灯/R02–R10 降级。"],
        )
        return snap, snap.notes[0], bool(cached) and not to_fetch

    for index, name in enumerate(to_fetch):
        check_cancel(cancel)
        if progress is not None:
            progress.update(
                phase="前N配置",
                buckets_done=index,
                buckets_total=len(to_fetch),
                status="running",
            )
        bucket = info.get(name) or BucketInfo(name=name, region=None)
        cached[name] = _fetch_one_config(bundle, bucket, cancel=cancel)
        if progress is not None:
            progress.update(phase="前N配置", buckets_done=index + 1, buckets_total=len(to_fetch))

    extra = _extra_from_configs(cached, set(info) | set(cached))
    cache.put(
        account_key,
        "bucket_config",
        None,
        config_snapshot_to_payload(cached),
        secret_key=secret_key,
    )
    note = None
    if extra:
        names = "、".join(b.name for b in extra)
        note = f"R12 清单/日志目标桶已纳入账号视图: {names}"
    fully_cached = bool(hit) and not to_fetch
    return ConfigSnapshot(by_bucket=cached, extra_buckets=extra), note, fully_cached


def _fetch_one_config(
    bundle: ClientBundle,
    bucket: BucketInfo,
    *,
    cancel: threading.Event | None,
) -> Any:
    getter_lc = getattr(bundle.cos, "get_bucket_lifecycle", None)
    getter_ver = getattr(bundle.cos, "get_bucket_versioning", None)
    getter_log = getattr(bundle.cos, "get_bucket_logging", None)
    getter_inv = getattr(bundle.cos, "list_bucket_inventory", None)
    lc: Any = {}
    ver: Any = {}
    log: Any = {}
    inv: Any = []
    local_notes: list[str] = []
    check_cancel(cancel)
    if callable(getter_lc):
        try:
            lc = getter_lc(bucket.name, bucket.region)
        except PermissionDeniedError as exc:
            local_notes.append(f"无生命周期权限: {exc}")
            lc = {}
        except Exception as exc:  # noqa: BLE001 — 单桶降级，不中断采集
            local_notes.append(f"生命周期读取失败: {exc}")
            lc = {}
    check_cancel(cancel)
    if callable(getter_ver):
        try:
            ver = getter_ver(bucket.name, bucket.region)
        except PermissionDeniedError:
            local_notes.append("无版本控制读取权限")
            ver = {}
        except Exception as exc:  # noqa: BLE001
            local_notes.append(f"版本控制读取失败: {exc}")
            ver = {}
    check_cancel(cancel)
    if callable(getter_log):
        try:
            log = getter_log(bucket.name, bucket.region)
        except PermissionDeniedError:
            local_notes.append("无日志配置读取权限")
            log = {}
        except Exception as exc:  # noqa: BLE001
            local_notes.append(f"日志配置读取失败: {exc}")
            log = {}
    check_cancel(cancel)
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
    return parse_bucket_config(
        bucket.name,
        bucket.region,
        lifecycle=lc,
        versioning=ver,
        logging=log,
        inventory=inv,
        notes=local_notes,
    )


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
    metrics: tuple[str, ...] | None = None,
    cancel: threading.Event | None = None,
    progress: CollectProgress | None = None,
    merge_into: MonitorSnapshot | None = None,
    cache_result: bool = True,
) -> tuple[MonitorSnapshot | None, str | None, bool]:
    if not force and cache_result:
        hit = cache.get(account_key, "monitor", month, ttl=MONITOR_TTL)
        if hit and isinstance(hit.payload, dict):
            return _monitor_from_payload(hit.payload), None, True
    try:
        snap = _pull_monitor(
            bundle, month, buckets, metrics=metrics, cancel=cancel, progress=progress
        )
    except PermissionDeniedError as exc:
        return (
            None,
            f"缺少监控权限（GetMonitorData）：容量 / 标准% / 外网列为空。{exc}",
            False,
        )
    if merge_into is not None and snap is not None:
        snap = _merge_monitor(merge_into, snap)
    if cache_result and snap is not None:
        cache.put(account_key, "monitor", month, _monitor_to_payload(snap), secret_key=secret_key)
    extra = "; ".join(snap.notes) if snap and snap.notes else None
    return snap, extra, False


def _pull_monitor(
    bundle: ClientBundle,
    month: str,
    buckets: list[str],
    *,
    metrics: tuple[str, ...] | None,
    cancel: threading.Event | None,
    progress: CollectProgress | None,
) -> MonitorSnapshot:
    pull = bundle.monitor.pull_cos_metrics
    try:
        return pull(
            month,
            buckets,
            metrics=metrics,
            cancel=cancel,
            progress=progress,
        )
    except TypeError:
        return pull(month, buckets)


def _merge_monitor(base: MonitorSnapshot, extra: MonitorSnapshot) -> MonitorSnapshot:
    for name, metrics in extra.by_bucket.items():
        if name not in base.by_bucket:
            base.by_bucket[name] = metrics
            continue
        dst = base.by_bucket[name]
        for field in (
            "std_storage_bytes",
            "maz_std_storage_bytes",
            "sia_storage_bytes",
            "maz_ia_storage_bytes",
            "arc_storage_bytes",
            "deep_arc_storage_bytes",
            "internet_traffic_bytes",
            "multipart_storage_bytes",
            "internal_traffic_bytes",
            "cdn_traffic_bytes",
            "get_requests",
            "put_requests",
            "err_4xx",
            "err_5xx",
        ):
            val = getattr(metrics, field, None)
            if val is not None:
                setattr(dst, field, val)
        dst.daily.update(metrics.daily or {})
        if metrics.dates and not dst.dates:
            dst.dates = list(metrics.dates)
    base.notes.extend(extra.notes or [])
    return base


def _top_names_by_payable(
    rows: list[BillResourceRow],
    buckets: list[BucketInfo],
    limit: int,
) -> list[str]:
    payable: dict[str, float] = defaultdict(float)
    for row in rows:
        key = row.resource_id or row.resource_name
        if not key:
            continue
        if row.real_total_cost is None:
            continue
        payable[key] += float(row.real_total_cost)
    names: list[str] = []
    seen: set[str] = set()
    for bucket in buckets:
        if bucket.name not in seen:
            names.append(bucket.name)
            seen.add(bucket.name)
    for key in payable:
        if key not in seen:
            names.append(key)
            seen.add(key)
    names.sort(key=lambda n: (-payable.get(n, 0.0), n))
    if limit <= 0:
        return names
    return names[:limit]


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
