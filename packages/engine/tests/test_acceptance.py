"""SPEC §7 acceptance tests T1–T11. These must always pass."""

from engine import (
    EnquiryResult,
    NewPlacement,
    Settings,
    SplitPlacement,
    add_days,
    assign,
    check_enquiry,
)
from engine_helpers import DEFAULTS, TODAY, enquiry, schedule, session, t


def first_alternatives(result: EnquiryResult, count: int) -> list[tuple[str, int, str, str]]:
    """(date, start, ticket type, verdict) of the first `count` alternatives."""
    return [(a.date, a.start, a.ticket_type, a.verdict) for a in result.alternatives[:count]]


def test_t1_empty_day_10_00_leaves_unsellable_hour() -> None:
    result = check_enquiry(schedule(), DEFAULTS, enquiry("10:00", "PREMIUM", 10), today=TODAY)

    assert result.verdict == "ACCEPT_COSTLY"
    assert result.dead_minutes_delta == 60
    assert "09:00–10:00" in result.reason
    assert first_alternatives(result, 2) == [
        (TODAY, t("10:30"), "PREMIUM", "ACCEPT_GOOD"),
        (TODAY, t("09:00"), "PREMIUM", "ACCEPT_GOOD"),
    ]


def test_t2_spare_seats_in_a_taken_zone_are_not_sold() -> None:
    """The 13-person zone is sold out; the group of 12 gets a zone of its own."""
    existing = session("11:00", "PREMIUM", 13)
    result = check_enquiry(
        schedule(existing), DEFAULTS, enquiry("11:00", "PREMIUM", 12), today=TODAY
    )

    assert result.verdict == "ACCEPT_GOOD"
    assert result.placement == NewPlacement(people=12)
    assert result.zone_minutes_added == 120


def test_t3_all_zones_busy_offers_next_day_only() -> None:
    day = schedule(*(session("10:00", "PREMIUM_CT", 25) for _ in range(4)))
    result = check_enquiry(day, DEFAULTS, enquiry("11:00", "CLASSIC", 6), today=TODAY)

    assert result.verdict == "NOT_POSSIBLE"
    assert "All 4 zones are in use at 11:00" in result.reason
    tomorrow = add_days(TODAY, 1)
    assert (tomorrow, t("11:00"), "CLASSIC", "ACCEPT_GOOD") in first_alternatives(result, 3)
    assert all(a.date != TODAY for a in result.alternatives)


def test_t4_group_of_40_opens_two_sessions() -> None:
    result = check_enquiry(schedule(), DEFAULTS, enquiry("09:00", "PREMIUM", 40), today=TODAY)

    assert result.verdict == "ACCEPT_GOOD"
    assert result.placement == SplitPlacement(new_sessions=(25, 15))
    assert result.zone_minutes_added == 240


def test_t5_ending_after_close_suggests_earlier_never_a_downgrade() -> None:
    result = check_enquiry(schedule(), DEFAULTS, enquiry("19:30", "PREMIUM", 8), today=TODAY)

    assert result.verdict == "NOT_POSSIBLE"
    assert "21:00 close" in result.reason
    assert first_alternatives(result, 1) == [(TODAY, t("19:00"), "PREMIUM", "ACCEPT_GOOD")]
    assert all(a.ticket_type in ("PREMIUM", "PREMIUM_CT") for a in result.alternatives)


def test_t6_half_hour_gap_is_costly() -> None:
    day = schedule(*(session("09:00", "PREMIUM", 25) for _ in range(4)))
    result = check_enquiry(day, DEFAULTS, enquiry("11:30", "PREMIUM", 10), today=TODAY)

    assert result.verdict == "ACCEPT_COSTLY"
    assert result.dead_minutes_delta == 30
    assert "11:00–11:30" in result.reason
    assert first_alternatives(result, 1) == [(TODAY, t("11:00"), "PREMIUM", "ACCEPT_GOOD")]


def test_t7_back_to_back_session_is_good() -> None:
    day = schedule(*(session("09:00", "PREMIUM", 25) for _ in range(4)))
    result = check_enquiry(day, DEFAULTS, enquiry("11:00", "PREMIUM_CT", 10), today=TODAY)

    assert result.verdict == "ACCEPT_GOOD"


def test_t8_pinned_session_keeps_its_zone() -> None:
    pinned = session("09:00", "PREMIUM_CT", 20, zone="A", pinned=True)
    floating = [session("09:00", "PREMIUM", 20) for _ in range(3)]
    day = schedule(pinned, *floating)

    result = check_enquiry(day, DEFAULTS, enquiry("11:00", "PREMIUM", 5), today=TODAY)
    assert result.verdict in ("ACCEPT_GOOD", "ACCEPT_COSTLY")

    new = session("11:00", "PREMIUM", 5)
    plan = assign((pinned, *floating, new), DEFAULTS)
    assert plan.ok
    assert plan.zone_of[pinned.id] == "A"
    assert plan.zone_of[new.id] in ("B", "C", "D")  # a zone freed at 11:00


def test_t9_turnaround_blocks_10_30_but_11_00_is_good() -> None:
    settings = Settings(turnaround_minutes=15)
    day = schedule(*(session("09:00", "CLASSIC", 25) for _ in range(4)))
    result = check_enquiry(day, settings, enquiry("10:30", "CLASSIC", 4), today=TODAY)

    assert result.verdict == "NOT_POSSIBLE"
    assert first_alternatives(result, 1) == [(TODAY, t("11:00"), "CLASSIC", "ACCEPT_GOOD")]


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


def test_t11_four_small_groups_sell_out_the_slot() -> None:
    """A zone taken by any group is sold out, even with 24 seats left."""
    day = schedule(*(session("10:00", "PREMIUM", 1) for _ in range(4)))
    result = check_enquiry(day, DEFAULTS, enquiry("10:00", "PREMIUM", 1), today=TODAY)

    assert result.verdict == "NOT_POSSIBLE"
    assert "All 4 zones are in use at 10:00" in result.reason
