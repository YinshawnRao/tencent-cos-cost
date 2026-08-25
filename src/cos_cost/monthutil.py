"""账期：默认「上一自然月」，时区 UTC+8（Asia/Shanghai）。"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ_UTC8 = ZoneInfo("Asia/Shanghai")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def previous_month_utc8(now: datetime | None = None) -> str:
    """返回 UTC+8 下当前日期的上一自然月，格式 YYYY-MM。"""
    current = (now or datetime.now(TZ_UTC8)).astimezone(TZ_UTC8).date()
    first = current.replace(day=1)
    prev = first - timedelta(days=1)
    return f"{prev.year:04d}-{prev.month:02d}"


def parse_month(value: str) -> str:
    match = _MONTH_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"账期必须是 YYYY-MM，收到: {value!r}")
    year = int(match.group(1))
    month = int(match.group(2))
    if month < 1 or month > 12:
        raise ValueError(f"非法月份: {value!r}")
    if year < 2019:
        raise ValueError("账单 2.0 不早于 2019-03，请传入 2019-03 及之后的账期")
    return f"{year:04d}-{month:02d}"


def shift_month(month: str, delta_months: int) -> str:
    parsed = parse_month(month)
    year, mon = (int(part) for part in parsed.split("-"))
    total = year * 12 + (mon - 1) + delta_months
    if total < 0:
        raise ValueError("账期下溢")
    return f"{total // 12:04d}-{(total % 12) + 1:02d}"


def month_bounds_utc8(month: str) -> tuple[datetime, datetime]:
    """账期在 UTC+8 的闭开区间 [start, end)。"""
    parsed = parse_month(month)
    year, mon = (int(part) for part in parsed.split("-"))
    start = datetime(year, mon, 1, tzinfo=TZ_UTC8)
    if mon == 12:
        end = datetime(year + 1, 1, 1, tzinfo=TZ_UTC8)
    else:
        end = datetime(year, mon + 1, 1, tzinfo=TZ_UTC8)
    return start, end


def month_last_date(month: str) -> date:
    _start, end = month_bounds_utc8(month)
    return (end - timedelta(days=1)).date()
