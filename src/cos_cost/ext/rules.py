"""Phase-1 规则。输入 = collect 快照（账单 + 监控 + 只读配置），禁止 List Objects。"""

from __future__ import annotations

from typing import Any

from cos_cost.ext import drafts
from cos_cost.ext.prices import GB, WAN, BucketPrices, bytes_to_gb, prices_for
from cos_cost.models import BucketConfig, CollectSnapshot, MonitorBucketMetrics

KPI_MIN_NET = 50.0
KPI_EXCLUDE_RULES = {"R05", "R06", "R07", "R08", "R09"}
LARGE_STD_GB = 1024.0  # 1 TB
MPU_GB_MIN = 1.0
MPU_SHARE_MIN = 0.01
FAILED_SHARE_MIN = 0.10
REQUEST_FEE_MATERIAL = 50.0
R01_FRACTION = 0.20
R01_DENSITY_MAX = 500.0  # 请求 / GB / 月，低于此视为偏冷
MIN_OBJECT_BYTES = 64 * 1024


COLUMN_RECYCLE = "recycle"
COLUMN_STEADY = "steady"
COLUMN_TRANSFORM = "transform"


def is_backup_bucket(name: str) -> bool:
    token = name.lower()
    return "backup" in token or token.startswith("bak-") or "-bak-" in token


def evaluate_rules(snapshot: CollectSnapshot) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    known = {b.name for b in snapshot.buckets}
    extra_names = [b.name for b in (snapshot.config.extra_buckets if snapshot.config else [])]
    bucket_names: list[str] = []
    seen: set[str] = set()
    for name in [b.name for b in snapshot.buckets] + extra_names:
        if name not in seen:
            bucket_names.append(name)
            seen.add(name)
    for row in snapshot.bill_resources:
        if row.resource_id and row.resource_id not in seen:
            bucket_names.append(row.resource_id)
            seen.add(row.resource_id)

    dest_pairs: list[tuple[str, str, str]] = []
    for name in bucket_names:
        cfg = _config(snapshot, name)
        metrics = snapshot.monitor.by_bucket.get(name) if snapshot.monitor else None
        prices = prices_for(snapshot, name)
        cards.extend(_r03(name, cfg, metrics, prices))
        cards.extend(_r11(name, metrics, prices, snapshot))
        cards.extend(_r01(name, cfg, metrics, prices))
        cards.extend(_r02(name, cfg, metrics, prices))
        cards.extend(_r04(name, cfg))
        cards.extend(_r10(name, cfg, metrics, prices))
        cards.extend(_r06(name, metrics, prices, snapshot))
        if cfg:
            for dest in cfg.inventory_dest_buckets:
                dest_pairs.append((name, dest, "inventory"))
            if cfg.logging_dest_bucket:
                dest_pairs.append((name, cfg.logging_dest_bucket, "logging"))

    cards.extend(_r12(dest_pairs, known))
    for card in cards:
        card["in_kpi"] = _in_kpi(card)
        card.setdefault("why", card.get("formula") or card.get("title") or "")
    cards.sort(key=lambda c: (-(c.get("net_saving") or 0), c.get("rule_id") or "", c.get("bucket") or ""))
    return cards


def _in_kpi(card: dict[str, Any]) -> bool:
    if card.get("rule_id") in KPI_EXCLUDE_RULES:
        return False
    if is_backup_bucket(str(card.get("bucket") or "")):
        return False
    net = card.get("net_saving")
    if net is None or float(net) < KPI_MIN_NET:
        return False
    if card.get("blockers"):
        return False
    if float(card.get("confidence") or 0) < 0.4:
        return False
    return True


def _config(snapshot: CollectSnapshot, bucket: str) -> BucketConfig | None:
    if not snapshot.config:
        return None
    return snapshot.config.by_bucket.get(bucket)


