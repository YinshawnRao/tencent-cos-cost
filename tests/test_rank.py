from __future__ import annotations

import json

from cos_cost.cache import FileCache
from cos_cost.collect import collect
from cos_cost.formatters import ranking_table
from cos_cost.ranking import build_ranking


def test_rank_columns_match_wireframe(cache_dir, mock_clients) -> None:
    snap = collect(mock_clients, "2026-07", FileCache(cache_dir))
    ranking = build_ranking(snap)
    text = ranking_table(ranking)
    for header in ("桶", "地域", "应付", "环比", "容量", "标准%", "外网", "机会", "配置灯"):
        assert header in text
    assert "logs-prod-1250000000" in text
    assert "ap-guangzhou" in text
    assert "img-cdn-1250000000" in text
    assert "backup-1250000000" in text
    assert "生命周期" in text
    assert "账单已出账" in text
    assert "12.4 TB" in text
    assert ranking.kpis.cos_payable == 186420.0
    logs = next(r for r in ranking.rows if r.bucket == "logs-prod-1250000000")
    assert logs.payable == 62100.0
    assert logs.region == "ap-guangzhou"
    assert logs.mom_pct is not None
    assert 17.5 <= logs.mom_pct <= 18.5
    assert logs.capacity_bytes is not None
    assert logs.standard_pct is not None
    assert 90 <= logs.standard_pct <= 92
    assert logs.opportunity_count == 0
    assert logs.config_lights.lifecycle == "unknown"


def test_rank_estimated_when_ready_zero(cache_dir, mock_clients) -> None:
    snap = collect(mock_clients, "2026-08", FileCache(cache_dir))
    ranking = build_ranking(snap)
    assert ranking.estimated is True
    assert ranking.ready == 0
    text = ranking_table(ranking)
    assert "暂估" in text
    assert ranking.rows  # Ready=0 仍返回行


def test_rank_missing_monitor_null_columns(cache_dir, fixture_data) -> None:
    from cos_cost.clients.mock import mock_bundle

    snap = collect(mock_bundle(fixture_data, deny_monitor=True), "2026-07", FileCache(cache_dir))
    ranking = build_ranking(snap)
    assert ranking.kpis.cos_payable == 186420.0
    for row in ranking.rows:
        assert row.capacity_bytes is None
        assert row.internet_traffic_bytes is None
    assert any("监控" in n for n in ranking.notes)


def test_rank_missing_bill_null_payable(cache_dir, fixture_data) -> None:
    from cos_cost.clients.mock import mock_bundle

    snap = collect(mock_bundle(fixture_data, deny_bill=True), "2026-07", FileCache(cache_dir))
    ranking = build_ranking(snap)
    assert len(ranking.rows) == 5
    assert all(row.payable is None for row in ranking.rows)
    assert ranking.kpis.cos_payable is None


def test_rank_json_uses_official_field_names(cache_dir, mock_clients) -> None:
    snap = collect(mock_clients, "2026-07", FileCache(cache_dir))
    raw = snap.bill_resources[0].raw
    assert "RealTotalCost" in raw
    assert "BusinessCode" in raw
    ranking = build_ranking(snap)
    payload = ranking.to_dict()
    assert payload["kpis"]["cos_payable"] == 186420.0
    json.dumps(payload)  # 可序列化
