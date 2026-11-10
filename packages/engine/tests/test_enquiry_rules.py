"""Enquiry-check rules beyond the acceptance tests: validity, zone slots, splits, warnings."""

from dataclasses import replace

import pytest

from engine import (
    Blackout,
    DayState,
    Enquiry,
    EnquiryResult,
    NewPlacement,
    Price,
    SearchBudget,
    Session,
    Settings,
    SplitPlacement,
    add_days,
    assign,
    check_enquiry,
)
from engine.alternatives import _Candidate, _rank
from engine.evaluate import group_sizes
from engine.model import SessionStatus
from engine.rules import TICKET_TYPES, TicketType, is_downgrade
from engine_helpers import DEFAULTS, TODAY, enquiry, pinned, schedule, session, t


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


@pytest.mark.parametrize(
    ("ticket_type", "route"),
    [
        ("PREMIUM", "ABC"),  # a zone missing
        ("CLASSIC", "BD"),  # every Classic visits A
        ("CLASSIC_CT", "ABC"),  # Classic + CT always visits D
        ("TINGKAT", "A"),  # Tingkat is never in A
        ("PREMIUM", "ABCE"),  # no zone E
    ],
)
def test_session_pinned_to_an_invalid_route_makes_day_infeasible(
    ticket_type: TicketType, route: str
) -> None:
    assert not assign([pinned("10:00", ticket_type, route)], DEFAULTS).ok


@pytest.mark.parametrize("status", ["open", "locked", "in_progress"])
def test_fixed_groups_must_stay_out_of_blackouts(status: SessionStatus) -> None:
    """A blackout added after booking must not cover a pinned or locked group either."""
    group = replace(pinned("09:00", "PREMIUM", "ABCD"), status=status)
    morning_off = [Blackout(t("09:00"), t("10:00"))]
    assert not assign([group], DEFAULTS, blackouts=morning_off).ok
    afternoon_off = [Blackout(t("14:00"), t("15:00"))]
    assert assign([group], DEFAULTS, blackouts=afternoon_off).ok


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


def test_ending_after_close_names_the_length() -> None:
    assert check(schedule(), enquiry("20:30", "CLASSIC", 4)) == (
        "A 1 h CHE from 20:30 would end after the 21:00 close."
    )
    assert check(schedule(), enquiry("19:30", "PREMIUM", 4)) == (
        "A 2 h PHE from 19:30 would end after the 21:00 close."
    )


@pytest.mark.parametrize(
    "broken",
    [
        Session("x", TODAY, t("10:15"), "CLASSIC", 4),  # off the 30-min grid
        Session("x", TODAY, t("08:30"), "CLASSIC", 4),  # before opening
        Session("x", TODAY, t("20:30"), "CLASSIC", 4),  # ends after closing
        Session("x", TODAY, t("10:00"), "T60", 4),  # type: ignore[arg-type]
    ],
)
def test_a_session_that_cannot_be_scheduled_makes_the_day_infeasible(broken: Session) -> None:
    assert not assign([broken], DEFAULTS).ok


def test_day_with_clashing_pins_still_explains_itself() -> None:
    day = schedule(pinned("10:00", "CLASSIC", "AD"), pinned("10:00", "PREMIUM", "ABCD"))
    result = check_enquiry(day, DEFAULTS, enquiry("10:00", "CLASSIC", 4), today=TODAY)
    assert result.verdict == "NOT_POSSIBLE"
    assert result.reason == "No order of zones fits 10:00–11:00 around the other groups."


# --- one group per zone slot ----------------------------------------------------------------


@pytest.mark.parametrize("ticket_type", ["TINGKAT", "CLASSIC", "CLASSIC_CT", "PREMIUM"])
def test_a_group_never_shares_a_zone_slot(ticket_type: TicketType) -> None:
    """Same start, same or other ticket type: the 5-person group's zone slots are sold out."""
    existing = session("11:00", "PREMIUM", 5)
    result = check_enquiry(
        schedule(existing), DEFAULTS, enquiry("11:00", ticket_type, 6), today=TODAY
    )
    assert isinstance(result.placement, NewPlacement)
    assert result.placement.people == 6
    assert result.zone_minutes_added > 0


# --- groups over 25 -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("people", "sizes"),
    [
        (0, ()),
        (1, (1,)),
        (25, (25,)),
        (26, (13, 13)),
        (27, (14, 13)),
        (40, (20, 20)),
        (51, (17, 17, 17)),
        (60, (20, 20, 20)),
        (101, (21, 20, 20, 20, 20)),
    ],
)
def test_groups_are_split_as_evenly_as_possible(people: int, sizes: tuple[int, ...]) -> None:
    assert group_sizes(people) == sizes