def _r03(
    bucket: str,
    cfg: BucketConfig | None,
    metrics: MonitorBucketMetrics | None,
    prices: BucketPrices,
) -> list[dict[str, Any]]:
    mpu_bytes = 0.0
    if metrics and metrics.multipart_storage_bytes:
        mpu_bytes = float(metrics.multipart_storage_bytes)
    gb = mpu_bytes / GB
    cap = metrics.capacity_bytes if metrics else None
    share = (mpu_bytes / cap) if cap else 0.0
    missing_abort = cfg is None or not cfg.has_abort()
    material = gb >= MPU_GB_MIN or share >= MPU_SHARE_MIN
    if not material and not (missing_abort and gb > 0):
        return []
    net = round(gb * prices.p_std.value, 2) if gb > 0 else None
    title = f"未完成分块 {gb:.1f} GB" if gb > 0 else "缺少 AbortIncompleteMultipartUpload"
    evidence = {
        "StdMultipartStorage_GB": round(gb, 3),
        "share_of_bucket": round(share, 4),
        "lifecycle_has_abort": bool(cfg and cfg.has_abort()),
        "P_class": prices.p_std.value,
        "price_basis": prices.p_std.label(),
    }
    return [
        _card(
            rule_id="R03",
            title=title,
            bucket=bucket,
            column=COLUMN_RECYCLE,
            net_saving=net,
            confidence=0.9,
            evidence=evidence,
            formula="one_off = GB × P_class（分块存储月费）",
            why=f"{bucket} 未完成分块 {gb:.1f} GB，单价 {prices.p_std.value:.4f} 元/GB（{prices.p_std.label()}）。",
            action="AbortIncompleteMultipartUpload Days=7",
            action_draft=drafts.abort_xml(),
            warning="进行中超过 7 天的分块会被中止；已完成对象不受影响。请先确认上传 SLA。",
        )
    ]


def _r11(
    bucket: str,
    metrics: MonitorBucketMetrics | None,
    prices: BucketPrices,
    snapshot: CollectSnapshot,
) -> list[dict[str, Any]]:
    if metrics is None:
        return []
    failed = (metrics.err_4xx or 0) + (metrics.err_5xx or 0)
    total = (metrics.get_requests or 0) + (metrics.put_requests or 0)
    if total <= 0 or failed <= 0:
        return []
    share = failed / total
    req_fee = _request_fee(snapshot, bucket)
    if share <= FAILED_SHARE_MIN or (req_fee or 0) < REQUEST_FEE_MATERIAL:
        return []
    failed_wan = failed / WAN
    net = round(failed_wan * prices.p_get_wan.value, 2)
    return [
        _card(
            rule_id="R11",
            title=f"失败请求 {share:.0%}",
            bucket=bucket,
            column=COLUMN_RECYCLE,
            net_saving=net,
            confidence=0.8,
            evidence={
                "4xx": metrics.err_4xx,
                "5xx": metrics.err_5xx,
                "get+put": total,
                "failed_share": round(share, 4),
                "failed_wan": round(failed_wan, 2),
                "P_get": prices.p_get_wan.value,
                "price_basis": prices.p_get_wan.label(),
                "request_fee": req_fee,
                "note": "失败请求仍计费",
            },
            formula="save = failed_wan × P_get",
            why=f"{bucket} 4xx+5xx 占比 {share:.1%}，失败 {failed_wan:.1f} 万次 × {prices.p_get_wan.value:.4f} 元/万次（{prices.p_get_wan.label()}）。",
            action="收敛重试、签名过期与不存在的 Key，降低 4xx/5xx。不要对全桶列对象。",
            action_draft=(
                "# 草稿：排查客户端重试、签名过期、错误 Key。\n"
                "# 禁止对全桶列对象。\n"
                "# 本工具不会改桶配置。\n"
            ),
            warning="失败请求仍计费；修复后次月请求费才会下降。",
        )
    ]


