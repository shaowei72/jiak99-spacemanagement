"""Unit tests for date arithmetic, routes and the dead-slot rule."""

import datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from engine import Blackout, Settings, Visit, assign, dead_gaps, route_visits
from engine.dates import add_days, days_between, is_valid_date, to_day_number, weekday
from engine.rules import ROUTES, START_TIMES, is_valid_route
from engine.text import duration_label
from engine.timeline import round_up_to_grid, window_mask
from engine_helpers import DEFAULTS, pinned, t

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


# --- routes ---------------------------------------------------------------------------------


def test_start_times_run_to_20_30() -> None:
    assert (START_TIMES[0], START_TIMES[-1], len(START_TIMES)) == (t("09:00"), t("20:30"), 24)


def test_number_of_routes_per_ticket() -> None:
    counts = {ticket_type: len(routes) for ticket_type, routes in ROUTES.items()}
    assert counts == {"TINGKAT": 3, "CLASSIC": 6, "CLASSIC_CT": 12, "PREMIUM": 24, "PREMIUM_CT": 24}


def test_route_validity() -> None:
    assert is_valid_route("CLASSIC", "BA")
    assert is_valid_route("CLASSIC_CT", "BDA")
    assert not is_valid_route("CLASSIC_CT", "BCA")  # Classic + CT always visits D
    assert not is_valid_route("CLASSIC", "AA")
    assert not is_valid_route("PREMIUM", "")


def test_premium_ct_visits() -> None:
    assert route_visits(t("11:00"), "PREMIUM_CT", "ABDC") == (
        Visit("A", t("11:00"), t("11:30")),
        Visit("B", t("11:30"), t("12:30")),
        Visit("D", t("12:30"), t("13:00")),
        Visit("C", t("13:00"), t("14:00")),
    )


def test_duration_labels() -> None:
    assert [duration_label(ticket_type) for ticket_type in ROUTES] == [
        "30 min",
        "1 h",
        "1.5 h",
        "2 h",
        "3 h",
    ]


def test_empty_window_has_no_slots() -> None:
    assert window_mask(5, 5) == 0
    assert window_mask(30, 40) == 0


def test_round_up_to_grid() -> None:
    assert round_up_to_grid(t("10:45")) == t("11:00")
    assert round_up_to_grid(t("11:00")) == t("11:00")
    assert round_up_to_grid(t("11:01")) == t("11:30")


# --- dead slots -----------------------------------------------------------------------------


def test_empty_day_has_no_dead_slots() -> None:
    plan = assign([], DEFAULTS)
    assert plan.ok
    assert plan.dead_minutes == 0


def test_dead_slots_are_merged_per_zone() -> None:
    # Everything is blacked out except 12:00–12:30. Only a Tingkat (30 min) would fit there,
    # and Tingkat doesn't count, so all four zones are dead for that half hour.
    blackouts = (Blackout(t("09:00"), t("12:00")), Blackout(t("12:30"), t("21:00")))
    plan = assign([], DEFAULTS, blackouts=blackouts)
    assert plan.dead_minutes == 4 * 30
    gaps = dead_gaps([], {}, DEFAULTS, blackouts)
    assert [(g.zone_id, g.start, g.end) for g in gaps] == [
        (zone, t("12:00"), t("12:30")) for zone in "ABCD"
    ]


def test_blackout_cells_are_never_dead() -> None:
    blackouts = (Blackout(t("09:00"), t("21:00")),)
    assert assign([], DEFAULTS, blackouts=blackouts).dead_minutes == 0


def test_off_grid_blackout_blocks_the_whole_slot() -> None:
    """A blackout from 12:15 takes the 12:00–12:30 slot too; 09:00–12:00 is still sellable."""
    blackouts = (Blackout(t("12:15"), t("21:00")),)
    plan = assign([], DEFAULTS, blackouts=blackouts)
    assert plan.dead_minutes == 0


def test_reset_time_is_never_dead() -> None:
    cfg = Settings(turnaround_minutes=15)
    fixed = pinned("09:00", "CLASSIC", "AD")
    gaps = dead_gaps([fixed], {fixed.id: "AD"}, cfg)
    starts = {(g.zone_id, g.start) for g in gaps}
    assert ("A", t("09:30")) not in starts
    assert ("D", t("10:00")) not in starts
