import pytest

from src.date_navigation import inclusive_dates, parse_iso_date, validate_date_range


def test_valid_date_and_inclusive_range():
    start, end = validate_date_range("2026-07-01", "2026-07-03")
    assert [item.isoformat() for item in inclusive_dates(start, end)] == ["2026-07-01", "2026-07-02", "2026-07-03"]


@pytest.mark.parametrize("value", ["2026/07/01", "26-07-01", "2026-7-01", ""])
def test_invalid_format(value):
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        parse_iso_date(value)


def test_nonexistent_date():
    with pytest.raises(ValueError, match="존재하지 않는"):
        parse_iso_date("2026-02-30")


def test_end_before_start():
    with pytest.raises(ValueError, match="빠를 수 없습니다"):
        validate_date_range("2026-07-02", "2026-07-01")

