"""Calendar arithmetic on 'YYYY-MM-DD' strings, without datetime objects.

Each date is converted to a day number (days since 1970-01-01), which makes adding days and
measuring distances simple integer maths. The conversion uses Howard Hinnant's well-known
civil-calendar algorithm.
"""

import re

_DATE_PATTERN = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _day_number(year: int, month: int, day: int) -> int:
    """Days since 1970-01-01 for a (proleptic Gregorian) calendar date."""
    if month <= 2:
        year -= 1
    era = year // 400
    year_of_era = year - era * 400
    shifted_month = month + 9 if month <= 2 else month - 3  # March = 0 ... February = 11
    day_of_year = (153 * shifted_month + 2) // 5 + day - 1
    day_of_era = year_of_era * 365 + year_of_era // 4 - year_of_era // 100 + day_of_year
    return era * 146097 + day_of_era - 719468


def _from_day_number(number: int) -> tuple[int, int, int]:
    """Inverse of _day_number: returns (year, month, day)."""
    number += 719468
    era = number // 146097
    day_of_era = number - era * 146097
    year_of_era = (
        day_of_era - day_of_era // 1460 + day_of_era // 36524 - day_of_era // 146096
    ) // 365
    day_of_year = day_of_era - (365 * year_of_era + year_of_era // 4 - year_of_era // 100)
    shifted_month = (5 * day_of_year + 2) // 153
    day = day_of_year - (153 * shifted_month + 2) // 5 + 1
    month = shifted_month + 3 if shifted_month < 10 else shifted_month - 9
    year = year_of_era + era * 400 + (1 if month <= 2 else 0)
    return year, month, day


def _format(year: int, month: int, day: int) -> str:
    return f"{year:04d}-{month:02d}-{day:02d}"


def to_day_number(date: str) -> int:
    """Convert 'YYYY-MM-DD' to a day number. Raises ValueError for anything that isn't a real date."""
    match = _DATE_PATTERN.match(date)
    if match is None:
        raise ValueError(f"not a YYYY-MM-DD date: {date!r}")
    year, month, day = (int(part) for part in match.groups())
    number = _day_number(year, month, day)
    # A round trip catches impossible dates such as 2026-02-30 or month 13.
    if _format(*_from_day_number(number)) != date:
        raise ValueError(f"not a real calendar date: {date!r}")
    return number


def from_day_number(number: int) -> str:
    """Convert a day number back to 'YYYY-MM-DD'."""
    return _format(*_from_day_number(number))


def is_valid_date(date: str) -> bool:
    try:
        to_day_number(date)
    except ValueError:
        return False
    return True


def add_days(date: str, days: int) -> str:
    """The date `days` after `date` (negative goes back)."""
    return from_day_number(to_day_number(date) + days)


def days_between(start: str, end: str) -> int:
    """How many days `end` is after `start` (negative if it is before)."""
    return to_day_number(end) - to_day_number(start)


def weekday(date: str) -> str:
    """Short weekday name, e.g. 'Tue'. 1970-01-01 was a Thursday."""
    return _WEEKDAYS[(to_day_number(date) + 3) % 7]
