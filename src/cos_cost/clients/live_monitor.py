"""云监控只读：GetMonitorData。Region 固定 ap-guangzhou。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from tencentcloud.common import credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.monitor.v20180724 import models, monitor_client

from cos_cost import MONITOR_NAMESPACE, MONITOR_REGION
from cos_cost.clients.errors import PermissionDeniedError, check_cancel, is_permission_error
from cos_cost.clients.protocols import model_to_dict
from cos_cost.limits import monitor_batch
from cos_cost.models import MonitorBucketMetrics, MonitorSnapshot
from cos_cost.monthutil import month_bounds_utc8
from cos_cost.secrets import Credentials

# 官方文档（2018-07-24）单次最多 10 个实例；产品说明 50。默认 50，可用 COS_MONITOR_BATCH 改。
MONITOR_MAX_INSTANCES = 50
# 存储类：MB，取 last；流量类：B，取 sum。Period=86400。
STORAGE_METRICS = {
    "StdStorage": "std_storage_bytes",
    "MazStdStorage": "maz_std_storage_bytes",
    "SiaStorage": "sia_storage_bytes",
    "MazIaStorage": "maz_ia_storage_bytes",
    "ArcStorage": "arc_storage_bytes",
    "DeepArcStorage": "deep_arc_storage_bytes",
}
TRAFFIC_METRICS = {
    "InternetTraffic": "internet_traffic_bytes",
    "InternalTraffic": "internal_traffic_bytes",
    "CdnOriginTraffic": "cdn_traffic_bytes",
}
COUNT_METRICS = {
    "GetRequests": "get_requests",
    "PutRequests": "put_requests",
    "4xxResponse": "err_4xx",
    "5xxResponse": "err_5xx",
}
MB_TO_BYTES = 1_000_000.0


class LiveMonitorClient:
    def __init__(self, creds: Credentials) -> None:
        cred = credential.Credential(creds.secret_id, creds.secret_key, creds.token)
        http_profile = HttpProfile()
        http_profile.endpoint = "monitor.tencentcloudapi.com"
        client_profile = ClientProfile()
        client_profile.httpProfile = http_profile
        self._client = monitor_client.MonitorClient(cred, MONITOR_REGION, client_profile)
        self._cancel = None

    def set_cancel(self, cancel) -> None:
        self._cancel = cancel

    def pull_cos_metrics(
        self,
        month: str,
        buckets: list[str],
        *,
        metrics: tuple[str, ...] | list[str] | None = None,
        cancel=None,
        progress=None,
    ) -> MonitorSnapshot:
        if not buckets:
            return MonitorSnapshot()
        ev = cancel if cancel is not None else self._cancel
        wanted = set(metrics) if metrics is not None else None
        start, end = month_bounds_utc8(month)
        start_iso = start.isoformat()
        # EndTime 用账期最后一秒（UTC+8），避免落到下一月。
        end_iso = (end - timedelta(seconds=1)).isoformat()
        by_bucket: dict[str, MonitorBucketMetrics] = {
            name: MonitorBucketMetrics(bucket=name) for name in buckets
        }
        notes: list[str] = []
        try:
            for metric, attr in STORAGE_METRICS.items():
                if wanted is not None and metric not in wanted:
                    continue
                self._fill(
                    metric, buckets, start_iso, end_iso, by_bucket, attr,
                    kind="last_mb", cancel=ev, progress=progress,
                )
            if wanted is None or "InternetTraffic" in wanted:
                self._fill(
                    "InternetTraffic",
                    buckets,
                    start_iso,
                    end_iso,
                    by_bucket,
                    "internet_traffic_bytes",
                    kind="sum_bytes",
                    cancel=ev,
                    progress=progress,
                )
            for metric, attr in {
                "InternalTraffic": "internal_traffic_bytes",
                "CdnOriginTraffic": "cdn_traffic_bytes",
                **COUNT_METRICS,
            }.items():
                if wanted is not None and metric not in wanted:
                    continue
                self._fill(
                    metric,
                    buckets,
                    start_iso,
                    end_iso,
                    by_bucket,
                    attr,
                    kind="sum_bytes",
                    optional=True,
                    cancel=ev,
                    progress=progress,
                )
            if wanted is None or "StdMultipartStorage" in wanted:
                self._fill(
                    "StdMultipartStorage",
                    buckets,
                    start_iso,
                    end_iso,
                    by_bucket,
                    "multipart_storage_bytes",
                    kind="last_mb",
                    optional=True,
                    cancel=ev,
                    progress=progress,
                )
        except PermissionDeniedError:
            raise
        except TencentCloudSDKException as exc:
            if is_permission_error(exc):
                raise PermissionDeniedError("monitor", str(exc)) from exc
            notes.append(f"监控拉取部分失败: {exc}")
        return MonitorSnapshot(by_bucket=by_bucket, notes=notes)

    def _fill(
        self,
        metric: str,
        buckets: list[str],
        start_iso: str,
        end_iso: str,
        by_bucket: dict[str, MonitorBucketMetrics],
        attr: str,
        *,
        kind: str,
        optional: bool = False,
        cancel=None,
        progress=None,
    ) -> None:
        batch = monitor_batch()
        chunks = _chunks(buckets, batch)
        total = len(buckets)
        done = 0
        for chunk in chunks:
            check_cancel(cancel)
            if progress is not None:
                progress.update(phase="监控", buckets_done=done, buckets_total=total)
            req = models.GetMonitorDataRequest()
            params = {
                "Namespace": MONITOR_NAMESPACE,
                "MetricName": metric,
                "Period": 86400,
                "StartTime": start_iso,
                "EndTime": end_iso,
                "Instances": [
                    {"Dimensions": [{"Name": "bucket", "Value": name}]} for name in chunk
                ],
            }
            req.from_json_string(__import__("json").dumps(params))
            try:
                resp = self._client.GetMonitorData(req)
            except TencentCloudSDKException as exc:
                if is_permission_error(exc):
                    raise PermissionDeniedError("monitor", str(exc)) from exc
                if optional:
                    return
                raise
            payload = model_to_dict(resp)
            done += len(chunk)
            if progress is not None:
                progress.update(phase="监控", buckets_done=min(done, total), buckets_total=total)
            for point in payload.get("DataPoints") or []:
                if not isinstance(point, dict):
                    continue
                name = _bucket_from_dimensions(point.get("Dimensions") or [])
                if not name or name not in by_bucket:
                    continue
                values = [v for v in (point.get("Values") or []) if v is not None]
                if not values:
                    continue
                if kind == "last_mb":
                    setattr(by_bucket[name], attr, float(values[-1]) * MB_TO_BYTES)
                else:
                    setattr(by_bucket[name], attr, float(sum(float(v) for v in values)))
                raw = by_bucket[name].raw.setdefault(metric, {})
                raw["values"] = values
                raw["timestamps"] = point.get("Timestamps")
                _merge_daily(by_bucket[name], metric, point.get("Timestamps") or [], values, kind)


def _bucket_from_dimensions(dims: Any) -> str | None:
    if not isinstance(dims, list):
        return None
    for dim in dims:
        if not isinstance(dim, dict):
            continue
        if str(dim.get("Name") or dim.get("name")) == "bucket":
            value = dim.get("Value") or dim.get("value")
            return str(value) if value else None
    return None


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _merge_daily(
    metrics: MonitorBucketMetrics,
    metric: str,
    timestamps: list[Any],
    values: list[Any],
    kind: str,
) -> None:
    if not timestamps or not values:
        return
    from datetime import datetime, timezone

    dates: list[str] = []
    series: list[float | None] = []
    for ts, value in zip(timestamps, values, strict=False):
        try:
            if isinstance(ts, (int, float)):
                dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            else:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            dates.append(dt.date().isoformat())
        except (TypeError, ValueError, OSError):
            continue
        try:
            series.append(None if value is None else float(value))
        except (TypeError, ValueError):
            series.append(None)
    if not dates:
        return
    if not metrics.dates:
        metrics.dates = dates
    metrics.daily[metric] = series
    _ = kind
