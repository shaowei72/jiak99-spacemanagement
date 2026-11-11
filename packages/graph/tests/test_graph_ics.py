"""Reading and writing .ics calendar files (SPEC §5.4 take-over, --from-ics)."""

import pytest

from graph.ics import IcsError, IcsEvent, escape, parse_ics, unescape, unfold, write_ics

HEADER = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
FOOTER = "END:VCALENDAR\r\n"


def calendar(*events: str) -> str:
    """A calendar file holding the given VEVENT bodies (lines separated by '\\n')."""
    parts = [HEADER]
    for event in events:
        parts.append("BEGIN:VEVENT\r\n" + event.replace("\n", "\r\n") + "\r\nEND:VEVENT\r\n")
    parts.append(FOOTER)
    return "".join(parts)


def test_reads_an_outlook_event() -> None:
    text = calendar(
        "DESCRIPTION:Number of Pax: 20 pax\\nPACKAGE INCLUDES:\\, tour\n"
        "UID:0400000082\n"
        " 00E0074\n"
        "SUMMARY:[Confirmed] Bluebird Primary- 20 pax (PHE + CT)\n"
        "DTSTART;TZID=Singapore Standard Time:20261012T100000\n"
        "DTEND;TZID=Singapore Standard Time:20261012T130000\n"
        "X-MICROSOFT-CDO-BUSYSTATUS:TENTATIVE"
    )
    assert parse_ics(text) == [
        IcsEvent(
            uid="040000008200E0074",
            date="2026-10-12",
            start=600,
            end=780,
            subject="[Confirmed] Bluebird Primary- 20 pax (PHE + CT)",
            body="Number of Pax: 20 pax\nPACKAGE INCLUDES:, tour",
            busy_status="TENTATIVE",
        )
    ]


def test_all_day_events_and_events_without_an_end() -> None:
    text = calendar(
        "UID:a\nSUMMARY:Race weekend\nDTSTART;VALUE=DATE:20261008\nDTEND;VALUE=DATE:20261011",
        "UID:b\nSUMMARY:Holiday\nDTSTART;VALUE=DATE:20261225",
        "UID:c\nSUMMARY:Reminder\nDTSTART:20261012T100000",
    )
    race, holiday, reminder = parse_ics(text)
    assert (race.all_day, race.start, race.end) == (True, 0, 3 * 1440)
    assert (holiday.all_day, holiday.end) == (True, 1440)
    assert (reminder.all_day, reminder.start, reminder.end) == (False, 600, 600)


def test_utc_times_move_to_singapore_time() -> None:
    text = calendar(
        "UID:a\nSUMMARY:Morning\nDTSTART:20261012T020000Z\nDTEND:20261012T040000Z",
        "UID:b\nSUMMARY:Late\nDTSTART:20261012T153000Z\nDTEND:20261012T163000Z",
    )
    morning, late = parse_ics(text)
    assert (morning.date, morning.start, morning.end) == ("2026-10-12", 600, 720)
    assert (late.date, late.start, late.end) == ("2026-10-12", 1410, 1440 + 30)


def test_cancelled_repeating_and_one_changed_repeat() -> None:
    text = calendar(
        "UID:m\nSUMMARY:Weekly meeting\nDTSTART:20261014T133000\nRRULE:FREQ=WEEKLY",
        "UID:m\nRECURRENCE-ID;TZID=Singapore Standard Time:20261111T133000\n"
        "SUMMARY:Canceled: Weekly meeting\nDTSTART:20261111T133000\nSTATUS:CANCELLED",
    )
    weekly, changed = parse_ics(text)
    assert weekly.recurring and not weekly.cancelled
    assert changed.uid == "m/20261111T133000"
    assert changed.cancelled and not changed.recurring


def test_skips_alarms_other_parts_and_unreadable_lines() -> None:
    text = (
        "BEGIN:VCALENDAR\nBEGIN:VTIMEZONE\nTZID:Singapore\nEND:VTIMEZONE\n"
        "BEGIN:VEVENT\nUID:a\nSUMMARY:First\nSUMMARY:Second\nnot a property\n"
        "DTSTART:20261012T100000\n"
        "BEGIN:VALARM\nDESCRIPTION:Reminder\nEND:VALARM\nEND:VEVENT\nEND:VCALENDAR"
    )
    (event,) = parse_ics(text)
    assert event.subject == "First"
    assert event.body == ""
    assert event.uid == "a"


def test_event_without_uid_gets_an_empty_id() -> None:
    (event,) = parse_ics(calendar("SUMMARY:x\nDTSTART:20261012T100000"))
    assert event.uid == ""


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("hello", "not a calendar file"),
        (calendar("UID:a\nSUMMARY:x"), "without a start"),
        (calendar("UID:a\nDTSTART:2026-10-12"), "can't read the date/time"),
        (calendar("UID:a\nDTSTART:20261332T100000"), "not a real date"),
        ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART:20261012T100000\n", "ends inside an event"),
    ],
)
def test_errors(text: str, message: str) -> None:
    with pytest.raises(IcsError, match=message):
        parse_ics(text)


def test_unfold_and_escapes() -> None:
    assert unfold(" starts folded\r\nA:b\r\n c\n\td\re") == [" starts folded", "A:bcd", "e"]
    assert unescape(r"a\nb\Nc\,d\;e\\f\x") == "a\nb\nc,d;e\\fx"
    assert unescape("ends with \\") == "ends with \\"
    assert escape("a,b;c\\d\r\ne\nf") == r"a\,b\;c\\d\ne\nf"


def test_write_then_read_gives_the_same_events() -> None:
    events = [
        IcsEvent(uid="1", date="2026-10-08", start=0, end=2 * 1440, subject="F1", all_day=True),
        IcsEvent(
            uid="2",
            date="2026-10-12",
            start=600,
            end=780,
            subject="[Confirmed] Bluebird, 20 pax; PHE",
            body="Number of Pax: 20\nline two",
            busy_status="TENTATIVE",
        ),
        IcsEvent(uid="3", date="2026-10-14", start=810, end=870, subject="Meeting", recurring=True),
        IcsEvent(uid="4", date="2026-11-04", start=900, end=1020, subject="Off", cancelled=True),
        IcsEvent(uid="5", date="2026-12-31", start=1320, end=1440 + 60, subject="Party"),
    ]
    text = write_ics(events)
    assert "X-WR-CALNAME:Calendar" in text
    assert parse_ics(text) == events
