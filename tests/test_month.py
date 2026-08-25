from datetime import datetime

from cos_cost.monthutil import parse_month, previous_month_utc8, shift_month, TZ_UTC8


def test_previous_month_utc8_end_of_month() -> None:
    now = datetime(2026, 8, 25, 10, 0, tzinfo=TZ_UTC8)
    assert previous_month_utc8(now) == "2026-07"


def test_previous_month_january() -> None:
    now = datetime(2026, 1, 3, tzinfo=TZ_UTC8)
    assert previous_month_utc8(now) == "2025-12"


def test_parse_and_shift() -> None:
    assert parse_month("2026-07") == "2026-07"
    assert shift_month("2026-07", -1) == "2026-06"
    assert shift_month("2026-01", -1) == "2025-12"
    assert shift_month("2026-07", -12) == "2025-07"
