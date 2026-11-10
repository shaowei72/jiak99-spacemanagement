"""SPEC §7 acceptance tests T1–T17 (v0.4 zone routes). These must always pass."""

from engine import (
    DayState,
    EnquiryResult,
    NewPlacement,
    Settings,
    SplitPlacement,
    Visit,
    add_days,
    assign,
    check_enquiry,
    dead_gaps,
    route_visits,
)
from engine.rules import TicketType
from engine_helpers import DEFAULTS, TODAY, enquiry, pinned, schedule, session, t


def first_alternatives(result: EnquiryResult, count: int) -> list[tuple[str, int, str, str]]:
    """(date, start, ticket type, verdict) of the first `count` alternatives."""
    return [(a.date, a.start, a.ticket_type, a.verdict) for a in result.alternatives[:count]]


def new_route(result: EnquiryResult) -> str:
    """The route of a booking that wasn't split."""
    assert isinstance(result.placement, NewPlacement)
    return result.placement.route


def share_a_zone_slot(first: tuple[Visit, ...], second: tuple[Visit, ...]) -> bool:
    """True if any visit of `first` overlaps a visit of `second` in the same zone."""
    return any(
        a.zone_id == b.zone_id and a.start < b.end and b.start < a.end
        for a in first
        for b in second
    )


def visits_of(start: str, ticket_type: TicketType, route: str) -> tuple[Visit, ...]:
    return route_visits(t(start), ticket_type, route)


def full_morning() -> dict[str, DayState]:
    """Four PREMIUM groups at 09:00: every zone slot from 09:00 to 11:00 is taken."""
    return schedule(*(session("09:00", "PREMIUM", 25) for _ in range(4)))


def test_t1_classic_visits_a_and_one_other_zone() -> None:
    result = check_enquiry(schedule(), DEFAULTS, enquiry("09:00", "CLASSIC", 10), today=TODAY)

    assert result.verdict == "ACCEPT_GOOD"
    route = new_route(result)
    assert len(route) == 2
    assert "A" in route
    assert route.replace("A", "") in ("B", "C", "D")
    visits = visits_of("09:00", "CLASSIC", route)
    assert (visits[0].start, visits[-1].end) == (t("09:00"), t("10:00"))


def test_t2_spare_seats_in_a_taken_zone_are_not_sold() -> None:
    existing = session("11:00", "PREMIUM", 13)
    result = check_enquiry(
        schedule(existing), DEFAULTS, enquiry("11:00", "PREMIUM", 12), today=TODAY
    )

    assert result.verdict == "ACCEPT_GOOD"
    assert isinstance(result.placement, NewPlacement)
    assert result.placement.people == 12
    assert result.zone_minutes_added == 120


def test_t3_every_slot_full_offers_next_day_only() -> None:
    starts = ("09:00", "11:00", "13:00", "15:00", "17:00", "19:00")
    day = schedule(*(session(start, "PREMIUM", 25) for start in starts for _ in range(4)))
    result = check_enquiry(day, DEFAULTS, enquiry("11:00", "CLASSIC", 6), today=TODAY)

    assert result.verdict == "NOT_POSSIBLE"
    tomorrow = add_days(TODAY, 1)
    assert (tomorrow, t("11:00"), "CLASSIC", "ACCEPT_GOOD") in first_alternatives(result, 3)
    assert all(a.date != TODAY for a in result.alternatives)


def test_t4_group_of_40_splits_into_two_groups_of_20() -> None:
    result = check_enquiry(schedule(), DEFAULTS, enquiry("09:00", "PREMIUM", 40), today=TODAY)

    assert result.verdict == "ACCEPT_GOOD"
    assert isinstance(result.placement, SplitPlacement)
    assert result.placement.new_sessions == (20, 20)
    first, second = result.placement.routes
    assert not share_a_zone_slot(
        visits_of("09:00", "PREMIUM", first), visits_of("09:00", "PREMIUM", second)
    )
    assert result.reason.startswith("Group of 40 at 09:00 · PHE is split into 2 groups (20 + 20).")


