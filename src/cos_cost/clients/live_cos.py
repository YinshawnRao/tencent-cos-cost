"""COS 只读：GET Service（list_buckets）与可选 HeadBucket。禁止 GetBucket。"""

from __future__ import annotations

from typing import Any

from qcloud_cos import CosConfig, CosS3Client
from qcloud_cos.cos_exception import CosClientError, CosServiceError

from cos_cost.clients.errors import PermissionDeniedError, is_permission_error
from cos_cost.clients.parse import parse_buckets
from cos_cost.models import BucketInfo
from cos_cost.secrets import Credentials


class LiveCosClient:
    def __init__(self, creds: Credentials, *, region: str = "ap-guangzhou") -> None:
        config = CosConfig(
            Region=region,
            SecretId=creds.secret_id,
            SecretKey=creds.secret_key,
            Token=creds.token,
            Scheme="https",
        )
        self._client = CosS3Client(config)

    def list_buckets(self) -> tuple[str | None, list[BucketInfo]]:
        merged: dict[str, Any] = {}
        buckets: list[BucketInfo] = []
        owner_id: str | None = None
        marker: str | None = None
        while True:
            kwargs: dict[str, Any] = {}
            if marker:
                kwargs["Marker"] = marker
            try:
                resp = self._client.list_buckets(**kwargs)
            except (CosServiceError, CosClientError) as exc:
                if is_permission_error(exc):
                    raise PermissionDeniedError("cos", str(exc)) from exc
                raise
            if not isinstance(resp, dict):
                break
            if not merged:
                merged = dict(resp)
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
        try:
            resp = self._client.head_bucket(Bucket=bucket)
        except (CosServiceError, CosClientError):
            return fallback_region
        if not isinstance(resp, dict):
            return fallback_region
        region = (
            resp.get("x-cos-bucket-region")
            or resp.get("X-Cos-Bucket-Region")
            or resp.get("x-cos-region")
        )
        return str(region) if region else fallback_region
