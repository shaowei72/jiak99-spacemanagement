"""The review sheet for the one-off calendar take-over (SPEC §5.4, steps 1-3)."""

import csv
import io

from graph.ics import IcsEvent
from graph.oldstyle import ClientRule
from graph.takeover import (
    ACTION_BOOK,
    ACTION_CHECK,
    ACTION_FILL_IN,
    ACTION_HISTORY,
    ACTION_OVER_100,
    ACTION_SKIP,
    CSV_COLUMNS,
    ReviewRow,
    build_review,
    count_actions,
    review_csv,
    review_row,
)

TODAY = "2026-11-10"


def event(
    subject: str,
    date: str = "2026-11-20",
    start: int = 600,
    end: int = 720,
    body: str = "",
    **flags: bool,
) -> IcsEvent:
    return IcsEvent(
        uid=f"uid-{subject[:12]}",
        date=date,
        start=start,
        end=end,
        subject=subject,
        body=body,
        **flags,
    )


def row_for(e: IcsEvent, rules: list[ClientRule] | None = None) -> ReviewRow:
    row = review_row(e, TODAY, rules or [])
    assert row is not None
    return row


def test_a_future_booking_to_book() -> None:
    row = row_for(event("[Soft Block] Bluebird Primary PHE - 30 + 6 staff= 36 pax"))
    assert row == ReviewRow(
        event_id="uid-[Soft Block]",
        date="2026-11-20",
        start="10:00",
        end="12:00",
        people=36,
        adults=36,
        children=0,
        ticket="PREMIUM",
        client="Bluebird Primary",
        status="soft block",
        soft_block=True,
        cancelled=False,
        when="future",
        action=ACTION_BOOK,
        notes="",
        people_found="subject",
        ticket_found="subject",
        subject="[Soft Block] Bluebird Primary PHE - 30 + 6 staff= 36 pax",
    )


def test_meetings_get_no_row() -> None:
    assert review_row(event("Weekly JIAK 99 meeting"), TODAY, []) is None


def test_actions() -> None:
    assert (
        row_for(event("[Confirmed] Bluebird PHE 20 pax", date="2026-11-09")).action
        == ACTION_HISTORY
    )
    assert row_for(event("[Confirmed] Bluebird PHE 20 pax", date=TODAY)).action == ACTION_BOOK
    assert row_for(event("Canceled: [Soft Block] Bluebird")).action == ACTION_SKIP
    assert row_for(event("[Confirmed] Bluebird PHE 20 pax", cancelled=True)).action == ACTION_SKIP
    past_cancelled = row_for(event("Canceled: [Soft Block] Bluebird", date="2026-10-01"))
    assert (past_cancelled.action, past_cancelled.cancelled) == (ACTION_HISTORY, True)
    assert row_for(event("[Soft Block] Bluebird")).action == ACTION_FILL_IN
    assert row_for(event("[Soft Block] Bluebird 20 pax")).action == ACTION_FILL_IN
    assert row_for(event("[Soft Block] Bluebird PHE")).action == ACTION_FILL_IN
    assert row_for(event("[Confirmed] Bluebird (112 pax)")).action == ACTION_OVER_100
    assert row_for(event("[Confirmed] Bluebird PHE 100 pax")).action == ACTION_BOOK
    assert row_for(event("[RESCHEDULED] Bluebird PHE 20 pax")).action == ACTION_CHECK
    assert row_for(event("[Confirmed] Bluebird PHE 20 pax", all_day=True)).action == ACTION_CHECK


def test_notes_point_at_what_to_look_at() -> None:
    row = row_for(
        event(
            "[RESCHEDULED] 2 adults + CT",
            start=645,
            end=780,
            body="Hi, we will come with 5pax",
            recurring=True,
        )
    )
    assert row.notes == (
        "subject has CT but no PHE/CHE; no ticket type found; "
        "tagged rescheduled: is it still on this date?; no client name found; "
        "starts at 10:45, not on the 30-min grid; repeating event: only its first date is read"
    )

    missing = row_for(event("[Soft Block] Bluebird", body="about 13 pax"))
    assert missing.notes == "no headcount found (body mentions 13 pax); no ticket type found"
    assert (missing.people, missing.adults, missing.children) == (None, None, None)

    too_short = row_for(event("[Confirmed] Bluebird PHE + CT 12 pax", start=630, end=780))
    assert too_short.notes == "event is 150 min, a PHE+CT stay is 180 min"


def test_all_day_and_overnight_times() -> None:
    all_day = row_for(event("[Confirmed] Bluebird PHE 20 pax", start=0, end=1440, all_day=True))
    assert (all_day.start, all_day.end, all_day.notes) == ("", "", "all-day event: no start time")
    late = row_for(event("[Confirmed] Bluebird Tingkat 20 pax", start=1410, end=1440 + 60))
    assert (late.start, late.end) == ("23:30", "01:00+1d")


def test_ticket_from_client_map() -> None:
    rule = ClientRule(
        name="Sunshine TourGroup", words=("sunshine", "tourgroup"), ticket_type="TINGKAT"
    )
    row = row_for(event("[Confirmed] Sunshine TourGroup 20 pax", end=630), [rule])
    assert (row.ticket, row.ticket_found, row.action) == (
        "TINGKAT",
        "client map: Sunshine TourGroup",
        ACTION_BOOK,
    )


def test_build_review_sorts_and_keeps_others_apart() -> None:
    events = [
        event("[Confirmed] Bluebird PHE 20 pax", date="2026-12-01"),
        event("Weekly meeting", date="2026-11-01"),
        event("[Confirmed] Kingfisher CHE 10 pax", date="2026-11-01", end=660),
    ]
    review = build_review(events, TODAY, [])
    assert [row.date for row in review.rows] == ["2026-11-01", "2026-12-01"]
    assert [other.subject for other in review.others] == ["Weekly meeting"]
    assert count_actions(review.rows) == {ACTION_BOOK: 1, ACTION_HISTORY: 1}


def test_csv() -> None:
    rows = [
        row_for(event("[Confirmed] Bluebird, Pte PHE 20 pax")),
        row_for(event("[Soft Block] Kingfisher")),
    ]
    text = review_csv(rows)
    read = list(csv.DictReader(io.StringIO(text)))
    assert tuple(read[0]) == CSV_COLUMNS
    assert read[0]["client"] == "Bluebird, Pte"
    assert read[0]["people"] == "20"
    assert read[0]["soft_block"] == "no"
    assert read[1]["soft_block"] == "yes"
    assert read[1]["people"] == ""
    assert review_csv([]) == ",".join(CSV_COLUMNS) + "\n"