def test_split_reason_lists_sizes_and_routes() -> None:
    existing = session("09:00", "PREMIUM", 20)
    result = check_enquiry(
        schedule(existing), DEFAULTS, enquiry("09:00", "PREMIUM", 30), today=TODAY
    )
    assert isinstance(result.placement, SplitPlacement)
    assert result.placement.new_sessions == (15, 15)
    routes = ", ".join(result.placement.routes)
    assert result.reason == (
        f"Group of 30 at 09:00 · PHE is split into 2 groups (15 + 15). "
        f"Fits (routes {routes}) with no unsellable slots."
    )


def test_needed_zones_that_are_full_are_named() -> None:
    """Three PHE+CT groups at 09:00 fill zones B and C from 09:00 to 12:00; every PHE needs both."""
    day = schedule(*(session("09:00", "PREMIUM_CT", 25) for _ in range(3)))
    assert check(day, enquiry("10:00", "PREMIUM", 4)) == "Zones B and C are in use 10:00–12:00."


def test_split_group_short_of_a_needed_zone() -> None:
    reason = check(schedule(), enquiry("10:00", "CLASSIC", 60))
    assert reason == (
        "Group of 60 at 10:00 · CHE needs 3 groups (20 + 20 + 20), "
        "but Zone A has room for only 2 groups in 10:00–11:00. "
        "Try booking part of the group at another time."
    )


def test_no_route_fits_although_no_zone_is_full() -> None:
    # Every zone is taken at 10:00 and free at 10:30: a Classic at 10:00 has no order that works.
    day = schedule(
        pinned("09:30", "CLASSIC", "DA"),
        pinned("10:00", "TINGKAT", "B"),
        pinned("10:00", "TINGKAT", "C"),
        pinned("10:00", "TINGKAT", "D"),
    )
    assert check(day, enquiry("10:00", "CLASSIC", 4)) == (
        "No order of zones fits 10:00–11:00 around the other groups."
    )


def test_groups_that_could_move_are_not_blamed() -> None:
    """The current plan puts both groups in A at 09:30 and 10:00, but the CHE+CT group could
    visit A at 10:30 instead. So A is not "in use 09:30–10:30"; only the CHE group must be in
    A then, which leaves room for one more group, not two."""
    day = schedule(session("09:30", "CLASSIC", 10), session("09:30", "CLASSIC_CT", 10))
    assert check(day, enquiry("09:30", "CLASSIC", 40)) == (
        "Group of 40 at 09:30 · CHE needs 2 groups (20 + 20), "
        "but Zone A has room for only 1 group in 09:30–10:30. "
        "Try booking part of the group at another time."
    )


def test_groups_that_must_visit_a_zone_use_its_room() -> None:
    """Three PHE groups at 10:00 each visit A once between 10:00 and 12:00, in some order. No
    single A slot is taken in every plan, yet only one of the four can be free."""
    day = schedule(*(session("10:00", "PREMIUM", 20) for _ in range(3)))
    assert check(day, enquiry("10:00", "PREMIUM", 26)) == (
        "Group of 26 at 10:00 · PHE needs 2 groups (13 + 13), "
        "but Zone A has room for only 1 group in 10:00–12:00. "
        "Try booking part of the group at another time."
    )


def test_room_for_a_split_group_needs_free_slots_in_a_row() -> None:
    """A PHE+CT spends 60 min in B. B is free 10:00–11:30 and at 12:00, but taken at 11:30 and
    12:30: 4 free slots, yet only one 60-min visit fits."""
    day = schedule(pinned("11:30", "TINGKAT", "B"), pinned("12:30", "TINGKAT", "B"))
    assert check(day, enquiry("10:00", "PREMIUM_CT", 40)) == (
        "Group of 40 at 10:00 · PHE+CT needs 2 groups (20 + 20), "
        "but Zone B has room for only 1 group in 10:00–13:00. "
        "Try booking part of the group at another time."
    )


def test_reason_on_a_day_that_does_not_fit_as_it_is() -> None:
    """A pinned group under a blackout (stored before blackouts were checked) breaks the day.
    The reason then only counts the certain slots: the blackout and the fixed groups."""
    day = DayState(
        TODAY,
        sessions=(pinned("09:00", "PREMIUM", "ABCD"),),
        blackouts=(Blackout(t("09:00"), t("09:30")),),
    )
    assert check({TODAY: day}, enquiry("12:00", "CLASSIC", 4)) == (
        "No order of zones fits 12:00–13:00 around the other groups."
    )


# --- costly warnings ------------------------------------------------------------------------


def test_good_reason_names_the_route() -> None:
    result = check_enquiry(schedule(), DEFAULTS, enquiry("09:00", "PREMIUM", 8), today=TODAY)
    assert isinstance(result.placement, NewPlacement)
    assert result.reason == f"Fits (route {result.placement.route}) with no unsellable slots."


