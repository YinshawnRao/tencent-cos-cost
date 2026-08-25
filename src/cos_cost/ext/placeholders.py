"""M2 占位：机会卡与配置体检。不是规则引擎，mock 用 fixture 喂布局。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cos_cost.models import ConfigLights, OpportunityHint

PLACEHOLDER_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "mock_placeholders.json"

COLUMN_LABELS = {
    "recycle": "本月可回收",
    "steady": "下月起稳态",
    "transform": "需业务改造",
}


def load_placeholders(path: Path | None = None) -> dict[str, Any]:
    target = path or PLACEHOLDER_PATH
    if not target.is_file():
        return {"opportunities": [], "config_health": {}, "config_lights": {}}
    return json.loads(target.read_text(encoding="utf-8"))


class PlaceholderOpportunityEngine:
    """读取 fixture 机会卡；M3 再换成真实规则引擎。"""

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        data = payload if payload is not None else load_placeholders()
        self.cards: list[dict[str, Any]] = list(data.get("opportunities") or [])

    def hint_for(self, bucket: str) -> OpportunityHint:
        matched = [c for c in self.cards if c.get("bucket") == bucket]
        if not matched:
            return OpportunityHint(amount=None, count=0, items=[])
        amounts = [float(c["net_saving"]) for c in matched if c.get("net_saving") is not None]
        headline = max(amounts) if amounts else None
        return OpportunityHint(amount=headline, count=len(matched), items=matched)

    def list_all(self) -> list[dict[str, Any]]:
        return list(self.cards)

    def cards_for(self, bucket: str) -> list[dict[str, Any]]:
        return [c for c in self.cards if c.get("bucket") == bucket]


class PlaceholderConfigLights:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        data = payload if payload is not None else load_placeholders()
        self._lights = data.get("config_lights") or {}
        self._health = data.get("config_health") or {}

    def lights_for(self, bucket: str) -> ConfigLights:
        raw = self._lights.get(bucket) or {}
        return ConfigLights(
            lifecycle=str(raw.get("lifecycle") or "unknown"),
            fragments=str(raw.get("fragments") or "unknown"),
            cdn=str(raw.get("cdn") or "unknown"),
            versioning=str(raw.get("versioning") or "unknown"),
            backup=str(raw.get("backup") or "unknown"),
        )

    def health_for(self, bucket: str) -> list[dict[str, Any]]:
        cards = self._health.get(bucket) or self._health.get("_default") or []
        return list(cards)
