from __future__ import annotations

from cos_cost.cli import main


def test_rank_mock_prints_table(cache_dir, capsys) -> None:
    code = main(["rank", "--mock", "--month", "2026-07", "--cache-dir", str(cache_dir)])
    assert code == 0
    out = capsys.readouterr().out
    assert "logs-prod-1250000000" in out
    assert "COS 应付" in out
    assert "桶排行" in out


def test_rank_mock_json(cache_dir, capsys) -> None:
    code = main(["rank", "--mock", "--month", "2026-07", "--json", "--cache-dir", str(cache_dir)])
    assert code == 0
    out = capsys.readouterr().out
    assert '"month": "2026-07"' in out
    assert "logs-prod-1250000000" in out


def test_collect_then_rank_mock(cache_dir, capsys) -> None:
    assert main(["collect", "--mock", "--month", "2026-07", "--cache-dir", str(cache_dir)]) == 0
    assert any(cache_dir.rglob("*.json"))
    assert main(["rank", "--mock", "--month", "2026-07", "--cache-dir", str(cache_dir)]) == 0
    assert "应付" in capsys.readouterr().out


def test_live_without_credentials_fails(monkeypatch, cache_dir, capsys) -> None:
    monkeypatch.delenv("COS_SECRET_ID", raising=False)
    monkeypatch.delenv("COS_SECRET_KEY", raising=False)
    code = main(["rank", "--month", "2026-07", "--cache-dir", str(cache_dir)])
    assert code == 2
    err = capsys.readouterr().err
    assert "COS_SECRET_ID" in err


def test_export_requires_output(cache_dir, capsys) -> None:
    code = main(["export", "--mock", "--month", "2026-07", "--cache-dir", str(cache_dir)])
    assert code == 2
    assert "--pdf" in capsys.readouterr().err


def test_cli_does_not_print_secret(monkeypatch, cache_dir, capsys) -> None:
    monkeypatch.setenv("COS_SECRET_ID", "AKIDdummy")
    monkeypatch.setenv("COS_SECRET_KEY", "super-secret-value-do-not-print")
    # mock 路径不读密钥；同时确保环境里的密钥不会出现在输出
    code = main(["rank", "--mock", "--month", "2026-07", "--cache-dir", str(cache_dir)])
    assert code == 0
    captured = capsys.readouterr()
    assert "super-secret-value-do-not-print" not in captured.out
    assert "super-secret-value-do-not-print" not in captured.err
