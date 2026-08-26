"""M1 客户端协议。M2/M3 可在此继续加只读方法，禁止 List Objects / 写接口。"""

from __future__ import annotations

from typing import Any, Protocol

from cos_cost.models import BillResourceRow, BillSummary, BucketInfo, MonitorSnapshot


class CosReadClient(Protocol):
    def list_buckets(self) -> tuple[str | None, list[BucketInfo]]:
        """GET Service。返回 (Owner.ID / APPID, buckets)。"""

    def head_bucket_region(self, bucket: str, fallback_region: str | None) -> str | None:
        """可选 HeadBucket，仅确认地域。禁止 GetBucket（列对象）。"""

    def get_bucket_lifecycle(self, bucket: str, region: str | None) -> dict[str, Any]:
        """GetBucketLifecycle。缺失规则时返回空 dict。禁止 List Objects。"""

    def get_bucket_versioning(self, bucket: str, region: str | None) -> dict[str, Any]:
        """GetBucketVersioning。"""

    def get_bucket_logging(self, bucket: str, region: str | None) -> dict[str, Any]:
        """GetBucketLogging。"""

    def list_bucket_inventory(self, bucket: str, region: str | None) -> list[dict[str, Any]]:
        """ListBucketInventory / GetBucketInventory。不读清单 CSV、不 List Objects。"""


class BillingReadClient(Protocol):
    def describe_bill_summary_by_product(self, month: str) -> BillSummary:
        """DescribeBillSummaryByProduct（BeginTime/EndTime = YYYY-MM）。"""

    def describe_bill_resource_summary(self, month: str) -> list[BillResourceRow]:
        """DescribeBillResourceSummary，BusinessCode=p_cos，Limit<=1000，5/s。"""


class MonitorReadClient(Protocol):
    def pull_cos_metrics(self, month: str, buckets: list[str]) -> MonitorSnapshot:
        """GetMonitorData，Region 固定 ap-guangzhou，Namespace=QCE/COS。"""


class ClientBundle:
    def __init__(
        self,
        *,
        account_key: str,
        cos: CosReadClient,
        billing: BillingReadClient,
        monitor: MonitorReadClient,
        mock: bool = False,
    ) -> None:
        self.account_key = account_key
        self.cos = cos
        self.billing = billing
        self.monitor = monitor
        self.mock = mock


def model_to_dict(model: Any) -> dict[str, Any]:
    if model is None:
        return {}
    to_json = getattr(model, "to_json_string", None)
    if callable(to_json):
        import json

        raw = to_json()
        if isinstance(raw, str):
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
    if hasattr(model, "_serialize"):
        serialized = model._serialize()
        if isinstance(serialized, dict):
            return serialized
    if isinstance(model, dict):
        return model
    return {"repr": repr(model)}
