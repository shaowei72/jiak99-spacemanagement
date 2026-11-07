"""Enquiry-check rules beyond the acceptance tests: validity, zone sharing, splits, warnings."""

import pytest

from engine import (
    Blackout,
    DayState,
    Enquiry,
    EnquiryResult,
    NewPlacement,
    Price,
    SearchBudget,
    Settings,
    SplitPlacement,
    add_days,
    assign,
    check_enquiry,
)
from engine.alternatives import _Candidate, _rank
from engine.rules import TICKET_TYPES, TicketType, is_downgrade
from engine_helpers import DEFAULTS, TODAY, enquiry, schedule, session, t


def check(day: dict[str, DayState], request: Enquiry, cfg: Settings = DEFAULTS, **kw: int) -> str:
    return check_enquiry(day, cfg, request, today=TODAY, **kw).reason  # type: ignore[arg-type]


# --- rule 1: validity -----------------------------------------------------------------------


def test_off_grid_start_is_not_possible() -> None:
    result = check_enquiry(schedule(), DEFAULTS, enquiry("10:15", "CLASSIC", 4), today=TODAY)
    assert result.verdict == "NOT_POSSIBLE"
    assert "every 30 min" in result.reason


def test_zero_people_is_not_possible() -> None:
    assert "at least 1 person" in check(schedule(), enquiry("10:00", "CLASSIC", 0))


def test_unknown_ticket_type_is_not_possible() -> None:
    assert "Unknown ticket type" in check(schedule(), enquiry("10:00", "T60", 4))  # type: ignore[arg-type]


def test_session_pinned_to_unknown_zone_makes_day_infeasible() -> None:
    assert not assign([session("10:00", "CLASSIC", 4, zone="E", pinned=True)], DEFAULTS).ok


def test_past_date_and_past_time() -> None:
    yesterday = add_days(TODAY, -1)
    assert "in the past" in check(schedule(), enquiry("10:00", "CLASSIC", 4, date=yesterday))
    assert "already passed" in check(
        schedule(), enquiry("10:00", "CLASSIC", 4), now_minute=t("12:00")
    )


def test_invalid_date_has_no_other_day_alternatives() -> None:
    result = check_enquiry(
        schedule(), DEFAULTS, enquiry("10:00", "CLASSIC", 4, date="2026-02-30"), today=TODAY
    )
    assert result.verdict == "NOT_POSSIBLE"
    assert "not a valid date" in result.reason
    assert all(a.date == "2026-02-30" for a in result.alternatives)


def test_closed_day() -> None:
    day = {TODAY: DayState(TODAY, closed=True)}
    assert "closed" in check(day, enquiry("10:00", "CLASSIC", 4))


def test_blackout_blocks_overlapping_requests_only() -> None:
    day = {TODAY: DayState(TODAY, blackouts=(Blackout(t("12:00"), t("13:00")),))}
    assert "12:00–13:00 blackout" in check(day, enquiry("11:00", "PREMIUM", 4))
    result = check_enquiry(day, DEFAULTS, enquiry("13:00", "PREMIUM", 4), today=TODAY)
    assert result.verdict != "NOT_POSSIBLE"


# --- a zone taken by a group is sold out -----------------------------------------------------


@pytest.mark.parametrize("ticket_type", ["PREMIUM", "CLASSIC", "CLASSIC_CT"])
def test_a_group_never_shares_a_zone(ticket_type: TicketType) -> None:
    """Same slot, same or other ticket type: the 5-person zone is sold out all the same."""
    existing = session("11:00", "PREMIUM", 5)
    result = check_enquiry(
        schedule(existing), DEFAULTS, enquiry("11:00", ticket_type, 6), today=TODAY
    )
    assert result.placement == NewPlacement(people=6)
    assert result.zone_minutes_added > 0


def test_alternatives_never_suggest_joining_a_group() -> None:
    small_group = session("10:30", "PREMIUM", 5)
    result = check_enquiry(
        schedule(small_group), DEFAULTS, enquiry("10:00", "PREMIUM", 4), today=TODAY
    )
    assert result.alternatives
    assert all("Join" not in a.gain_summary for a in result.alternatives)
    assert all(a.verdict == "ACCEPT_GOOD" for a in result.alternatives)


# --- groups over 25 -------------------------------------------------------------------------


def test_big_group_takes_whole_zones_even_if_the_slot_has_spare_seats() -> None:
    existing = session("09:00", "PREMIUM", 20)
    result = check_enquiry(
        schedule(existing), DEFAULTS, enquiry("09:00", "PREMIUM", 30), today=TODAY
    )
    assert result.placement == SplitPlacement(new_sessions=(25, 5))
    assert result.reason.startswith("Group of 30 at 09:00 · Premium needs 2 zones (25 + 5).")


def test_big_group_needing_more_zones_than_free() -> None:
    day = schedule(*(session("09:00", "PREMIUM_CT", 25) for _ in range(3)))
    reason = check(day, enquiry("10:00", "PREMIUM", 40))
    assert reason == "Only 1 zone free at 10:00; this group needs 2."


