"""组装 C5 桶排行与账户 KPI。"""

from __future__ import annotations

from collections import defaultdict

from cos_cost.ext.config_lights import ConfigLightProvider, UnknownConfigLights
from cos_cost.ext.opportunity import NullOpportunityEngine, OpportunityEngine
from cos_cost.models import (
    BillResourceRow,
    CollectSnapshot,
    Kpis,
    RankingResult,
    RankingRow,
)


def build_ranking(
    snapshot: CollectSnapshot,
    *,
    opportunity: OpportunityEngine | None = None,
    lights: ConfigLightProvider | None = None,
) -> RankingResult:
    engine = opportunity or NullOpportunityEngine()
    light_provider = lights or UnknownConfigLights()

    current = _aggregate_payable(snapshot.bill_resources)
    previous = _aggregate_payable(snapshot.prev_bill_resources)
    regions = {b.name: b.region for b in snapshot.buckets}

    bucket_names: list[str] = []
    seen: set[str] = set()
    for bucket in snapshot.buckets:
        if bucket.name not in seen:
            bucket_names.append(bucket.name)
            seen.add(bucket.name)
    for resource_id in current:
        if resource_id not in seen:
            bucket_names.append(resource_id)
            seen.add(resource_id)

    rows: list[RankingRow] = []
    for name in bucket_names:
        payable = current.get(name)
        prev = previous.get(name)
        mom = _change_pct(payable, prev)
        metrics = None
        if snapshot.monitor:
            metrics = snapshot.monitor.by_bucket.get(name)
        hint = engine.hint_for(name)
        region = regions.get(name)
        if region is None:
            region = _first_region(snapshot.bill_resources, name)
        raw_ids, raw_names = _raw_ids_for(snapshot.bill_resources, name)
        rows.append(
            RankingRow(
                bucket=name,
                region=region,
                payable=payable,
                mom_pct=mom,
                capacity_bytes=metrics.capacity_bytes if metrics else None,
                standard_pct=metrics.standard_pct if metrics else None,
                internet_traffic_bytes=metrics.internet_traffic_bytes if metrics else None,
                opportunity_amount=hint.amount,
                opportunity_count=hint.count,
                config_lights=light_provider.lights_for(name),
                raw_resource_ids=raw_ids,
                raw_resource_names=raw_names,
            )
        )

    rows.sort(key=lambda r: (r.payable is None, -(r.payable or 0.0), r.bucket))

    ready = snapshot.bill_summary.ready if snapshot.bill_summary else None
    estimated = True if ready is None else ready != 1
    if snapshot.bill_summary is None and not snapshot.bill_resources:
        # 无账单权限时不标「暂估」，用 notes 说明应付为空。
        estimated = False
        ready_for_kpi = None
    else:
        ready_for_kpi = 0 if ready is None else ready

    cos_payable = None
    if snapshot.bill_summary and snapshot.bill_summary.cos_real_total_cost is not None:
        cos_payable = snapshot.bill_summary.cos_real_total_cost
    elif current:
        cos_payable = float(sum(v for v in current.values() if v is not None))

    prev_total = None
    if snapshot.prev_bill_summary and snapshot.prev_bill_summary.cos_real_total_cost is not None:
        prev_total = snapshot.prev_bill_summary.cos_real_total_cost
    elif previous:
        prev_total = float(sum(v for v in previous.values() if v is not None))

    yoy_total = None
    if snapshot.yoy_bill_summary and snapshot.yoy_bill_summary.cos_real_total_cost is not None:
        yoy_total = snapshot.yoy_bill_summary.cos_real_total_cost

    std_num = 0.0
    cap_num = 0.0
    traffic = 0.0
    saw_cap = False
    saw_traffic = False
    if snapshot.monitor:
        for metrics in snapshot.monitor.by_bucket.values():
            if metrics.capacity_bytes is not None:
                cap_num += metrics.capacity_bytes
                saw_cap = True
            if metrics.standard_bytes is not None:
                std_num += metrics.standard_bytes
            if metrics.internet_traffic_bytes is not None:
                traffic += metrics.internet_traffic_bytes
                saw_traffic = True

    kpis = Kpis(
        cos_payable=cos_payable,
        mom_pct=_change_pct(cos_payable, prev_total),
        yoy_pct=_change_pct(cos_payable, yoy_total),
        optimizable_amount=None,
        standard_storage_pct=(100.0 * std_num / cap_num) if saw_cap and cap_num > 0 else None,
        internet_traffic_bytes=traffic if saw_traffic else None,
        request_fee=None,
        ready=ready_for_kpi,
        bucket_listed=len(snapshot.buckets),
        bucket_with_bill=sum(1 for name in seen if name in current),
    )

    notes = list(snapshot.notes)
    if snapshot.bill_summary and snapshot.bill_summary.estimated:
        notes.append("账单 Ready=0，本月排行为暂估，数据可能未出账完成。")
    if snapshot.monitor is None:
        if not any("监控" in n for n in notes):
            notes.append("无监控数据：容量 / 标准% / 外网为空。")
    if snapshot.bill_summary is None and not snapshot.bill_resources:
        if not any("账单" in n or "应付" in n for n in notes):
            notes.append("无账单数据：应付 / 环比为空。")
    notes.append("机会列与配置灯为 M2/M3 占位，本阶段不探测生命周期或未完成分片。")
    notes.append("请求费未拆分：M1 只使用 DescribeBillResourceSummary，不翻页 DescribeBillDetail。")

    return RankingResult(
        month=snapshot.month,
        account_key=snapshot.account_key,
        ready=ready_for_kpi,
        estimated=estimated,
        kpis=kpis,
        rows=rows,
        notes=notes,
        mock=snapshot.mock,
    )


def _aggregate_payable(rows: list[BillResourceRow]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        key = row.resource_id or row.resource_name
        if not key:
            # 无法识别资源时仍保留 raw，调用方在 JSON 里能看到。
            key = str(row.raw.get("ResourceId") or row.raw.get("ResourceName") or "")
        if not key:
            continue
        if row.real_total_cost is None:
            continue
        totals[key] += row.real_total_cost
    return dict(totals)


def _change_pct(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    if previous == 0:
        return None
    return (current - previous) / previous * 100.0


def _first_region(rows: list[BillResourceRow], resource_id: str) -> str | None:
    for row in rows:
        if row.resource_id == resource_id and row.region_name:
            return row.region_name
    return None


def _raw_ids_for(
    rows: list[BillResourceRow], resource_id: str
) -> tuple[list[str], list[str]]:
    ids: list[str] = []
    names: list[str] = []
    for row in rows:
        if row.resource_id == resource_id:
            if row.resource_id and row.resource_id not in ids:
                ids.append(row.resource_id)
            if row.resource_name and row.resource_name not in names:
                names.append(row.resource_name)
    return ids, names