def test_t5_ending_after_close_suggests_earlier_never_tingkat() -> None:
    result = check_enquiry(schedule(), DEFAULTS, enquiry("20:30", "CLASSIC", 8), today=TODAY)

    assert result.verdict == "NOT_POSSIBLE"
    assert "21:00 close" in result.reason
    assert first_alternatives(result, 1) == [(TODAY, t("20:00"), "CLASSIC", "ACCEPT_GOOD")]
    assert all(a.ticket_type != "TINGKAT" for a in result.alternatives)


def test_t6_classic_that_strands_b_and_c_is_costly() -> None:
    day = schedule(
        pinned("19:00", "PREMIUM", "ABCD"),
        pinned("19:00", "PREMIUM", "BCDA"),
        pinned("19:00", "PREMIUM", "CDAB"),
    )
    result = check_enquiry(day, DEFAULTS, enquiry("19:00", "CLASSIC", 5), today=TODAY)

    assert result.verdict == "ACCEPT_COSTLY"
    assert new_route(result) == "DA"
    assert result.dead_minutes_delta == 60
    assert "B 20:00–20:30 and C 20:30–21:00" in result.reason
    assert first_alternatives(result, 1) == [(TODAY, t("19:00"), "PREMIUM", "ACCEPT_GOOD")]
    assert result.alternatives[0].change_summary == "PHE instead of CHE"


def test_t7_tingkat_when_b_c_and_d_are_full() -> None:
    result = check_enquiry(full_morning(), DEFAULTS, enquiry("10:00", "TINGKAT", 4), today=TODAY)

    assert result.verdict == "NOT_POSSIBLE"
    assert result.reason == "Zones B, C and D are in use at 10:00."
    assert first_alternatives(result, 1) == [(TODAY, t("11:00"), "TINGKAT", "ACCEPT_GOOD")]


def test_t8_pinned_route_never_moves() -> None:
    fixed = pinned("09:00", "PREMIUM", "ABCD")
    result = check_enquiry(schedule(fixed), DEFAULTS, enquiry("09:00", "PREMIUM", 5), today=TODAY)

    assert result.verdict == "ACCEPT_GOOD"
    route = new_route(result)
    assert not share_a_zone_slot(
        visits_of("09:00", "PREMIUM", "ABCD"), visits_of("09:00", "PREMIUM", route)
    )
    new = session("09:00", "PREMIUM", 5)
    plan = assign((fixed, new), DEFAULTS)
    assert plan.ok
    assert plan.route_of[fixed.id] == "ABCD"


def test_t9_turnaround_sends_the_group_to_a_last() -> None:
    settings = Settings(turnaround_minutes=15)
    fixed = pinned("09:00", "CLASSIC", "AD")
    result = check_enquiry(schedule(fixed), settings, enquiry("09:30", "CLASSIC", 4), today=TODAY)

    assert result.verdict != "NOT_POSSIBLE"
    route = new_route(result)
    assert route in ("BA", "CA")

    booked = session("09:30", "CLASSIC", 4, route=route, pinned=True)
    plan = assign((fixed, booked), settings)
    assert plan.ok
    dead = {(g.zone_id, g.start) for g in dead_gaps((fixed, booked), plan.route_of, settings)}
    # A 09:30–10:00 and D 10:00–10:30 are reset time, never dead.
    assert ("A", t("09:30")) not in dead
    assert ("D", t("10:00")) not in dead


def test_t10_beyond_six_month_window() -> None:
    too_far = add_days(TODAY, 184)
    result = check_enquiry(
        schedule(), DEFAULTS, enquiry("10:30", "PREMIUM", 10, date=too_far), today=TODAY
    )

    assert result.verdict == "NOT_POSSIBLE"
    assert "6-month" in result.reason