def _r01(
    bucket: str,
    cfg: BucketConfig | None,
    metrics: MonitorBucketMetrics | None,
    prices: BucketPrices,
) -> list[dict[str, Any]]:
    if is_backup_bucket(bucket):
        return []
    if metrics is None or not metrics.standard_bytes:
        return []
    std_gb = metrics.standard_bytes / GB
    if std_gb < LARGE_STD_GB:
        return []
    get_put = (metrics.get_requests or 0) + (metrics.put_requests or 0)
    density = get_put / std_gb if std_gb else None
    if density is not None and density > R01_DENSITY_MAX:
        return []
    has_inventory = bool(cfg and cfg.inventory_ids)
    # M3 不读清单 CSV：即使有 inventory 配置也只做桶级保守观察。
    fraction = R01_FRACTION
    cold_gb = std_gb * fraction
    # Size<64KB 无清单无法识别，按保守折扣而不是把全量 STANDARD 算进沉降。
    delta = max(prices.p_std.value - prices.p_ia.value, 0.0)
    net = round(cold_gb * delta, 2)
    confidence = 0.55 if not has_inventory else 0.62
    return [
        _card(
            rule_id="R01",
            title="标准存储超 30 天（桶级保守）",
            bucket=bucket,
            column=COLUMN_STEADY,
            net_saving=net,
            confidence=confidence,
            evidence={
                "StdStorage_GB": round(std_gb, 1),
                "request_density": round(density, 1) if density is not None else None,
                "fraction": fraction,
                "cold_GB": round(cold_gb, 1),
                "P_std": prices.p_std.value,
                "P_ia": prices.p_ia.value,
                "price_basis_std": prices.p_std.label(),
                "price_basis_ia": prices.p_ia.label(),
                "inventory_csv_read": False,
                "min_object_guard": f"<{MIN_OBJECT_BYTES}B 未计入（无清单，已用 {fraction:.0%} 折扣）",
            },
            formula="cold_GB × (P_std − P_ia)；无清单时 cold_GB = 0.2 × STANDARD，不含 <64KB",
            why=(
                f"{bucket} 标准 {std_gb:.0f} GB，请求密度偏低，无清单 CSV。"
                f"按 20% 保守沉降到低频，差价 {delta:.4f} 元/GB。"
            ),
            action="Transition 到 STANDARD_IA，Days=30（不低于 30）",
            action_draft=drafts.transition_xml(storage_class="STANDARD_IA", days=30),
            warning="无对象级清单，不能按前缀沉降；可能含热数据。复制草稿后请人工确认，不会应用到桶。",
        )
    ]


def _r02(
    bucket: str,
    cfg: BucketConfig | None,
    metrics: MonitorBucketMetrics | None,
    prices: BucketPrices,
) -> list[dict[str, Any]]:
    std_gb = 0.0
    if metrics and metrics.standard_bytes:
        std_gb = metrics.standard_bytes / GB
    mpu_gb = 0.0
    if metrics and metrics.multipart_storage_bytes:
        mpu_gb = metrics.multipart_storage_bytes / GB
    missing_transition = std_gb >= LARGE_STD_GB and (cfg is None or not cfg.has_storage_transition())
    missing_abort = mpu_gb >= MPU_GB_MIN and (cfg is None or not cfg.has_abort())
    versioning_gap = bool(cfg and cfg.versioning_enabled() and not cfg.has_noncurrent_rule())
    if not (missing_transition or missing_abort or versioning_gap):
        return []
    gaps = []
    if missing_transition:
        gaps.append("无 STANDARD Transition")
    if missing_abort:
        gaps.append("无 AbortIncompleteMultipartUpload")
    if versioning_gap:
        gaps.append("版本开但无 Noncurrent* 规则")
    return [
        _card(
            rule_id="R02",
            title="缺失 / 不完整生命周期",
            bucket=bucket,
            column=COLUMN_STEADY,
            net_saving=None,
            confidence=0.7,
            evidence={
                "gaps": gaps,
                "std_GB": round(std_gb, 1),
                "multipart_GB": round(mpu_gb, 3),
                "versioning": cfg.versioning if cfg else None,
                "rule_count": len(cfg.rules) if cfg else 0,
            },
            formula="配置骨架：7 天中止分块 + 30 天转低频（金额记在 R01/R03/R04，避免重复计入 KPI）",
            why=f"{bucket} 生命周期不完整：{'；'.join(gaps)}。",
            action="组合 Abort 7 天 + STANDARD_IA Days=30。禁止列对象。",
            action_draft=drafts.skeleton_xml(),
            warning="复制草稿后请在控制台人工确认，本工具不会应用到桶。",
        )
    ]


