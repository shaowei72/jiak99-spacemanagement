"""Availability board (§4.5) and day metrics (§4.6)."""

import pytest

from engine import DayState, Price, Settings, Slot, availability, day_metrics
from engine_helpers import DEFAULTS, TODAY, schedule, session, t


def slot_at(board: tuple[Slot, ...], start: str, ticket_type: str) -> Slot:
    return next(s for s in board if s.start == t(start) and s.ticket_type == ticket_type)


def test_board_shows_available_full_and_not_offered() -> None:
    # Four zones taken at 14:00 by groups of just 1 person: the slot is sold out all the same.
    day = schedule(
        session("11:00", "PREMIUM", 20),
        *(session("14:00", "PREMIUM_CT", 1) for _ in range(4)),
    )
    board = availability(day, DEFAULTS, TODAY, 4, today=TODAY)

    assert len(board) == 22 * 4  # start times × ticket types
    assert slot_at(board, "11:00", "PREMIUM") == Slot(t("11:00"), "PREMIUM", "available")
    assert slot_at(board, "09:00", "CLASSIC").status == "available"
    assert slot_at(board, "15:00", "CLASSIC").status == "full"
    assert slot_at(board, "19:30", "PREMIUM").status == "not_offered"


def test_board_for_a_big_group_needs_enough_free_zones() -> None:
    day = schedule(*(session("10:00", "PREMIUM_CT", 1) for _ in range(3)))
    assert (
        slot_at(availability(day, DEFAULTS, TODAY, 25, today=TODAY), "10:00", "PREMIUM").status
        == "available"
    )
    assert (
        slot_at(availability(day, DEFAULTS, TODAY, 26, today=TODAY), "10:00", "PREMIUM").status
        == "full"
    )


def test_metrics_of_a_full_morning() -> None:
    prices = {
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
    metrics = day_metrics(day, Settings(prices={"PREMIUM": Price(2000, 1000)}))
    assert metrics.revenue_per_zone_hour_cents == pytest.approx(10 * 2000 / 3.5)


def test_metrics_of_an_empty_day() -> None:
    metrics = day_metrics(DayState(TODAY), DEFAULTS)
    assert metrics.seat_hour_use == 0
    assert metrics.average_fill is None
    assert metrics.revenue_per_zone_hour_cents is None
    assert metrics.dead_gap_hours == 0


def test_metrics_of_a_day_with_clashing_pins() -> None:
    clash = schedule(
        session("09:00", "PREMIUM_CT", 5, zone="A", pinned=True),
        session("10:00", "CLASSIC", 5, zone="A", pinned=True),
    )[TODAY]
    assert day_metrics(clash, DEFAULTS).dead_gap_hours is None
