from calendarutils import is_leap_year


def test_divisible_by_4_not_100() -> None:
    assert is_leap_year(2024) is True
    assert is_leap_year(2023) is False


def test_centurial_years() -> None:
    assert is_leap_year(1900) is False
    assert is_leap_year(2000) is True
