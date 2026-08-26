"""Fixture / mock 客户端：无网络、无 AK/SK。"""

from __future__ import annotations

import json
from calendar import monthrange
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
from cos_cost.monthutil import parse_month

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

    def _config(self, bucket: str) -> dict[str, Any]:
        block = (self.fixture.get("bucket_config") or {}).get(bucket) or {}
        return block if isinstance(block, dict) else {}

    def get_bucket_lifecycle(self, bucket: str, region: str | None) -> dict[str, Any]:
        if self.deny:
            raise PermissionDeniedError("cos", "mock: GetBucketLifecycle denied")
        raw = self._config(bucket).get("lifecycle")
        return raw if isinstance(raw, dict) else {}

    def get_bucket_versioning(self, bucket: str, region: str | None) -> dict[str, Any]:
        if self.deny:
            raise PermissionDeniedError("cos", "mock: GetBucketVersioning denied")
        status = self._config(bucket).get("versioning")
        if isinstance(status, dict):
            return status
        if status:
            return {"Status": status}
        return {}

    def get_bucket_logging(self, bucket: str, region: str | None) -> dict[str, Any]:
        if self.deny:
            raise PermissionDeniedError("cos", "mock: GetBucketLogging denied")
        raw = self._config(bucket).get("logging")
        return raw if isinstance(raw, dict) else {}

    def list_bucket_inventory(self, bucket: str, region: str | None) -> list[dict[str, Any]]:
        if self.deny:
            raise PermissionDeniedError("cos", "mock: GetBucketInventory denied")
        raw = self._config(bucket).get("inventory")
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, dict):
            return [raw]
        return []


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
        extras = {
            "StdMultipartStorage": ("multipart_storage_bytes", MB_TO_BYTES),
            "InternalTraffic": ("internal_traffic_bytes", 1.0),
            "CdnOriginTraffic": ("cdn_traffic_bytes", 1.0),
            "GetRequests": ("get_requests", 1.0),
            "PutRequests": ("put_requests", 1.0),
            "4xxResponse": ("err_4xx", 1.0),
            "5xxResponse": ("err_5xx", 1.0),
        }
        for metric, (attr, scale) in extras.items():
            series = raw.get(metric) or {}
            for name, value in series.items():
                if name not in by_bucket:
                    continue
                setattr(by_bucket[name], attr, float(value) * scale)
        for name, metrics in by_bucket.items():
            _attach_daily(metrics, month, raw, name)
        return MonitorSnapshot(by_bucket=by_bucket)


def _attach_daily(
    metrics: MonitorBucketMetrics, month: str, raw: dict[str, Any], name: str
) -> None:
    year, mon = (int(part) for part in parse_month(month).split("-"))
    days = monthrange(year, mon)[1]
    dates = [f"{year:04d}-{mon:02d}-{day:02d}" for day in range(1, days + 1)]
    override = (raw.get("daily") or {}).get(name)
    if isinstance(override, dict) and override.get("dates"):
        metrics.dates = [str(d) for d in override["dates"]]
        daily: dict[str, list[float | None]] = {}
        for key, values in override.items():
            if key == "dates" or not isinstance(values, list):
                continue
            daily[str(key)] = [None if v is None else float(v) for v in values]
        metrics.daily = daily
        return
    metrics.dates = dates
    daily = {}
    if metrics.std_storage_bytes is not None:
        daily["StdStorage"] = _vary_last(metrics.std_storage_bytes / MB_TO_BYTES, days)
    if metrics.sia_storage_bytes is not None:
        daily["SiaStorage"] = _vary_last(metrics.sia_storage_bytes / MB_TO_BYTES, days)
    if metrics.arc_storage_bytes is not None:
        daily["ArcStorage"] = _vary_last(metrics.arc_storage_bytes / MB_TO_BYTES, days)
    if metrics.deep_arc_storage_bytes is not None:
        daily["DeepArcStorage"] = _vary_last(metrics.deep_arc_storage_bytes / MB_TO_BYTES, days)
    if metrics.multipart_storage_bytes is not None:
        daily["StdMultipartStorage"] = _vary_last(
            metrics.multipart_storage_bytes / MB_TO_BYTES, days
        )
    if metrics.internet_traffic_bytes is not None:
        daily["InternetTraffic"] = _vary_sum(metrics.internet_traffic_bytes, days)
    if metrics.internal_traffic_bytes is not None:
        daily["InternalTraffic"] = _vary_sum(metrics.internal_traffic_bytes, days)
    if metrics.cdn_traffic_bytes is not None:
        daily["CdnOriginTraffic"] = _vary_sum(metrics.cdn_traffic_bytes, days)
    if metrics.get_requests is not None:
        daily["GetRequests"] = _vary_sum(metrics.get_requests, days)
    if metrics.put_requests is not None:
        daily["PutRequests"] = _vary_sum(metrics.put_requests, days)
    if metrics.err_4xx is not None:
        daily["4xxResponse"] = _vary_sum(metrics.err_4xx, days)
    if metrics.err_5xx is not None:
        daily["5xxResponse"] = _vary_sum(metrics.err_5xx, days)
    metrics.daily = daily


def _vary_last(last: float, n: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(n):
        wave = 1.0 + 0.03 * ((i % 7) - 3) / 3.0
        out.append(max(0.0, last * wave * (0.92 + 0.08 * (i + 1) / n)))
    return out


def _vary_sum(total: float, n: int) -> list[float | None]:
    if n <= 0:
        return []
    weights = [1.0 + 0.25 * ((i % 7) - 3) / 3.0 for i in range(n)]
    scale = total / sum(weights) if sum(weights) else 0.0
    return [max(0.0, w * scale) for w in weights]


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
