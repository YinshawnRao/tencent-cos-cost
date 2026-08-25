from __future__ import annotations

from pathlib import Path

import pytest

from cos_cost.clients.mock import load_fixture, mock_bundle


@pytest.fixture
def fixture_data() -> dict:
    return load_fixture()


@pytest.fixture
def mock_clients(fixture_data):
    return mock_bundle(fixture_data)


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "cache"
