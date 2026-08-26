"""账单只读客户端。域名 billing.tencentcloudapi.com，版本 2018-07-09。"""

from __future__ import annotations

from tencentcloud.billing.v20180709 import billing_client, models
from tencentcloud.common import credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile

from cos_cost import BILLING_ENDPOINT, COS_BUSINESS_CODE
from cos_cost.clients.errors import PermissionDeniedError, is_permission_error
from cos_cost.clients.parse import (
    is_cos_business,
    parse_bill_resource,
    parse_bill_summary_by_product,
    parse_bill_summary_fallback,
)
from cos_cost.clients.protocols import model_to_dict
from cos_cost.clients.throttle import RateLimiter
from cos_cost.limits import billing_qps
from cos_cost.models import BillResourceRow, BillSummary
from cos_cost.secrets import Credentials

PAGE_LIMIT = 1000


class LiveBillingClient:
    def __init__(self, creds: Credentials, *, limiter: RateLimiter | None = None) -> None:
        cred = credential.Credential(creds.secret_id, creds.secret_key, creds.token)
        http_profile = HttpProfile()
        http_profile.endpoint = BILLING_ENDPOINT
        client_profile = ClientProfile()
        client_profile.httpProfile = http_profile
        # 账单为地域无关接口；SDK 仍要求 Region 字符串，传空。
        self._client = billing_client.BillingClient(cred, "", client_profile)
        self._limiter = limiter or RateLimiter(billing_qps())
        self._cancel = None

    def set_cancel(self, cancel) -> None:
        self._cancel = cancel
        self._limiter.set_cancel(cancel)

    def describe_bill_summary_by_product(self, month: str) -> BillSummary:
        req = models.DescribeBillSummaryByProductRequest()
        req.BeginTime = month
        req.EndTime = month
        try:
            resp = self._client.DescribeBillSummaryByProduct(req)
        except TencentCloudSDKException as exc:
            if is_permission_error(exc):
                raise PermissionDeniedError("billing", str(exc)) from exc
            # 部分账号仅开通了 DescribeBillSummary
            return self._describe_bill_summary(month)
        payload = model_to_dict(resp)
        return parse_bill_summary_by_product(month, payload)

    def _describe_bill_summary(self, month: str) -> BillSummary:
        req = models.DescribeBillSummaryRequest()
        req.Month = month
        req.GroupType = "business"
        try:
            resp = self._client.DescribeBillSummary(req)
        except TencentCloudSDKException as exc:
            if is_permission_error(exc):
                raise PermissionDeniedError("billing", str(exc)) from exc
            raise
        return parse_bill_summary_fallback(month, model_to_dict(resp))

    def describe_bill_resource_summary(self, month: str) -> list[BillResourceRow]:
        rows: list[BillResourceRow] = []
        offset = 0
        while True:
            self._limiter.wait(self._cancel)
            req = models.DescribeBillResourceSummaryRequest()
            req.Offset = offset
            req.Limit = PAGE_LIMIT
            req.Month = month
            req.NeedRecordNum = 1
            req.BusinessCode = COS_BUSINESS_CODE
            # 与费用中心示例一致；需与该月账单统计周期相同。
            req.PeriodType = "byPayTime"
            try:
                resp = self._client.DescribeBillResourceSummary(req)
            except TencentCloudSDKException as exc:
                if is_permission_error(exc):
                    raise PermissionDeniedError("billing", str(exc)) from exc
                raise
            payload = model_to_dict(resp)
            items = payload.get("ResourceSummarySet") or []
            if not isinstance(items, list):
                items = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                row = parse_bill_resource(item)
                if is_cos_business(row):
                    rows.append(row)
            total = payload.get("Total")
            offset += PAGE_LIMIT
            if len(items) < PAGE_LIMIT:
                break
            if total is not None:
                try:
                    if offset >= int(total):
                        break
                except (TypeError, ValueError):
                    pass
        return rows
