"""Fixture / mock 客户端：无网络、无 AK/SK。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cos_cost.clients.errors import PermissionDeniedError
from cos_cost.clients.parse import (
    is_cos_business,
    parse_bill_resource,
    parse_bill_summary_by_product,
    parse_buckets,
)
from cos_cost.models import BillResourceRow, BillSummary, BucketInfo, MonitorBucketMetrics, MonitorSnapshot

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "mock_account.json"
MB_TO_BYTES = 1_000_000.0

STORAGE_ATTR = {
    "StdStorage": "std_storage_bytes",
    "MazStdStorage": "maz_std_storage_bytes",
    "SiaStorage": "sia_storage_bytes",
    "MazIaStorage": "maz_ia_storage_bytes",
    "ArcStorage": "arc_storage_bytes",
    "DeepArcStorage": "deep_arc_storage_bytes",
}


def load_fixture(path: Path | None = None) -> dict[str, Any]:
    target = path or FIXTURE_PATH
    return json.loads(target.read_text(encoding="utf-8"))


class MockCosClient:
    def __init__(self, fixture: dict[str, Any], *, deny: bool = False) -> None:
        self.fixture = fixture
        self.deny = deny

    def list_buckets(self) -> tuple[str | None, list[BucketInfo]]:
        if self.deny:
            raise PermissionDeniedError("cos", "mock: GetService denied")
        payload = {
            "Owner": {"ID": self.fixture.get("appid"), "DisplayName": self.fixture.get("appid")},
            "Buckets": {"Bucket": self.fixture.get("buckets") or []},
        }
        return parse_buckets(payload)

    def head_bucket_region(self, bucket: str, fallback_region: str | None) -> str | None:
        if self.deny:
            return fallback_region
        for item in self.fixture.get("buckets") or []:
            if item.get("Name") == bucket:
                return item.get("Location") or fallback_region
        return fallback_region


class MockBillingClient:
    def __init__(self, fixture: dict[str, Any], *, deny: bool = False) -> None:
        self.fixture = fixture
        self.deny = deny

    def describe_bill_summary_by_product(self, month: str) -> BillSummary:
        if self.deny:
            raise PermissionDeniedError("billing", "mock: DescribeBillSummaryByProduct denied")
        block = self._month(month)
        payload = block.get("summary_by_product") or {"Ready": 0, "SummaryOverview": []}
        return parse_bill_summary_by_product(month, payload)

    def describe_bill_resource_summary(self, month: str) -> list[BillResourceRow]:
        if self.deny:
            raise PermissionDeniedError("billing", "mock: DescribeBillResourceSummary denied")
        block = self._month(month)
        rows: list[BillResourceRow] = []
        for item in block.get("resources") or []:
            if not isinstance(item, dict):
                continue
            row = parse_bill_resource(item)
            if is_cos_business(row):
                rows.append(row)
        return rows

    def _month(self, month: str) -> dict[str, Any]:
        months = self.fixture.get("months") or {}
        block = months.get(month)
        if not isinstance(block, dict):
            return {"ready": 0, "summary_by_product": {"Ready": 0}, "resources": []}
        return block


class MockMonitorClient:
    def __init__(self, fixture: dict[str, Any], *, deny: bool = False) -> None:
        self.fixture = fixture
        self.deny = deny

    def pull_cos_metrics(self, month: str, buckets: list[str]) -> MonitorSnapshot:
        if self.deny:
            raise PermissionDeniedError("monitor", "mock: GetMonitorData denied")
        months = self.fixture.get("months") or {}
        block = months.get(month) or {}
        raw = block.get("monitor") or {}
        by_bucket: dict[str, MonitorBucketMetrics] = {
            name: MonitorBucketMetrics(bucket=name) for name in buckets
        }
        for metric, attr in STORAGE_ATTR.items():
            series = raw.get(metric) or {}
            for name, mb in series.items():
                if name not in by_bucket:
                    continue
                setattr(by_bucket[name], attr, float(mb) * MB_TO_BYTES)
                by_bucket[name].raw[metric] = {"last_mb": mb}
        traffic = raw.get("InternetTraffic") or {}
        for name, raw_bytes in traffic.items():
            if name not in by_bucket:
                continue
            by_bucket[name].internet_traffic_bytes = float(raw_bytes)
            by_bucket[name].raw["InternetTraffic"] = {"sum_bytes": raw_bytes}
        return MonitorSnapshot(by_bucket=by_bucket)


def mock_bundle(
    fixture: dict[str, Any] | None = None,
    *,
    deny_bill: bool = False,
    deny_monitor: bool = False,
    deny_cos: bool = False,
):
    from cos_cost.clients.protocols import ClientBundle

    data = fixture or load_fixture()
    account = str(data.get("appid") or "mock")
    return ClientBundle(
        account_key=account,
        cos=MockCosClient(data, deny=deny_cos),
        billing=MockBillingClient(data, deny=deny_bill),
        monitor=MockMonitorClient(data, deny=deny_monitor),
        mock=True,
    )