def test_pinned_sessions_can_block_without_all_zones_busy() -> None:
    # A and B are pinned with gaps that don't line up; C and D are busy all day.
    day = schedule(
        session("09:00", "PREMIUM", 5, zone="A", pinned=True),
        session("12:00", "PREMIUM_CT", 5, zone="A", pinned=True),
        session("10:00", "PREMIUM", 5, zone="B", pinned=True),
        session("13:00", "PREMIUM_CT", 5, zone="B", pinned=True),
        *(
            session(start, "PREMIUM_CT", 5, zone=zone, pinned=True)
            for zone in "CD"
            for start in ("09:00", "12:00", "15:00", "18:00")
        ),
    )
    reason = check(day, enquiry("11:00", "PREMIUM", 4))
    assert reason == "No zone is free for 11:00–13:00 without moving pinned or locked sessions."


# --- costly warnings ------------------------------------------------------------------------


def test_small_group_warning_is_advisory() -> None:
    cfg = Settings(small_group_warn_below=8)
    result = check_enquiry(schedule(), cfg, enquiry("09:00", "PREMIUM", 4), today=TODAY)
    assert result.verdict == "ACCEPT_COSTLY"
    assert "only 4 people" in result.reason
    big_enough = check_enquiry(schedule(), cfg, enquiry("09:00", "PREMIUM", 8), today=TODAY)
    assert big_enough.verdict == "ACCEPT_GOOD"


def test_same_dead_gap_in_two_zones_is_counted_not_repeated() -> None:
    # All zones busy 09:00–10:30. A group of 50 at 11:00 needs two zones, each left with a
    # dead 10:30–11:00 gap.
    day = schedule(*(session("09:00", "CLASSIC", 25) for _ in range(4)))
    result = check_enquiry(day, DEFAULTS, enquiry("11:00", "CLASSIC", 50), today=TODAY)
    assert result.verdict == "ACCEPT_COSTLY"
    assert result.dead_minutes_delta == 60
    assert result.reason == (
        "Group of 50 at 11:00 · Classic needs 2 zones (25 + 25). "
        "Fits, but leaves 10:30–11:00 in 2 zones unsellable."
    )


# --- ranking --------------------------------------------------------------------------------


DOWNGRADES = {
    ("PREMIUM", "CLASSIC"),
    ("PREMIUM", "CLASSIC_CT"),
    ("PREMIUM_CT", "CLASSIC"),
    ("PREMIUM_CT", "CLASSIC_CT"),
    ("PREMIUM_CT", "PREMIUM"),
    ("CLASSIC_CT", "CLASSIC"),
    ("CLASSIC_CT", "PREMIUM"),
}


@pytest.mark.parametrize("asked", TICKET_TYPES)
@pytest.mark.parametrize("offered", TICKET_TYPES)
def test_what_counts_as_a_downgrade(asked: TicketType, offered: TicketType) -> None:
    assert is_downgrade(asked, offered) == ((asked, offered) in DOWNGRADES)


@pytest.mark.parametrize(
    ("start", "ticket_type"),
    [("19:30", "PREMIUM"), ("19:00", "CLASSIC_CT"), ("18:30", "PREMIUM_CT")],
)
def test_alternatives_never_suggest_a_downgrade(start: str, ticket_type: TicketType) -> None:
    result = check_enquiry(schedule(), DEFAULTS, enquiry(start, ticket_type, 8), today=TODAY)
    assert result.alternatives
    assert not any(is_downgrade(ticket_type, a.ticket_type) for a in result.alternatives)


def test_alternatives_can_suggest_an_upgrade() -> None:
    """19:00 Classic ends 20:30, leaving 30 dead minutes; Premium fills to 21:00."""
    result = check_enquiry(schedule(), DEFAULTS, enquiry("19:00", "CLASSIC", 8), today=TODAY)
    assert result.verdict == "ACCEPT_COSTLY"
    assert [(a.start, a.ticket_type) for a in result.alternatives] == [
        (t("19:30"), "CLASSIC"),
        (t("19:00"), "PREMIUM"),
        (t("18:00"), "CLASSIC"),
    ]
    assert result.alternatives[1].change_summary == "Premium instead of Classic"


def test_prices_break_ties_towards_higher_revenue() -> None:
    """Two otherwise equal upgrades: the one earning more ranks first."""
    asked = enquiry("10:00", "CLASSIC", 4)
    to_premium = _Candidate(TODAY, t("10:00"), "PREMIUM", 0, True, 0)
    to_premium_ct = _Candidate(TODAY, t("10:00"), "PREMIUM_CT", 0, True, 0)
    good = EnquiryResult("ACCEPT_GOOD", NewPlacement(4), 0, 120, "Fits.")
    prices = {"PREMIUM": Price(2000, 1000), "PREMIUM_CT": Price(3000, 1500)}

    def order(cfg: Settings) -> list[str]:
        ranked = sorted([to_premium_ct, to_premium], key=lambda c: _rank(c, good, asked, cfg))
        return [c.ticket_type for c in ranked]

    assert order(DEFAULTS) == ["PREMIUM", "PREMIUM_CT"]  # no prices: list order
    assert order(Settings(prices=prices)) == ["PREMIUM_CT", "PREMIUM"]


def test_expired_budget_still_gives_a_valid_answer() -> None:
    always_late = SearchBudget(0, clock=lambda: 1.0)
    day = schedule(*(session("09:00", "CLASSIC", 25) for _ in range(3)))
    result = check_enquiry(
        day, DEFAULTS, enquiry("11:00", "CLASSIC", 5), today=TODAY, budget=always_late
    )
    assert result.verdict in ("ACCEPT_GOOD", "ACCEPT_COSTLY")
    plan = assign(day[TODAY].sessions, DEFAULTS, budget=always_late)
    assert plan.ok
