from __future__ import annotations

import json
from datetime import timedelta

import pytest

from cos_cost.cache import ESTIMATED_BILL_TTL, FileCache
from cos_cost.secrets import SecretLeakError


def test_cache_roundtrip_and_ttl(cache_dir) -> None:
    cache = FileCache(cache_dir)
    cache.put("1250000000", "buckets", None, {"Owner": {"ID": "1250000000"}})
    hit = cache.get("1250000000", "buckets", None, ttl=timedelta(hours=1))
    assert hit is not None
    assert hit.payload["Owner"]["ID"] == "1250000000"


def test_ready_bill_is_immutable(cache_dir) -> None:
    cache = FileCache(cache_dir)
    cache.put(
        "1250000000",
        "bill_summary",
        "2026-07",
        {"Ready": 1, "SummaryOverview": []},
        ready=1,
        immutable=True,
    )
    # 即使 ttl 为 0 也应命中
    hit = cache.get(
        "1250000000",
        "bill_summary",
        "2026-07",
        ttl=timedelta(0),
        immutable_if_ready=True,
    )
    assert hit is not None
    assert hit.ready == 1


def test_estimated_bill_respects_ttl(cache_dir) -> None:
    cache = FileCache(cache_dir)
    cache.put(
        "1250000000",
        "bill_summary",
        "2026-08",
        {"Ready": 0},
        ready=0,
        immutable=False,
    )
    path = cache.path_for("1250000000", "bill_summary", "2026-08")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["fetched_at"] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(raw), encoding="utf-8")
    hit = cache.get(
        "1250000000",
        "bill_summary",
        "2026-08",
        ttl=ESTIMATED_BILL_TTL,
        immutable_if_ready=True,
    )
    assert hit is None


def test_cache_rejects_secret_key(cache_dir) -> None:
    cache = FileCache(cache_dir)
    with pytest.raises(SecretLeakError):
        cache.put(
            "1250000000",
            "buckets",
            None,
            {"note": "leak"},
            secret_key="leak",
        )
    with pytest.raises(SecretLeakError):
        cache.put("1250000000", "buckets", None, {"secret_key": "AKIDxxx"})