def test_small_group_warning_is_advisory() -> None:
    cfg = Settings(small_group_warn_below=8)
    result = check_enquiry(schedule(), cfg, enquiry("09:00", "PREMIUM", 4), today=TODAY)
    assert result.verdict == "ACCEPT_COSTLY"
    assert "only 4 people" in result.reason
    big_enough = check_enquiry(schedule(), cfg, enquiry("09:00", "PREMIUM", 8), today=TODAY)
    assert big_enough.verdict == "ACCEPT_GOOD"


def test_tingkat_is_ignored_when_judging_waste() -> None:
    """A free zone slot only Tingkat could use still counts as unsellable (T6 without pins)."""
    day = schedule(
        pinned("19:00", "PREMIUM", "ABCD"),
        pinned("19:00", "PREMIUM", "BCDA"),
        pinned("19:00", "PREMIUM", "CDAB"),
        pinned("19:00", "CLASSIC", "DA"),
    )
    plan = assign(day[TODAY].sessions, DEFAULTS)
    assert plan.ok
    assert plan.dead_minutes == 60


# --- ranking --------------------------------------------------------------------------------


DOWNGRADES = {
    ("PREMIUM", "CLASSIC"),
    ("PREMIUM", "CLASSIC_CT"),
    ("PREMIUM_CT", "CLASSIC"),
    ("PREMIUM_CT", "CLASSIC_CT"),
    ("PREMIUM_CT", "PREMIUM"),
    ("CLASSIC_CT", "CLASSIC"),
    ("CLASSIC_CT", "PREMIUM"),
    # Tingkat is below everything else, so it is never offered instead of another ticket.
    ("CLASSIC", "TINGKAT"),
    ("CLASSIC_CT", "TINGKAT"),
    ("PREMIUM", "TINGKAT"),
    ("PREMIUM_CT", "TINGKAT"),
}


@pytest.mark.parametrize("asked", TICKET_TYPES)
@pytest.mark.parametrize("offered", TICKET_TYPES)
def test_what_counts_as_a_downgrade(asked: TicketType, offered: TicketType) -> None:
    assert is_downgrade(asked, offered) == ((asked, offered) in DOWNGRADES)


@pytest.mark.parametrize(
    ("start", "ticket_type"),
    [("19:30", "PREMIUM"), ("20:00", "CLASSIC_CT"), ("18:30", "PREMIUM_CT"), ("20:30", "CLASSIC")],
)
def test_alternatives_never_suggest_a_downgrade(start: str, ticket_type: TicketType) -> None:
    result = check_enquiry(schedule(), DEFAULTS, enquiry(start, ticket_type, 8), today=TODAY)
    assert result.alternatives
    assert not any(is_downgrade(ticket_type, a.ticket_type) for a in result.alternatives)


def test_tingkat_request_can_be_offered_an_upgrade() -> None:
    """B, C and D are full at 10:00 but A is free all morning, and from 10:30 B is free again."""
    day = schedule(
        pinned("10:00", "TINGKAT", "B"),
        pinned("10:00", "TINGKAT", "C"),
        pinned("10:00", "TINGKAT", "D"),
    )
    result = check_enquiry(day, DEFAULTS, enquiry("10:00", "TINGKAT", 4), today=TODAY)
    assert result.verdict == "NOT_POSSIBLE"
    assert (t("10:00"), "CLASSIC") in [(a.start, a.ticket_type) for a in result.alternatives]


def test_prices_break_ties_towards_higher_revenue() -> None:
    """Two otherwise equal upgrades: the one earning more ranks first."""
    asked = enquiry("10:00", "CLASSIC", 4)
    to_premium = _Candidate(TODAY, t("10:00"), "PREMIUM", 0, True, 0)
    to_premium_ct = _Candidate(TODAY, t("10:00"), "PREMIUM_CT", 0, True, 0)
    good = EnquiryResult("ACCEPT_GOOD", NewPlacement(4, "ABCD"), 0, 120, "Fits.")
    prices: dict[TicketType, Price] = {
        "PREMIUM": Price(2000, 1000),
        "PREMIUM_CT": Price(3000, 1500),
    }

    def order(cfg: Settings) -> list[str]:
        ranked = sorted([to_premium_ct, to_premium], key=lambda c: _rank(c, good, asked, cfg))
        return [c.ticket_type for c in ranked]

    assert order(DEFAULTS) == ["PREMIUM", "PREMIUM_CT"]  # no prices: list order
    assert order(Settings(prices=prices)) == ["PREMIUM_CT", "PREMIUM"]


def test_expired_budget_still_gives_a_valid_answer() -> None:
    always_late = SearchBudget(0, clock=lambda: 1.0)
    day = schedule(*(session("09:00", "PREMIUM", 25) for _ in range(3)))
    result = check_enquiry(
        day, DEFAULTS, enquiry("11:00", "CLASSIC", 5), today=TODAY, budget=always_late
    )
    assert result.verdict in ("ACCEPT_GOOD", "ACCEPT_COSTLY")
    plan = assign(day[TODAY].sessions, DEFAULTS, budget=always_late)
    assert plan.ok
