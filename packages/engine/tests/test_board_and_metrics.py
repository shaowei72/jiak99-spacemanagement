"""Availability board (§4.5) and day metrics (§4.6)."""

import pytest

from engine import (
    DayState,
    Price,
    Settings,
    Slot,
    availability,
    day_metrics,
    seat_hour_use,
    zone_hour_use,
)
from engine.rules import TicketType
from engine_helpers import DEFAULTS, TODAY, pinned, schedule, session, t


def slot_at(board: tuple[Slot, ...], start: str, ticket_type: str) -> Slot:
    return next(s for s in board if s.start == t(start) and s.ticket_type == ticket_type)


def test_board_shows_available_full_and_not_offered() -> None:
    # Four groups of just 1 person at 14:00 fill every zone slot to 16:00: sold out all the same.
    day = schedule(
        session("11:00", "PREMIUM", 20),
        *(session("14:00", "PREMIUM", 1) for _ in range(4)),
    )
    board = availability(day, DEFAULTS, TODAY, 4, today=TODAY)

    assert len(board) == 24 * 5  # start times × ticket types
    assert slot_at(board, "11:00", "PREMIUM") == Slot(t("11:00"), "PREMIUM", "available")
    assert slot_at(board, "09:00", "CLASSIC").status == "available"
    assert slot_at(board, "15:00", "CLASSIC").status == "full"
    assert slot_at(board, "15:00", "TINGKAT").status == "full"
    assert slot_at(board, "19:30", "PREMIUM").status == "not_offered"
    assert slot_at(board, "20:30", "CLASSIC").status == "not_offered"
    assert slot_at(board, "20:30", "TINGKAT").status == "available"


def test_board_for_a_big_group_needs_a_route_per_sub_group() -> None:
    day = schedule(*(session("10:00", "PREMIUM", 1) for _ in range(3)))
    board_25 = availability(day, DEFAULTS, TODAY, 25, today=TODAY)
    board_26 = availability(day, DEFAULTS, TODAY, 26, today=TODAY)
    assert slot_at(board_25, "10:00", "PREMIUM").status == "available"
    assert slot_at(board_26, "10:00", "PREMIUM").status == "full"


def test_metrics_of_a_full_morning() -> None:
    prices: dict[TicketType, Price] = {
        "CLASSIC": Price(1000, 500),
        "PREMIUM": Price(2000, 1000),
        "PREMIUM_CT": Price(3000, 1500),
    }
    day = schedule(*(session("09:00", "PREMIUM", 25) for _ in range(4)))[TODAY]
    metrics = day_metrics(day, Settings(prices=prices))

    assert metrics.seat_hour_use == pytest.approx(200 / 1200)
    assert metrics.zone_hour_use == pytest.approx(8 / 48)
    assert metrics.average_fill == 1.0
    assert metrics.dead_gap_hours == 0
    assert metrics.revenue_per_zone_hour_cents == pytest.approx(100 * 2000 / 8)


def test_metrics_with_prices_for_some_ticket_types_only() -> None:
    day = schedule(session("09:00", "PREMIUM", 10), session("09:00", "CLASSIC", 10))[TODAY]
    premium_only: dict[TicketType, Price] = {"PREMIUM": Price(2000, 1000)}
    metrics = day_metrics(day, Settings(prices=premium_only))
    assert metrics.revenue_per_zone_hour_cents == pytest.approx(10 * 2000 / 3)


def test_metrics_of_an_empty_day() -> None:
    metrics = day_metrics(DayState(TODAY), DEFAULTS)
    assert metrics.seat_hour_use == 0
    assert metrics.average_fill is None
    assert metrics.revenue_per_zone_hour_cents is None
    assert metrics.dead_gap_hours == 0


def test_metrics_of_a_day_with_clashing_pins() -> None:
    clash = schedule(
        pinned("09:00", "PREMIUM_CT", "ABCD"),
        pinned("09:00", "CLASSIC", "AD"),  # both in A at 09:00
    )[TODAY]
    assert day_metrics(clash, DEFAULTS).dead_gap_hours is None


def test_usage_without_the_full_metrics() -> None:
    """The 6-month calendar uses these two alone (no zone plan needed)."""
    day = schedule(session("09:00", "PREMIUM_CT", 25), session("12:00", "CLASSIC", 10))[TODAY]
    assert seat_hour_use(day.sessions) == pytest.approx((25 * 3 + 10 * 1) / 1200)
    assert zone_hour_use(day.sessions) == pytest.approx(4 / 48)
    assert seat_hour_use(()) == 0
