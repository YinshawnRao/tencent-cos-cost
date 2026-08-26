from __future__ import annotations

from cos_cost.cache import FileCache
from cos_cost.collect import collect
from cos_cost.ext.opportunity import RuleEngine
from cos_cost.ext.rules import KPI_EXCLUDE_RULES, KPI_MIN_NET, is_backup_bucket
from cos_cost.ranking import build_ranking


def _engine(cache_dir, mock_clients, month: str = "2026-07") -> RuleEngine:
    snap = collect(mock_clients, month, FileCache(cache_dir))
    return RuleEngine(snap)


def test_phase1_rules_fire_on_mock_fixture(cache_dir, mock_clients) -> None:
    engine = _engine(cache_dir, mock_clients)
    ids = {c["rule_id"] for c in engine.list_all()}
    for rule_id in ("R01", "R02", "R03", "R04", "R10", "R11", "R12"):
        assert rule_id in ids, f"{rule_id} did not fire"

    r03 = next(c for c in engine.list_all() if c["rule_id"] == "R03")
    assert r03["bucket"] == "logs-prod-1250000000"
    assert r03["confidence"] == 0.9
    assert r03["evidence"]["lifecycle_has_abort"] is False
    assert r03["action_draft"].startswith("<?xml")
    assert "PutBucketLifecycle" in r03["action_draft"] or "不会" in r03["action_draft"]
    assert "GetBucket>" not in r03["action_draft"]

    r01 = next(c for c in engine.list_all() if c["rule_id"] == "R01" and "logs-prod" in c["bucket"])
    assert r01["confidence"] <= 0.62
    assert r01["evidence"]["inventory_csv_read"] is False

    r11 = next(c for c in engine.list_all() if c["rule_id"] == "R11")
    assert r11["bucket"] == "tmp-scratch-1250000000"
    assert r11["evidence"]["failed_share"] > 0.10

    r10 = next(c for c in engine.list_all() if c["rule_id"] == "R10")
    assert r10["bucket"] == "tmp-scratch-1250000000"
    assert any(v["days"] < v["min_days"] for v in r10["evidence"]["violations"])

    r04 = [c for c in engine.list_all() if c["rule_id"] == "R04"]
    assert any(c["bucket"] == "archive-cold-1250000000" for c in r04)
    assert all(c["net_saving"] is None for c in r04)

    r12 = [c for c in engine.list_all() if c["rule_id"] == "R12"]
    dests = {c["bucket"] for c in r12}
    assert "inv-archive-1250000000" in dests
    assert "log-access-1250000000" in dests


def test_kpi_excludes_r06_and_backup(cache_dir, mock_clients) -> None:
    engine = _engine(cache_dir, mock_clients)
    r06 = [c for c in engine.list_all() if c["rule_id"] == "R06"]
    assert r06
    assert all("不含 CDN 下行" in c["title"] for c in r06)
    assert all(c["in_kpi"] is False for c in r06)
    assert all(c["rule_id"] not in KPI_EXCLUDE_RULES or c["in_kpi"] is False for c in engine.list_all())
    for card in engine.list_all():
        if is_backup_bucket(card["bucket"]):
            assert card["in_kpi"] is False
        if card["in_kpi"]:
            assert card["net_saving"] >= KPI_MIN_NET
    assert engine.kpi_total() == sum(
        c["net_saving"] for c in engine.list_all() if c["in_kpi"]
    )


def test_drafts_never_suggest_getbucket_or_apply(cache_dir, mock_clients) -> None:
    engine = _engine(cache_dir, mock_clients)
    blob = "\n".join(str(c.get("action_draft") or "") + str(c.get("action") or "") for c in engine.list_all())
    assert "list_objects" not in blob.lower()
    assert "应用到桶" not in blob


def test_ranking_includes_r12_dest_buckets(cache_dir, mock_clients) -> None:
    snap = collect(mock_clients, "2026-07", FileCache(cache_dir))
    ranking = build_ranking(snap)
    names = {r.bucket for r in ranking.rows}
    assert "inv-archive-1250000000" in names
    assert "log-access-1250000000" in names
    logs = next(r for r in ranking.rows if r.bucket == "logs-prod-1250000000")
    assert logs.opportunity_count >= 3
    assert ranking.kpis.cos_payable == 186420.0