def test_t10_last_day_of_window_is_allowed() -> None:
    last_day = add_days(TODAY, 183)
    result = check_enquiry(
        schedule(), DEFAULTS, enquiry("10:30", "PREMIUM", 10, date=last_day), today=TODAY
    )

    assert result.verdict == "ACCEPT_GOOD"


def test_t11_two_small_groups_sell_out_zone_a() -> None:
    """A zone slot taken by any group is sold out, even with 24 seats left."""
    day = schedule(
        session("10:00", "CLASSIC", 1, route="AD"),
        session("10:00", "CLASSIC", 1, route="DA"),
    )
    result = check_enquiry(day, DEFAULTS, enquiry("10:00", "CLASSIC", 1), today=TODAY)

    assert result.verdict == "NOT_POSSIBLE"
    assert result.reason == "Zone A is in use 10:00–11:00."


def test_t12_group_of_26_splits_13_and_13() -> None:
    result = check_enquiry(schedule(), DEFAULTS, enquiry("09:00", "PREMIUM", 26), today=TODAY)

    assert result.verdict == "ACCEPT_GOOD"
    assert isinstance(result.placement, SplitPlacement)
    assert result.placement.new_sessions == (13, 13)


def test_t13_three_classic_groups_cannot_share_zone_a() -> None:
    result = check_enquiry(schedule(), DEFAULTS, enquiry("10:00", "CLASSIC", 60), today=TODAY)

    assert result.verdict == "NOT_POSSIBLE"
    assert "Zone A has room for only 2 groups" in result.reason
    assert "Try booking part of the group at another time." in result.reason
    # An upgrade is offered; which one comes first is not fixed (SPEC §7 T13).
    upgrades = {a.ticket_type for a in result.alternatives if a.verdict == "ACCEPT_GOOD"}
    assert upgrades & {"CLASSIC_CT", "PREMIUM", "PREMIUM_CT"}


def test_t14_late_tingkat_in_b_c_or_d() -> None:
    result = check_enquiry(schedule(), DEFAULTS, enquiry("20:30", "TINGKAT", 4), today=TODAY)

    assert result.verdict == "ACCEPT_GOOD"
    assert new_route(result) in ("B", "C", "D")


def test_t15_premium_ct_spends_an_unbroken_hour_in_b_and_c() -> None:
    result = check_enquiry(schedule(), DEFAULTS, enquiry("09:00", "PREMIUM_CT", 10), today=TODAY)

    assert result.verdict != "NOT_POSSIBLE"
    route = new_route(result)
    assert sorted(route) == ["A", "B", "C", "D"]
    visits = visits_of("09:00", "PREMIUM_CT", route)
    minutes = {v.zone_id: v.end - v.start for v in visits}
    assert minutes == {"A": 30, "B": 60, "C": 60, "D": 30}
    assert (visits[0].start, visits[-1].end) == (t("09:00"), t("12:00"))


def test_t16_classic_avoids_a_taken_zone_d() -> None:
    day = schedule(pinned("10:00", "TINGKAT", "D"), pinned("10:30", "TINGKAT", "D"))
    result = check_enquiry(day, DEFAULTS, enquiry("10:00", "CLASSIC", 10), today=TODAY)

    assert result.verdict != "NOT_POSSIBLE"
    assert sorted(new_route(result)) in (["A", "B"], ["A", "C"])


def test_t17_classic_ct_uses_b_when_c_is_taken() -> None:
    day = schedule(
        pinned("10:00", "TINGKAT", "C"),
        pinned("10:30", "TINGKAT", "C"),
        pinned("11:00", "TINGKAT", "C"),
    )
    result = check_enquiry(day, DEFAULTS, enquiry("10:00", "CLASSIC_CT", 10), today=TODAY)

    assert result.verdict != "NOT_POSSIBLE"
    assert sorted(new_route(result)) == ["A", "B", "D"]
