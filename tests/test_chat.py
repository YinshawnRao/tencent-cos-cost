from __future__ import annotations

from fastapi.testclient import TestClient

from cos_cost.web.app import create_app
from cos_cost.web.service import DashboardService


def _client(cache_dir) -> TestClient:
    return TestClient(create_app(DashboardService(mock=True, cache_dir=cache_dir)))


def test_ask_why_expensive_uses_mock_kpi(cache_dir) -> None:
    client = _client(cache_dir)
    resp = client.post("/api/ask", json={"q": "这个月为什么贵", "month": "2026-07"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "why_expensive"
    values = [item["value"] for item in data["numbers"]]
    assert 186420.0 in values
    assert "186420" in data["answer"].replace(",", "").replace(" ", "")


def test_ask_how_to_save_bucket(cache_dir) -> None:
    client = _client(cache_dir)
    resp = client.post("/api/ask", json={"q": "logs-prod-1250000000 怎么省", "month": "2026-07"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "how_to_save"
    assert "logs-prod-1250000000" in data["answer"]
    assert "R03" in data["answer"] or "R01" in data["answer"]
    names = [item["name"] for item in data["numbers"]]
    assert any("应付" in n for n in names)


def test_ask_export_last_month(cache_dir) -> None:
    client = _client(cache_dir)
    resp = client.post("/api/ask", json={"q": "导出上月一页", "month": "2026-07"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "export_page"
    hrefs = [l["href"] for l in data["links"]]
    assert any(h.startswith("/export/pdf") for h in hrefs)
    assert any(h.startswith("/export/xlsx") for h in hrefs)
