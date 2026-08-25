"""人类可读表格 + JSON。列对齐线框 C5 桶排行。"""

from __future__ import annotations

import json
from typing import Any

from cos_cost.ext.config_lights import LIGHT_LABELS
from cos_cost.models import RankingResult, RankingRow

TB = 1_000_000_000_000.0


def money_text(value: float | None) -> str:
    return _money(value)


def pct_text(value: float | None, *, signed: bool = True) -> str:
    return _pct(value, signed=signed)


def volume_text(nbytes: float | None) -> str:
    return _volume(nbytes)


def opportunity_text(row: RankingRow) -> str:
    return _opportunity(row)


def ready_label(ready: int | None, estimated: bool) -> str:
    return _ready_label(ready, estimated)


def ranking_json(result: RankingResult) -> str:
    payload = result.to_dict()
    payload["ready_label"] = _ready_label(result.ready, result.estimated)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def ranking_table(result: RankingResult) -> str:
    ready_label = _ready_label(result.ready, result.estimated)
    title = f"COS 机会大师 · 桶排行    账期 {result.month}    {ready_label}"
    if result.mock:
        title += "    [mock]"
    k = result.kpis
    kpi_lines = [
        "KPI",
        f"  COS 应付          {_money(k.cos_payable)}"
        f"    环比 {_pct(k.mom_pct)}    同比 {_pct(k.yoy_pct)}",
        f"  可优化金额        {_money(k.optimizable_amount)}（M3 规则引擎未接入）",
        f"  标准存储占比      {_pct(k.standard_storage_pct, signed=False)}",
        f"  外网下行          {_volume(k.internet_traffic_bytes)}",
        f"  请求费            {_money(k.request_fee)}（M1 未拆分计费项）",
        f"  数据就绪          {ready_label} · 桶 {k.bucket_with_bill}/{k.bucket_listed}",
    ]

    headers = ["桶", "地域", "应付", "环比", "容量", "标准%", "外网", "机会", "配置灯"]
    body: list[list[str]] = []
    for row in result.rows:
        body.append(
            [
                row.bucket,
                row.region or "—",
                _money(row.payable),
                _pct(row.mom_pct),
                _volume(row.capacity_bytes),
                _pct(row.standard_pct, signed=False),
                _volume(row.internet_traffic_bytes),
                _opportunity(row),
                _lights(row),
            ]
        )
    table = _ascii_table(headers, body)
    notes = ""
    if result.notes:
        notes = "\n说明\n" + "\n".join(f"  · {n}" for n in result.notes)
    return "\n".join([title, "", *kpi_lines, "", "桶排行", table, notes]).rstrip() + "\n"


def _ready_label(ready: int | None, estimated: bool) -> str:
    if ready == 1:
        return "账单已出账 (Ready=1)"
    if ready == 0 or estimated:
        return "暂估 (Ready=0)"
    return "账单不可用"


def _money(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    return f"{sign}¥ {abs(value):,.2f}".replace(".00", "")


def _pct(value: float | None, *, signed: bool = True) -> str:
    if value is None:
        return "—"
    if signed:
        return f"{value:+.0f}%" if abs(value) >= 10 or abs(value - round(value)) < 0.05 else f"{value:+.1f}%"
    return f"{value:.0f}%"


def _volume(nbytes: float | None) -> str:
    if nbytes is None:
        return "—"
    tb = nbytes / TB
    if nbytes == 0:
        return "0"
    if abs(tb - round(tb)) < 0.05 and abs(tb) >= 1:
        return f"{round(tb):.0f} TB"
    if abs(tb) >= 0.1:
        return f"{tb:.1f} TB"
    return f"{tb:.2f} TB"


def _opportunity(row: RankingRow) -> str:
    if row.opportunity_count <= 0 and row.opportunity_amount is None:
        return "—"
    amount = _money(row.opportunity_amount) if row.opportunity_amount is not None else "—"
    return f"{amount} · {row.opportunity_count}"


def _lights(row: RankingRow) -> str:
    data = row.config_lights.as_dict()
    parts: list[str] = []
    for key, label in LIGHT_LABELS:
        status = data.get(key, "unknown")
        mark = "●" if status in {"on", "yes", "true", "risk"} else "○"
        parts.append(f"{label}{mark}")
    return " ".join(parts)


def _ascii_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], _display_width(cell))
    def fmt(cells: list[str]) -> str:
        padded = []
        for i, cell in enumerate(cells):
            pad = widths[i] - _display_width(cell)
            padded.append(cell + " " * max(pad, 0))
        return "  ".join(padded)

    line = "  ".join("-" * w for w in widths)
    out = [fmt(headers), line]
    out.extend(fmt(r) for r in rows)
    if not rows:
        out.append("（无桶）")
    return "\n".join(out)


def _display_width(text: str) -> int:
    width = 0
    for ch in text:
        width += 2 if ord(ch) > 127 else 1
    return width


def collect_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