def _r04(bucket: str, cfg: BucketConfig | None) -> list[dict[str, Any]]:
    if cfg is None or not cfg.versioning_enabled():
        return []
    if cfg.has_noncurrent_rule():
        return []
    return [
        _card(
            rule_id="R04",
            title="版本控制开、无非当前版本过期",
            bucket=bucket,
            column=COLUMN_STEADY,
            net_saving=None,
            confidence=0.65,
            evidence={
                "versioning": cfg.versioning,
                "has_noncurrent_rule": False,
                "inventory": bool(cfg.inventory_ids),
                "amount": "config-only：无清单，不编造非当前版本 GB",
            },
            formula="无清单时只出配置项，不估算对象容量，不计入可优化 KPI",
            why=f"{bucket} 已开版本控制但生命周期没有 NoncurrentExpiration / NoncurrentTransition。",
            action="NoncurrentVersionExpiration NoncurrentDays=30",
            action_draft=drafts.noncurrent_expiration_xml(),
            warning="无清单不能估计历史版本容量；不要列对象。",
            blockers=["no_inventory_gb"],
        )
    ]


def _r10(
    bucket: str,
    cfg: BucketConfig | None,
    metrics: MonitorBucketMetrics | None,
    prices: BucketPrices,
) -> list[dict[str, Any]]:
    if cfg is None:
        return []
    violations: list[dict[str, Any]] = []
    min_map = drafts.MIN_TRANSITION_DAYS
    for rule in cfg.rules:
        if not rule.enabled:
            continue
        for item in list(rule.transitions) + list(rule.noncurrent_transitions):
            klass = (item.storage_class or "").upper().replace(" ", "_")
            if klass in {"IA", "SIA"}:
                klass = "STANDARD_IA"
            minimum = min_map.get(klass)
            if minimum is None or item.days is None:
                continue
            if item.days < minimum:
                violations.append(
                    {
                        "storage_class": klass,
                        "days": item.days,
                        "min_days": minimum,
                        "rule_id": rule.rule_id,
                    }
                )
    if not violations:
        return []
    std_gb = bytes_to_gb(metrics.standard_bytes) if metrics else None
    best_net = 0.0
    pairs: list[tuple[str, int]] = []
    for item in violations:
        klass = item["storage_class"]
        remain = item["min_days"] - int(item["days"])
        if klass == "STANDARD_IA":
            p = prices.p_ia
        elif klass == "ARCHIVE":
            p = prices.p_archive
        else:
            p = prices.p_deep
        gb = std_gb or 0.0
        risk = gb * p.value * (remain / item["min_days"]) if gb else 0.0
        item["risk"] = round(risk, 2)
        item["price_basis"] = p.label()
        best_net = max(best_net, risk)
        pairs.append((klass, item["min_days"]))
    net = round(best_net, 2) if best_net > 0 else None
    return [
        _card(
            rule_id="R10",
            title="生命周期过短（未达最低存储时长）",
            bucket=bucket,
            column=COLUMN_STEADY,
            net_saving=net,
            confidence=0.85,
            evidence={
                "violations": violations,
                "std_GB_conservative": round(std_gb, 1) if std_gb is not None else None,
                "note": "节省 = 避免提前删除罚金的上限观察，无清单不拆对象",
            },
            formula="risk ≈ STANDARD_GB × P_dest × (min_days − days) / min_days",
            why=f"{bucket} 存在 IA<30 / ARCHIVE<90 / DEEP<180 的 Transition，可能触发提前删除费用。",
            action="把沉降天数提高到 IA≥30、ARCHIVE≥90、DEEP_ARCHIVE≥180。",
            action_draft=drafts.corrected_transitions_xml(pairs),
            warning="高优先级：过短规则可能产生提前删除费用。本工具不会改桶。",
        )
    ]


