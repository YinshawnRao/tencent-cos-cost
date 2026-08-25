from __future__ import annotations

from unittest.mock import MagicMock

from cos_cost.cache import FileCache
from cos_cost.clients.mock import mock_bundle
from cos_cost.clients.protocols import ClientBundle
from cos_cost.collect import collect
from cos_cost.models import BillSummary


def test_collect_mock_and_second_run_uses_cache(cache_dir, mock_clients) -> None:
    cache = FileCache(cache_dir)
    first = collect(mock_clients, "2026-07", cache)
    assert first.account_key == "1250000000"
    assert len(first.buckets) == 5
    assert {b.name for b in first.buckets} >= {
        "logs-prod-1250000000",
        "img-cdn-1250000000",
        "backup-1250000000",
    }
    assert first.bill_summary is not None
    assert first.bill_summary.ready == 1
    assert first.bill_summary.cos_real_total_cost == 186420.0
    assert all(r.business_code == "p_cos" for r in first.bill_resources)
    assert not any(r.resource_id == "ins-should-be-filtered" for r in first.bill_resources)
    assert first.cache_hits == []

    billing = MagicMock()
    billing.describe_bill_summary_by_product.side_effect = AssertionError("should use cache")
    billing.describe_bill_resource_summary.side_effect = AssertionError("should use cache")
    cos = MagicMock()
    cos.list_buckets.side_effect = AssertionError("should use cache")
    monitor = MagicMock()
    monitor.pull_cos_metrics.side_effect = AssertionError("should use cache")
    stale = ClientBundle(
        account_key="1250000000",
        cos=cos,
        billing=billing,
        monitor=monitor,
        mock=True,
    )
    second = collect(stale, "2026-07", cache)
    assert "buckets" in second.cache_hits
    assert any(h.startswith("bill_summary:2026-07") for h in second.cache_hits)
    assert any(h.startswith("bill_resources:2026-07") for h in second.cache_hits)
    assert second.bill_summary is not None
    assert second.bill_summary.cos_real_total_cost == 186420.0


def test_collect_missing_bill_still_lists_buckets(cache_dir, fixture_data) -> None:
    bundle = mock_bundle(fixture_data, deny_bill=True)
    snap = collect(bundle, "2026-07", FileCache(cache_dir))
    assert len(snap.buckets) == 5
    assert snap.bill_summary is None
    assert snap.bill_resources == []
    assert any("账单" in n or "应付" in n for n in snap.notes)


def test_collect_missing_monitor_still_has_bills(cache_dir, fixture_data) -> None:
    bundle = mock_bundle(fixture_data, deny_monitor=True)
    snap = collect(bundle, "2026-07", FileCache(cache_dir))
    assert snap.bill_summary is not None
    assert snap.monitor is None
    assert any("监控" in n for n in snap.notes)


def test_collect_cache_has_no_credentials(cache_dir, mock_clients) -> None:
    cache = FileCache(cache_dir)
    collect(mock_clients, "2026-07", cache, creds=None)
    for path in cache_dir.rglob("*.json"):
        text = path.read_text(encoding="utf-8").lower()
        assert "secretkey" not in text
        assert "secret_key" not in text
        assert "cos_secret_key" not in text


def test_force_bypasses_cache(cache_dir, mock_clients) -> None:
    cache = FileCache(cache_dir)
    collect(mock_clients, "2026-07", cache)
    calls = {"summary": 0}

    class CountingBilling:
        def describe_bill_summary_by_product(self, month: str) -> BillSummary:
            calls["summary"] += 1
            return mock_clients.billing.describe_bill_summary_by_product(month)

        def describe_bill_resource_summary(self, month: str):
            return mock_clients.billing.describe_bill_resource_summary(month)

    bundle = ClientBundle(
        account_key="1250000000",
        cos=mock_clients.cos,
        billing=CountingBilling(),
        monitor=mock_clients.monitor,
        mock=True,
    )
    collect(bundle, "2026-07", cache, force=True)
    assert calls["summary"] >= 1
