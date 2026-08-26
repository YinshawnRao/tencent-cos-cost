"""优化机会：M3 规则引擎。输入 collect 快照，输出机会卡与可优化 KPI。"""

from __future__ import annotations

from typing import Any, Protocol

from cos_cost.ext.rules import KPI_MIN_NET, evaluate_rules
from cos_cost.models import CollectSnapshot, OpportunityHint


class OpportunityEngine(Protocol):
    def hint_for(self, bucket: str) -> OpportunityHint:
        """按桶给出可优化金额与规则条数。"""


class NullOpportunityEngine:
    """显式关闭规则时使用。"""

    def hint_for(self, bucket: str) -> OpportunityHint:
        return OpportunityHint(amount=None, count=0, items=[])

    def list_all(self) -> list[dict[str, Any]]:
        return []

    def cards_for(self, bucket: str) -> list[dict[str, Any]]:
        return []

    def kpi_total(self) -> float | None:
        return None


class RuleEngine:
    """从监控 + 只读配置 + 账单单价计算机会卡。mock 与线上走同一套规则。"""

    def __init__(self, snapshot: CollectSnapshot | None = None) -> None:
        self.snapshot = snapshot
        self.cards: list[dict[str, Any]] = evaluate_rules(snapshot) if snapshot is not None else []

    def hint_for(self, bucket: str) -> OpportunityHint:
        matched = self.cards_for(bucket)
        kpi_amounts = [
            float(c["net_saving"])
            for c in matched
            if c.get("in_kpi") and c.get("net_saving") is not None
        ]
        amount = float(sum(kpi_amounts)) if kpi_amounts else None
        return OpportunityHint(amount=amount, count=len(matched), items=matched)

    def list_all(self) -> list[dict[str, Any]]:
        return list(self.cards)

    def cards_for(self, bucket: str) -> list[dict[str, Any]]:
        return [c for c in self.cards if c.get("bucket") == bucket]

    def kpi_total(self) -> float:
        amounts = [
            float(c["net_saving"])
            for c in self.cards
            if c.get("in_kpi") and c.get("net_saving") is not None
        ]
        return float(sum(amounts))

    def top_opportunities(self, limit: int = 5) -> list[dict[str, Any]]:
        ranked = sorted(
            self.cards,
            key=lambda c: (-(c.get("net_saving") or 0.0), c.get("rule_id") or ""),
        )
        return ranked[:limit]


def engine_for(snapshot: CollectSnapshot) -> RuleEngine:
    return RuleEngine(snapshot)


KPI_THRESHOLD = KPI_MIN_NET
