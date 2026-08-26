from __future__ import annotations

from fastapi.testclient import TestClient

from cos_cost.web.app import create_app
from cos_cost.web.service import DashboardService


def _client(cache_dir) -> TestClient:
    service = DashboardService(mock=True, cache_dir=cache_dir)
    return TestClient(create_app(service))


def test_api_account_ranking_mock(cache_dir) -> None:
    client = _client(cache_dir)
    resp = client.get("/api/account", params={"month": "2026-07"})
    assert resp.status_code == 200
    data = resp.json()
    names = [row["bucket"] for row in data["ranking"]]
    assert "img-cdn-1250000000" in names
    assert "logs-prod-1250000000" in names
    assert "backup-1250000000" in names
    logs = next(r for r in data["ranking"] if r["bucket"] == "logs-prod-1250000000")
    assert logs["payable"] == 62100.0
    assert data["kpis"]["cos_payable"] == 186420.0
    assert data["ready"] == 1
    assert "secret_key" not in resp.text.lower()
    assert "COS_SECRET_KEY" not in resp.text
    assert data["settings"]["mode"] == "mock"
    assert data["settings"]["secret_id_masked"] is None


def test_pages_render(cache_dir) -> None:
    client = _client(cache_dir)
    home = client.get("/", params={"month": "2026-07"})
    assert home.status_code == 200
    assert "COS 机会大师" in home.text
    assert "img-cdn-1250000000" in home.text
    assert "logs-prod-1250000000" in home.text
    assert "backup-1250000000" in home.text
    assert "桶排行" in home.text
    bucket = client.get("/b/logs-prod-1250000000", params={"month": "2026-07"})
    assert bucket.status_code == 200
    assert "logs-prod-1250000000" in bucket.text
    assert "清单未就绪，对象级建议不可用" in bucket.text
    assert "复制草稿" in bucket.text
    assert "应用到桶" not in bucket.text.replace("不会应用到桶", "")


def test_api_bucket_and_ready_zero(cache_dir) -> None:
    client = _client(cache_dir)
    resp = client.get("/api/buckets/logs-prod-1250000000", params={"month": "2026-07"})
    assert resp.status_code == 200
    assert resp.json()["bucket"] == "logs-prod-1250000000"
    est = client.get("/", params={"month": "2026-08"})
    assert est.status_code == 200
    assert "暂估" in est.text


def test_missing_bucket_404(cache_dir) -> None:
    client = _client(cache_dir)
    assert client.get("/b/no-such-bucket-1250000000", params={"month": "2026-07"}).status_code == 404


def test_account_uses_engine_cards_not_static_only(cache_dir) -> None:
    client = _client(cache_dir)
    data = client.get("/api/account", params={"month": "2026-07"}).json()
    cards = [c for col in data["opportunities"]["columns"] for c in col["cards"]]
    ids = {c["rule_id"] for c in cards}
    assert {"R01", "R02", "R03", "R11"}.issubset(ids)
    assert data["kpis"]["cos_payable"] == 186420.0
    assert data["kpis"]["optimizable"] is not None
    logs = next(r for r in data["ranking"] if r["bucket"] == "logs-prod-1250000000")
    assert logs["opportunity_text"] != "—"


def test_export_endpoints(cache_dir) -> None:
    client = _client(cache_dir)
    pdf = client.get("/export/pdf", params={"month": "2026-07"})
    assert pdf.status_code == 200
    assert pdf.content[:4] == b"%PDF"
    assert "secret" not in pdf.text.lower()
    xlsx = client.get("/export/xlsx", params={"month": "2026-07"})
    assert xlsx.status_code == 200
    assert xlsx.content[:2] == b"PK"
