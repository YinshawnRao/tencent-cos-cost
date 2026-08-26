"""Local credential settings API — SecretKey must never leave the server."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from cos_cost.clients.mock import load_fixture, mock_bundle
from cos_cost.local_creds import save_local_creds
from cos_cost.secrets import Credentials
from cos_cost.web.app import create_app
from cos_cost.web.service import DashboardService


def _client(tmp_path: Path, *, mock: bool = True) -> TestClient:
    cache = tmp_path / "cache"
    creds = tmp_path / ".local-creds.json"
    service = DashboardService(mock=mock, cache_dir=cache, creds_path=creds)
    return TestClient(create_app(service))


def _patch_live_bundle(monkeypatch) -> None:
    """Avoid constructing Tencent SDK clients with dummy keys."""

    def fake_bundle(*, mock: bool, creds=None, **_k):
        return mock_bundle(load_fixture())

    monkeypatch.setattr("cos_cost.web.service.build_bundle", fake_bundle)


def test_gitignore_includes_local_creds() -> None:
    text = Path(".gitignore").read_text(encoding="utf-8")
    assert ".local-creds.json" in text
    check = subprocess.run(
        ["git", "check-ignore", "-q", ".local-creds.json"],
        cwd=Path(__file__).resolve().parents[1],
    )
    assert check.returncode == 0


def test_status_starts_mock(tmp_path: Path) -> None:
    client = _client(tmp_path)
    r = client.get("/api/settings/status")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "mock"
    assert body["secret_id_masked"] is None
    blob = json.dumps(body)
    assert "secret_key" not in blob
    assert "COS_SECRET_KEY" not in blob


def test_post_credentials_does_not_leak_secret_key(tmp_path: Path, monkeypatch) -> None:
    _patch_live_bundle(monkeypatch)
    client = _client(tmp_path, mock=True)
    dummy_key = "SECRETKEY_NEVER_LEAK_THIS_VALUE_12345"
    r = client.post(
        "/api/settings/credentials",
        json={
            "secret_id": "AKIDabcdefghijklmnopqrstuvwxyz012345",
            "secret_key": dummy_key,
            "month": "2026-07",
        },
    )
    assert r.status_code == 200, r.text
    assert dummy_key not in r.text
    body = r.json()
    assert "secret_key" not in body
    assert body["mode"] == "live"
    assert body["saved"] is True
    assert body["secret_id_masked"] == "AKID****2345"
    assert body["month"] == "2026-07"
    assert body.get("last_collect_error") in (None, "")

    status = client.get("/api/settings/status").json()
    assert status["mode"] == "live"
    assert status["secret_id_masked"] == "AKID****2345"
    assert dummy_key not in json.dumps(status)
    assert "secret_key" not in status

    creds_file = tmp_path / ".local-creds.json"
    assert creds_file.is_file()
    stored = json.loads(creds_file.read_text(encoding="utf-8"))
    assert stored["secret_key"] == dummy_key
    assert (creds_file.stat().st_mode & 0o777) == 0o600

    cache_dir = tmp_path / "cache"
    if cache_dir.exists():
        cache_blob = "".join(
            p.read_text(encoding="utf-8", errors="ignore") for p in cache_dir.rglob("*") if p.is_file()
        )
        assert dummy_key not in cache_blob

    account = client.get("/api/account?month=2026-07")
    assert account.status_code == 200
    assert dummy_key not in account.text
    payload = account.json()
    assert payload["settings"]["secret_id_masked"] == "AKID****2345"
    assert payload["settings"]["mode"] == "live"
    assert payload["mock"] is False

    page = client.get("/", params={"month": "2026-07"})
    assert page.status_code == 200
    assert dummy_key not in page.text
    assert "AKID****2345" in page.text


def test_switch_back_to_mock(tmp_path: Path, monkeypatch) -> None:
    _patch_live_bundle(monkeypatch)
    client = _client(tmp_path)
    client.post(
        "/api/settings/credentials",
        json={"secret_id": "AKIDxxxxYYYY", "secret_key": "k", "month": "2026-07"},
    )
    r = client.post("/api/settings/mock")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "mock"
    assert body["secret_id_masked"] is None
    assert not (tmp_path / ".local-creds.json").exists()


def test_collect_auth_error_shown_without_leaking_key(tmp_path: Path, monkeypatch) -> None:
    dummy_key = "SECRETKEY_NEVER_LEAK_THIS_VALUE_12345"

    def boom(*_a, **_k):
        raise RuntimeError(f"AuthFailure.SecretIdNotFound secret={dummy_key}")

    _patch_live_bundle(monkeypatch)
    monkeypatch.setattr("cos_cost.web.service.collect", boom)
    client = _client(tmp_path)
    r = client.post(
        "/api/settings/credentials",
        json={
            "secret_id": "AKIDabcdefghijklmnopqrstuvwxyz012345",
            "secret_key": dummy_key,
            "month": "2026-07",
        },
    )
    assert r.status_code == 200, r.text
    assert dummy_key not in r.text
    body = r.json()
    assert body["mode"] == "live"
    assert "鉴权失败" in (body.get("last_collect_error") or "")
    page = client.get("/", params={"month": "2026-07"})
    assert page.status_code == 200
    assert dummy_key not in page.text
    assert "鉴权失败" in page.text


def test_status_reads_local_creds_file(tmp_path: Path, monkeypatch) -> None:
    _patch_live_bundle(monkeypatch)
    creds_path = tmp_path / ".local-creds.json"
    save_local_creds(
        Credentials("AKIDabcdefghijklmnopqrstuvwxyz012345", "SECRETKEY_NEVER_LEAK"),
        creds_path,
        month="2026-07",
    )
    service = DashboardService(
        mock=False, cache_dir=tmp_path / "cache", creds_path=creds_path
    )
    status = service.settings_status()
    assert status["mode"] == "live"
    assert status["secret_id_masked"] == "AKID****2345"
    assert "SECRETKEY_NEVER_LEAK" not in json.dumps(status)


def test_classify_cos_invalid_access_key() -> None:
    from cos_cost.secrets import classify_collect_error

    raw = (
        "{'code': 'InvalidAccessKeyId', 'message': "
        "'The Access Key Id you provided does not exist in our records'}"
    )
    assert "鉴权失败" in classify_collect_error(raw)


def test_missing_credentials_returns_400_without_key(tmp_path: Path) -> None:
    client = _client(tmp_path)
    dummy_key = "SECRETKEY_NEVER_LEAK_THIS_VALUE_12345"
    r = client.post(
        "/api/settings/credentials",
        json={"secret_id": "", "secret_key": dummy_key},
    )
    assert r.status_code == 400
    assert dummy_key not in r.text
    assert r.json()["detail"]


def test_health_includes_mode(tmp_path: Path) -> None:
    client = _client(tmp_path)
    r = client.get("/api/health")
    assert r.json()["ok"] is True
    assert r.json()["mode"] in ("mock", "live")
