"""组装 mock / 线上只读客户端。线上路径惰性导入 SDK。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from cos_cost.clients.mock import load_fixture, mock_bundle
from cos_cost.clients.protocols import ClientBundle
from cos_cost.secrets import Credentials


def build_bundle(
    *,
    mock: bool,
    creds: Credentials | None = None,
    fixture: dict[str, Any] | None = None,
    fixture_path: Path | None = None,
) -> ClientBundle:
    if mock:
        data = fixture if fixture is not None else load_fixture(fixture_path)
        return mock_bundle(data)

    if creds is None:
        raise RuntimeError("线上模式需要 Credentials")
    from cos_cost.clients.live_billing import LiveBillingClient
    from cos_cost.clients.live_cos import LiveCosClient
    from cos_cost.clients.live_monitor import LiveMonitorClient

    account = (os.environ.get("COS_APPID") or "").strip() or "unknown"
    return ClientBundle(
        account_key=account,
        cos=LiveCosClient(creds),
        billing=LiveBillingClient(creds),
        monitor=LiveMonitorClient(creds),
        mock=False,
    )
