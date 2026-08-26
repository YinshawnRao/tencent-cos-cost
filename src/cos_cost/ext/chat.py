"""模板问答。数字只来自缓存的排行 + 机会卡，不编造金额。"""

from __future__ import annotations

from typing import Any

from cos_cost.formatters import money_text, pct_text, volume_text
from cos_cost.models import RankingResult
from cos_cost.monthutil import previous_month_utc8

INTENT_WHY = "why_expensive"
INTENT_SAVE = "how_to_save"
INTENT_EXPORT = "export_page"


def answer_question(
    question: str,
    *,
    month: str,
    ranking: RankingResult,
    cards: list[dict[str, Any]],
) -> dict[str, Any]:
    q = (question or "").strip()
    intent, bucket = classify_intent(q, [r.bucket for r in ranking.rows])
    numbers = _collect_numbers(ranking, cards, bucket)
    if intent == INTENT_EXPORT:
        target = _export_month(q, month)
        answer = (
            f"按口径导出账期 {target} 的一页 PDF / 五表 Excel。"
            f"COS 应付以缓存为准"
            f"{('：' + money_text(ranking.kpis.cos_payable)) if ranking.month == target else ''}。"
            "数字不会现场编造；请下载导出文件。"
        )
        links = [
            {"rel": "pdf", "href": f"/export/pdf?month={target}"},
            {"rel": "xlsx", "href": f"/export/xlsx?month={target}"},
        ]
        if ranking.month == target:
            numbers = _collect_numbers(ranking, cards, None)
        return {"answer": answer, "numbers": numbers, "links": links, "intent": intent}

    if intent == INTENT_SAVE:
        target_bucket = bucket or _guess_bucket(q, ranking)
        bucket_cards = [c for c in cards if c.get("bucket") == target_bucket] if target_bucket else cards[:5]
        if not target_bucket:
            answer = "请指定桶名，例如「logs-prod-1250000000 怎么省」。以下是账号级可优化口径。"
        else:
            bits = []
            for card in bucket_cards:
                net = money_text(card.get("net_saving")) if card.get("net_saving") is not None else "—"
                bits.append(
                    f"{card.get('rule_id')} {card.get('title')} 净节省 {net} "
                    f"（置信度 {card.get('confidence')}）。{card.get('why') or ''}"
                )
            kpi = next((r.opportunity_amount for r in ranking.rows if r.bucket == target_bucket), None)
            payable = next((r.payable for r in ranking.rows if r.bucket == target_bucket), None)
            answer = (
                f"{target_bucket} 本月应付 {money_text(payable)}，"
                f"可计入 KPI 的节省 {money_text(kpi)}。"
                + (" ".join(bits) if bits else " 规则引擎未对该桶产出机会卡。")
                + " 抽屉只能「复制草稿」，不会应用到桶。"
            )
        links = []
        if target_bucket:
            links.append({"rel": "bucket", "href": f"/b/{target_bucket}?month={month}"})
        return {"answer": answer, "numbers": numbers, "links": links, "intent": intent}

    # why expensive
    k = ranking.kpis
    top = ranking.rows[:3]
    top_txt = "、".join(
        f"{r.bucket} {money_text(r.payable)}（环比 {pct_text(r.mom_pct)}）" for r in top if r.payable is not None
    )
    recycle = [c for c in cards if c.get("column") == "recycle" and c.get("net_saving")]
    answer = (
        f"{month} COS 应付 {money_text(k.cos_payable)}，"
        f"环比 {pct_text(k.mom_pct)}，同比 {pct_text(k.yoy_pct)}。"
        f"外网 {volume_text(k.internet_traffic_bytes)}，请求费 {money_text(k.request_fee)}。"
        f"应付最高的桶：{top_txt or '—'}。"
        f"可优化金额 {money_text(k.optimizable_amount)}（net≥50、无强阻断，不含 R06 CDN 下行）。"
    )
    if recycle:
        answer += " 本月可回收：" + "；".join(
            f"{c.get('rule_id')} {c.get('bucket')} {money_text(c.get('net_saving'))}" for c in recycle[:3]
        )
        answer += "。"
    links = [{"rel": "account", "href": f"/?month={month}"}]
    return {"answer": answer, "numbers": numbers, "links": links, "intent": intent}


def classify_intent(question: str, buckets: list[str]) -> tuple[str, str | None]:
    q = question.strip()
    bucket = _match_bucket(q, buckets)
    if any(token in q for token in ("为什么贵", "为何贵", "怎么贵")):
        return INTENT_WHY, bucket
    if any(token in q for token in ("怎么省", "如何省", "怎样省", "省钱")):
        return INTENT_SAVE, bucket
    if bucket and any(token in q for token in ("怎么", "如何")):
        return INTENT_SAVE, bucket
    if any(token in q for token in ("导出", "一页", "pdf", "PDF", "excel", "Excel")):
        return INTENT_EXPORT, None
    return INTENT_WHY, bucket


def _export_month(question: str, month: str) -> str:
    if "上月" in question:
        return previous_month_utc8()
    if "本月" in question:
        return month
    return month


def _match_bucket(question: str, buckets: list[str]) -> str | None:
    for name in buckets:
        if name in question:
            return name
    # 允许短名 logs-prod
    for name in buckets:
        short = name.rsplit("-", 1)[0]
        if short and short in question:
            return name
    return None


def _guess_bucket(question: str, ranking: RankingResult) -> str | None:
    return _match_bucket(question, [r.bucket for r in ranking.rows])


def _collect_numbers(
    ranking: RankingResult, cards: list[dict[str, Any]], bucket: str | None
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    k = ranking.kpis

    def add(name: str, value: Any) -> None:
        if value is None:
            return
        out.append({"name": name, "value": value})

    add("COS应付", k.cos_payable)
    add("可优化金额", k.optimizable_amount)
    add("请求费", k.request_fee)
    add("外网字节", k.internet_traffic_bytes)
    add("环比", k.mom_pct)
    if bucket:
        row = next((r for r in ranking.rows if r.bucket == bucket), None)
        if row:
            add(f"{bucket}.应付", row.payable)
            add(f"{bucket}.可优化", row.opportunity_amount)
        for card in cards:
            if card.get("bucket") == bucket and card.get("net_saving") is not None:
                add(f"{card.get('rule_id')}.净节省", card.get("net_saving"))
    else:
        for card in cards:
            if card.get("in_kpi") and card.get("net_saving") is not None:
                add(f"{card.get('rule_id')}.{card.get('bucket')}", card.get("net_saving"))
    # 去重保序
    seen: set[str] = set()
    unique = []
    for item in out:
        key = item["name"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique
