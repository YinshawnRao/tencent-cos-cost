"""优化机会接口（M3 规则引擎接入点）。M1 只返回空占位。"""

from __future__ import annotations

from typing import Protocol

from cos_cost.models import OpportunityHint


class OpportunityEngine(Protocol):
    def hint_for(self, bucket: str) -> OpportunityHint:
        """按桶给出可优化金额与规则条数。M1 恒为空。"""


class NullOpportunityEngine:
    """M1：不跑规则，机会列显示 —。"""

    def hint_for(self, bucket: str) -> OpportunityHint:
        return OpportunityHint(amount=None, count=0, items=[])
