"""Unit tests for date arithmetic and the dead-minutes rule."""

import datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from engine import Settings
from engine.dates import add_days, days_between, is_valid_date, to_day_number, weekday
from engine.timeline import Block, dead_gaps_in_zone, dead_minutes_in_gap, round_up_to_grid
from engine_helpers import t

# --- dates (checked against Python's datetime, which the engine itself doesn't use) ---------


@given(st.dates(min_value=datetime.date(1900, 1, 1), max_value=datetime.date(2200, 12, 31)))
def test_day_numbers_match_datetime(day: datetime.date) -> None:
    epoch = datetime.date(1970, 1, 1)
    assert to_day_number(day.isoformat()) == (day - epoch).days


@given(
    st.dates(min_value=datetime.date(2000, 1, 1), max_value=datetime.date(2100, 1, 1)),
    st.integers(min_value=-400, max_value=400),
)
def test_add_days_matches_datetime(day: datetime.date, offset: int) -> None:
    expected = (day + datetime.timedelta(days=offset)).isoformat()
    assert add_days(day.isoformat(), offset) == expected
    assert days_between(day.isoformat(), expected) == offset


@pytest.mark.parametrize("bad", ["2026-02-29", "2026-13-01", "2026-04-31", "26-1-1", "", "x"])
def test_invalid_dates_are_rejected(bad: str) -> None:
    assert not is_valid_date(bad)


def test_leap_day_and_weekday() -> None:
    assert is_valid_date("2028-02-29")
    assert weekday("2026-12-01") == "Tue"


# --- dead minutes ---------------------------------------------------------------------------


def test_round_up_to_grid() -> None:
    assert round_up_to_grid(t("10:45")) == t("11:00")
    assert round_up_to_grid(t("11:00")) == t("11:00")
    assert round_up_to_grid(t("11:01")) == t("11:30")


@pytest.mark.parametrize(
    ("start", "end", "ends_at_session", "turnaround", "dead"),
    [
        ("09:00", "10:00", True, 0, 60),  # 60 min: no ticket fits
        ("09:00", "10:30", True, 0, 0),  # exactly 1.5 h fits
        ("09:00", "10:30", True, 15, 90),  # 1.5 h + 15 min reset doesn't fit
        ("19:30", "21:00", False, 15, 0),  # no reset needed before closing
        ("11:00", "11:00", True, 0, 0),  # back to back: no gap at all
    ],
)
def test_dead_minutes_in_gap(
    start: str, end: str, ends_at_session: bool, turnaround: int, dead: int
) -> None:
    cfg = Settings(turnaround_minutes=turnaround)
    assert (
        dead_minutes_in_gap(t(start), t(end), ends_at_session=ends_at_session, settings=cfg) == dead
    )


def test_turnaround_rounding_is_not_dead_time() -> None:
    """09:00 1.5 h + 15 min reset ends 10:45; 10:45–11:00 is structural, not dead (T9)."""
    cfg = Settings(turnaround_minutes=15)
    blocks = [Block(t("09:00"), t("10:45"), True), Block(t("11:00"), t("12:45"), True)]
    assert dead_gaps_in_zone("A", blocks, cfg) == []


def test_gap_before_a_blackout_needs_no_turnaround() -> None:
    cfg = Settings(turnaround_minutes=15)
    blocks = [Block(t("09:00"), t("10:45"), True), Block(t("12:30"), t("14:00"), False)]
    # 11:00–12:30 is exactly 1.5 h; a ticket fits because a blackout follows, not a session.
    assert dead_gaps_in_zone("A", blocks, cfg) == []
