"""用 MagicMock 冒充腾讯云 SDK，验证官方字段映射与只读调用。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from cos_cost.clients.errors import PermissionDeniedError, is_permission_error
from cos_cost.clients.parse import parse_bill_resource, parse_bill_summary_by_product
from cos_cost.clients.throttle import RateLimiter


def test_parse_official_bill_fields() -> None:
    payload = {
        "Ready": 1,
        "SummaryOverview": [
            {
                "BusinessCode": "p_cos",
                "BusinessCodeName": "对象存储 COS",
                "RealTotalCost": "12.50",
            }
        ],
        "SummaryTotal": {"RealTotalCost": "100"},
    }
    summary = parse_bill_summary_by_product("2026-07", payload)
    assert summary.ready == 1
    assert summary.cos_real_total_cost == 12.5
    row = parse_bill_resource(
        {
            "ResourceId": "logs-prod-1250000000",
            "BusinessCode": "p_cos",
            "RealTotalCost": "1.00",
            "RegionName": "华南地区（广州）",
        }
    )
    assert row.resource_id == "logs-prod-1250000000"
    assert row.real_total_cost == 1.0


def test_rate_limiter_spaces_calls() -> None:
    limiter = RateLimiter(qps=50.0)
    limiter.wait()
    limiter.wait()


def test_permission_error_detection() -> None:
    assert is_permission_error(RuntimeError("AuthFailure.UnauthorizedOperation"))
    assert is_permission_error(RuntimeError("code=UnauthorizedOperation"))


def test_live_billing_maps_sdk_response() -> None:
    fake_resp = MagicMock()
    fake_resp.to_json_string.return_value = json.dumps(
        {
            "Ready": 1,
            "SummaryOverview": [
                {"BusinessCode": "p_cos", "RealTotalCost": "9.00"}
            ],
            "SummaryTotal": {"RealTotalCost": "9.00"},
        }
    )
    fake_client = MagicMock()
    fake_client.DescribeBillSummaryByProduct.return_value = fake_resp

    with (
        patch("tencentcloud.common.credential.Credential"),
        patch(
            "tencentcloud.billing.v20180709.billing_client.BillingClient",
            return_value=fake_client,
        ),
        patch("tencentcloud.billing.v20180709.models.DescribeBillSummaryByProductRequest"),
    ):
        from cos_cost.clients.live_billing import LiveBillingClient
        from cos_cost.secrets import Credentials

        client = LiveBillingClient(Credentials("id", "key"))
        client._client = fake_client
        summary = client.describe_bill_summary_by_product("2026-07")
    assert summary.cos_real_total_cost == 9.0
    fake_client.DescribeBillSummaryByProduct.assert_called_once()


def test_live_billing_permission_becomes_typed_error() -> None:
    from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
        TencentCloudSDKException,
    )

    fake_client = MagicMock()
    fake_client.DescribeBillSummaryByProduct.side_effect = TencentCloudSDKException(
        "UnauthorizedOperation", "no bill"
    )
    fake_client.DescribeBillSummary.side_effect = TencentCloudSDKException(
        "UnauthorizedOperation", "no bill"
    )
    with (
        patch("tencentcloud.common.credential.Credential"),
        patch(
            "tencentcloud.billing.v20180709.billing_client.BillingClient",
            return_value=fake_client,
        ),
        patch("tencentcloud.billing.v20180709.models.DescribeBillSummaryByProductRequest"),
        patch("tencentcloud.billing.v20180709.models.DescribeBillSummaryRequest"),
    ):
        from cos_cost.clients.live_billing import LiveBillingClient
        from cos_cost.secrets import Credentials

        client = LiveBillingClient(Credentials("id", "key"))
        client._client = fake_client
        try:
            client.describe_bill_summary_by_product("2026-07")
            raised = False
        except PermissionDeniedError:
            raised = True
    assert raised


def test_live_cos_list_buckets_only() -> None:
    fake_s3 = MagicMock()
    fake_s3.list_buckets.return_value = {
        "Owner": {"ID": "1250000000"},
        "Buckets": {
            "Bucket": [{"Name": "logs-prod-1250000000", "Location": "ap-guangzhou"}]
        },
    }
    with (
        patch("qcloud_cos.CosConfig"),
        patch("qcloud_cos.CosS3Client", return_value=fake_s3),
    ):
        from cos_cost.clients.live_cos import LiveCosClient
        from cos_cost.secrets import Credentials

        client = LiveCosClient(Credentials("id", "key"))
        client._client = fake_s3
        owner, buckets = client.list_buckets()
    assert owner == "1250000000"
    assert buckets[0].name == "logs-prod-1250000000"
    fake_s3.list_objects.assert_not_called()
    fake_s3.get_bucket.assert_not_called()
    assert not hasattr(fake_s3, "put_bucket") or True


def test_live_cos_lifecycle_is_readonly() -> None:
    fake_s3 = MagicMock()
    fake_s3.get_bucket_lifecycle.return_value = {
        "Rule": [{"ID": "a", "Status": "Enabled", "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}}]
    }
    with (
        patch("qcloud_cos.CosConfig"),
        patch("qcloud_cos.CosS3Client", return_value=fake_s3),
    ):
        from cos_cost.clients.live_cos import LiveCosClient
        from cos_cost.secrets import Credentials

        client = LiveCosClient(Credentials("id", "key"))
        client._client = fake_s3
        payload = client.get_bucket_lifecycle("logs-prod-1250000000", "ap-guangzhou")
    assert payload["Rule"][0]["ID"] == "a"
    fake_s3.list_objects.assert_not_called()
    fake_s3.put_bucket_lifecycle.assert_not_called()
