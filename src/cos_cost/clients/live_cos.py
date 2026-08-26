"""COS 只读：GET Service、HeadBucket、Lifecycle / Versioning / Logging / Inventory。

禁止 List Objects（GetBucket）、禁止任何 Put / Delete。
"""

from __future__ import annotations

import threading
from typing import Any

from qcloud_cos import CosConfig, CosS3Client
from qcloud_cos.cos_exception import CosClientError, CosServiceError

from cos_cost.clients.errors import PermissionDeniedError, check_cancel, is_permission_error
from cos_cost.clients.parse import parse_buckets
from cos_cost.clients.throttle import RateLimiter
from cos_cost.limits import cos_max_inflight, cos_qps
from cos_cost.models import BucketInfo
from cos_cost.secrets import Credentials

_MISSING_CODES = (
    "nosuchlifecycleconfiguration",
    "nosuchbucketpolicy",
    "no such lifecycle",
    "inventoryconfigurationnotfounderror",
    "no such inventory",
    "logging not found",
)


def _is_missing(exc: BaseException) -> bool:
    text = str(exc).lower()
    if any(code in text.replace(" ", "") or code in text for code in _MISSING_CODES):
        return True
    code = getattr(exc, "get_error_code", None)
    if callable(code):
        token = str(code() or "").lower().replace(" ", "")
        return token in {
            "nosuchlifecycleconfiguration",
            "inventoryconfigurationnotfounderror",
            "nosuchkey",
        }
    return False


class LiveCosClient:
    def __init__(
        self,
        creds: Credentials,
        *,
        region: str = "ap-guangzhou",
        limiter: RateLimiter | None = None,
        inflight: threading.Semaphore | None = None,
    ) -> None:
        self._creds = creds
        self._default_region = region
        self._clients: dict[str, CosS3Client] = {}
        self._clients[region] = self._make(region)
        self._limiter = limiter or RateLimiter(cos_qps())
        self._inflight = inflight or threading.Semaphore(cos_max_inflight())
        self._cancel: threading.Event | None = None

    def set_cancel(self, cancel: threading.Event | None) -> None:
        self._cancel = cancel
        self._limiter.set_cancel(cancel)

    @property
    def _client(self) -> CosS3Client:
        return self._s3(self._default_region)

    @_client.setter
    def _client(self, value: CosS3Client) -> None:
        self._clients[self._default_region] = value

    def _make(self, region: str) -> CosS3Client:
        config = CosConfig(
            Region=region or self._default_region,
            SecretId=self._creds.secret_id,
            SecretKey=self._creds.secret_key,
            Token=self._creds.token,
            Scheme="https",
        )
        return CosS3Client(config)

    def _s3(self, region: str | None) -> CosS3Client:
        key = region or self._default_region
        if key not in self._clients:
            self._clients[key] = self._make(key)
        return self._clients[key]

    def list_buckets(self) -> tuple[str | None, list[BucketInfo]]:
        buckets: list[BucketInfo] = []
        owner_id: str | None = None
        marker: str | None = None
        while True:
            kwargs: dict[str, Any] = {}
            if marker:
                kwargs["Marker"] = marker
            try:
                check_cancel(self._cancel)
                self._limiter.wait(self._cancel)
                resp = self._s3(self._default_region).list_buckets(**kwargs)
            except (CosServiceError, CosClientError) as exc:
                if is_permission_error(exc):
                    raise PermissionDeniedError("cos", str(exc)) from exc
                raise
            if not isinstance(resp, dict):
                break
            page_owner, page_buckets = parse_buckets(resp)
            if page_owner:
                owner_id = page_owner
            buckets.extend(page_buckets)
            truncated = str(resp.get("IsTruncated") or resp.get("isTruncated") or "").lower()
            marker = resp.get("NextMarker") or resp.get("nextMarker")
            if truncated not in {"true", "1"} or not marker:
                break
        return owner_id, buckets

    def head_bucket_region(self, bucket: str, fallback_region: str | None) -> str | None:
        check_cancel(self._cancel)
        self._limiter.wait(self._cancel)
        acquired = self._acquire()
        try:
            resp = self._s3(fallback_region).head_bucket(Bucket=bucket)
        except (CosServiceError, CosClientError):
            return fallback_region
        finally:
            if acquired:
                self._inflight.release()
        if not isinstance(resp, dict):
            return fallback_region
        region = (
            resp.get("x-cos-bucket-region")
            or resp.get("X-Cos-Bucket-Region")
            or resp.get("x-cos-region")
        )
        return str(region) if region else fallback_region

    def get_bucket_lifecycle(self, bucket: str, region: str | None) -> dict[str, Any]:
        return self._read_dict("get_bucket_lifecycle", bucket, region)

    def get_bucket_versioning(self, bucket: str, region: str | None) -> dict[str, Any]:
        return self._read_dict("get_bucket_versioning", bucket, region)

    def get_bucket_logging(self, bucket: str, region: str | None) -> dict[str, Any]:
        return self._read_dict("get_bucket_logging", bucket, region)

    def list_bucket_inventory(self, bucket: str, region: str | None) -> list[dict[str, Any]]:
        client = self._s3(region)
        method = getattr(client, "list_bucket_inventory_configurations", None)
        if callable(method):
            raw = self._call(method, bucket, allow_missing=True)
            if isinstance(raw, dict):
                node = (
                    raw.get("InventoryConfiguration")
                    or raw.get("InventoryConfigurations")
                    or raw.get("InventoryConfigurationList")
                )
                if isinstance(node, list):
                    return [i for i in node if isinstance(i, dict)]
                if isinstance(node, dict):
                    return [node]
            if isinstance(raw, list):
                return [i for i in raw if isinstance(i, dict)]
        getter = getattr(client, "get_bucket_inventory", None)
        if callable(getter):
            raw = self._call(getter, bucket, allow_missing=True)
            if isinstance(raw, dict) and raw:
                return [raw]
        return []

    def _read_dict(self, method_name: str, bucket: str, region: str | None) -> dict[str, Any]:
        method = getattr(self._s3(region), method_name, None)
        if not callable(method):
            return {}
        raw = self._call(method, bucket, allow_missing=True)
        return raw if isinstance(raw, dict) else {}

    def _acquire(self) -> bool:
        while True:
            check_cancel(self._cancel)
            if self._inflight.acquire(timeout=0.2):
                return True

    def _call(self, method: Any, bucket: str, *, allow_missing: bool) -> Any:
        check_cancel(self._cancel)
        self._limiter.wait(self._cancel)
        self._acquire()
        try:
            return method(Bucket=bucket)
        except (CosServiceError, CosClientError) as exc:
            if allow_missing and _is_missing(exc):
                return {}
            if is_permission_error(exc):
                raise PermissionDeniedError("cos", str(exc)) from exc
            return {}
        finally:
            self._inflight.release()