def _r06(
    bucket: str,
    metrics: MonitorBucketMetrics | None,
    prices: BucketPrices,
    snapshot: CollectSnapshot,
) -> list[dict[str, Any]]:
    if metrics is None or not metrics.internet_traffic_bytes:
        return []
    internet = metrics.internet_traffic_bytes
    cdn = metrics.cdn_traffic_bytes or 0.0
    if internet < 1000 * GB:
        return []
    if cdn >= 0.5 * internet:
        return []
    traffic_cost = _traffic_fee(snapshot, bucket)
    gb = internet / GB
    if traffic_cost is None and prices.p_traffic_gb:
        traffic_cost = gb * prices.p_traffic_gb.value
    # 仅 COS 侧备注，不进 KPI
    net = round(float(traffic_cost), 2) if traffic_cost else None
    return [
        _card(
            rule_id="R06",
            title="外网未走 CDN（不含 CDN 下行）",
            bucket=bucket,
            column=COLUMN_TRANSFORM,
            net_saving=net,
            confidence=0.45,
            evidence={
                "InternetTraffic_GB": round(gb, 2),
                "CdnOriginTraffic_GB": round(cdn / GB, 2),
                "cos_side_cost": net,
                "note": "金额为 COS 外网下行，不含 CDN 下行",
            },
            formula="COS 侧外网下行费用（不含 CDN 下行）。不计入可优化 KPI。",
            why=f"{bucket} COS 外网 {gb:.1f} GB，CDN 回源偏低。下列金额不含 CDN 下行。",
            action="静态命中走 CDN，桶仅回源。需业务改域名与鉴权。",
            action_draft=(
                "# 草稿：将源站接入 CDN，减少 COS 直连外网下载。\n"
                "# 不含 CDN 下行费用。本工具不会改 CDN / 桶配置。\n"
            ),
            warning="需业务改造。金额不计入可优化 KPI。",
            blockers=["business_change", "exclude_from_kpi"],
        )
    ]


def _r12(
    dest_pairs: list[tuple[str, str, str]], known: set[str]
) -> list[dict[str, Any]]:
    if not dest_pairs:
        return []
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for src, dest, kind in dest_pairs:
        if dest in seen:
            continue
        seen.add(dest)
        listed = dest in known
        cards.append(
            _card(
                rule_id="R12",
                title="清单/日志目标桶已纳入账号视图",
                bucket=dest,
                column=COLUMN_TRANSFORM,
                net_saving=None,
                confidence=0.9,
                evidence={
                    "source_bucket": src,
                    "kind": kind,
                    "already_in_getservice": listed,
                },
                formula="GetBucketInventory / GetBucketLogging 揭示的目标桶并入排行，不读清单 CSV",
                why=f"{src} 的{('清单' if kind == 'inventory' else '日志')}目标 {dest} 已出现在账号视图。",
                action="在排行中核对该目标桶费用，避免漏算投递存储。",
                action_draft="# 只读提示：目标桶已加入账号视图。不 List Objects，不改配置。\n",
                warning=None,
                blockers=["not_a_saving"],
            )
        )
    return cards


def _request_fee(snapshot: CollectSnapshot, bucket: str) -> float | None:
    from cos_cost.billing_items import request_fee_total

    rows = [r for r in snapshot.bill_resources if r.resource_id == bucket]
    return request_fee_total(rows)


def _traffic_fee(snapshot: CollectSnapshot, bucket: str) -> float | None:
    from cos_cost.billing_items import CATEGORY_TRAFFIC, classify_product

    total = 0.0
    saw = False
    for row in snapshot.bill_resources:
        if row.resource_id != bucket or row.real_total_cost is None:
            continue
        if classify_product(row.product_code, row.product_code_name) == CATEGORY_TRAFFIC:
            total += row.real_total_cost
            saw = True
    return total if saw else None


def _card(
    *,
    rule_id: str,
    title: str,
    bucket: str,
    column: str,
    net_saving: float | None,
    confidence: float,
    evidence: dict[str, Any],
    formula: str,
    why: str,
    action: str,
    action_draft: str,
    warning: str | None,
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "title": title,
        "bucket": bucket,
        "column": column,
        "net_saving": net_saving,
        "confidence": confidence,
        "evidence": evidence,
        "formula": formula,
        "why": why,
        "action": action,
        "action_draft": action_draft,
        "warning": warning,
        "blockers": blockers or [],
    }
