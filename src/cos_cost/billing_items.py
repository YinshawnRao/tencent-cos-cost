"""把账单 ProductCode / ProductCodeName 归到线框计费大类。不确定的归「其他」。"""

from __future__ import annotations

from collections import defaultdict

from cos_cost.models import BillResourceRow

CATEGORY_STORAGE = "storage"
CATEGORY_TRAFFIC = "traffic"
CATEGORY_REQUEST = "request"
CATEGORY_RETRIEVAL = "retrieval"
CATEGORY_OTHER = "other"

CATEGORY_LABELS = {
    CATEGORY_STORAGE: "存储",
    CATEGORY_TRAFFIC: "流量",
    CATEGORY_REQUEST: "请求",
    CATEGORY_RETRIEVAL: "取回",
    CATEGORY_OTHER: "其他",
}

CATEGORY_COLORS = {
    CATEGORY_STORAGE: "#2563EB",
    CATEGORY_TRAFFIC: "#0891B2",
    CATEGORY_REQUEST: "#DB2777",
    CATEGORY_RETRIEVAL: "#64748B",
    CATEGORY_OTHER: "#94A3B8",
}

STORAGE_CLASS_COLORS = {
    "standard": "#2563EB",
    "ia": "#16A34A",
    "archive": "#D97706",
    "deep": "#7C3AED",
    "multipart": "#EA580C",
}

STORAGE_CLASS_LABELS = {
    "standard": "标准",
    "ia": "低频",
    "archive": "归档",
    "deep": "深度",
    "multipart": "碎片",
}


def classify_product(product_code: str | None, product_code_name: str | None) -> str:
    blob = f"{product_code or ''} {product_code_name or ''}".lower()
    if any(k in blob for k in ("retriev", "restore", "取回", "回热", "解冻")):
        return CATEGORY_RETRIEVAL
    if any(k in blob for k in ("traffic", "带宽", "流量", "外网", "cdn", "下行", "上行")):
        return CATEGORY_TRAFFIC
    if any(k in blob for k in ("req", "request", "请求", "api")):
        return CATEGORY_REQUEST
    if any(
        k in blob
        for k in (
            "std",
            "sia",
            "ia",
            "arc",
            "archive",
            "storage",
            "存储",
            "标准",
            "低频",
            "归档",
            "maz",
        )
    ):
        return CATEGORY_STORAGE
    if any(k in blob for k in ("manage", "管理", "inventory", "清单")):
        return CATEGORY_OTHER
    return CATEGORY_OTHER


def compose_categories(rows: list[BillResourceRow]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        if row.real_total_cost is None:
            continue
        totals[classify_product(row.product_code, row.product_code_name)] += row.real_total_cost
    return {key: float(val) for key, val in totals.items() if val}


def request_fee_total(rows: list[BillResourceRow]) -> float | None:
    composed = compose_categories(rows)
    if CATEGORY_REQUEST not in composed:
        return None
    return composed[CATEGORY_REQUEST]
