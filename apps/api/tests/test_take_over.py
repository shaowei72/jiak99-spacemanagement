"""`import-outlook --review FILE [--commit]`: applying the corrected review sheet (SPEC §5.4 one-off
take-over, step 4). Uses the in-memory fake calendar; nothing talks to Microsoft."""

import csv
import io
from pathlib import Path

import pytest
from sqlalchemy import func, select

from graph.fake import FakeGateway
from graph.model import CalendarEvent
from graph.sheet import read_sheet
from graph.takeover import CSV_COLUMNS
from jiak_api import import_outlook, models
from jiak_api.clock import Now
from jiak_api.db import Database
from jiak_api.take_over import (
    OUTCOME_ALREADY,
    OUTCOME_BOOKED,
    OUTCOME_HISTORY,
    OUTCOME_NO_EVENT,
    OUTCOME_NO_FIT,
    OUTCOME_NOT_READY,
    OUTCOME_OVER_100,
    OUTCOME_SKIPPED,
    TakeOverResult,
    apply_sheet,
)

TODAY = "2026-11-11"
NOW = Now(today=TODAY, minute=0)

# One row per kind of outcome. Cells left out are empty.
ROWS: list[dict[str, str]] = [
    {  # past -> history
        "event_id": "uid-past",
        "date": "2026-10-20",
        "start": "10:00",
        "end": "12:00",
        "people": "20",
        "ticket": "PREMIUM",
        "ticket_found": "subject",
        "client": "Bluebird Primary",
        "status": "confirmed",
        "soft_block": "no",
        "subject": "[Confirmed] Bluebird Primary PHE- 20 pax",
    },
    {  # past and cancelled -> history, cancelled
        "event_id": "uid-past-cancelled",
        "date": "2026-10-21",
        "start": "",
        "client": "Kingfisher Travel",
        "cancelled": "yes",
        "subject": "Canceled: Kingfisher Travel",
    },
    {  # future, 30 people -> booked as 2 sub-groups, event rewritten in place
        "event_id": "uid-sunshine",
        "date": "2026-11-20",
        "start": "11:00",
        "end": "11:30",
        "people": "30",
        "children": "10",
        "ticket": "TINGKAT",
        "client": "Sunshine TourGroup",
        "soft_block": "yes",
        "subject": "[Soft Block] Sunshine TourGroup 30 pax",
    },
    {  # future, the venue is closed that day -> doesn't fit
        "event_id": "uid-closed",
        "date": "2026-11-21",
        "start": "10:00",
        "people": "10",
        "ticket": "CLASSIC",
        "client": "Hornbill Primary",
        "subject": "[Confirmed] Hornbill Primary CHE 10 pax",
    },
    {  # future, but the event is no longer in Outlook
        "event_id": "uid-gone",
        "date": "2026-11-22",
        "start": "10:00",
        "people": "10",
        "ticket": "CLASSIC",
        "client": "Pandan Primary",
        "subject": "[Confirmed] Pandan Primary CHE 10 pax",
    },
    {  # over 100 -> listed
        "event_id": "uid-big",
        "date": "2026-12-04",
        "start": "13:30",
        "people": "110",
        "client": "Art Corp",
        "subject": "[Confirmed] Art Corp 110 pax",
    },
    {  # no headcount -> not ready
        "event_id": "uid-no-people",
        "date": "2026-12-08",
        "start": "11:00",
        "ticket": "PREMIUM",
        "client": "Seniors",
        "subject": "[Confirmed] Seniors PHE",
    },
    {  # skipped on the sheet
        "event_id": "uid-skip",
        "date": "2026-12-09",
        "start": "14:00",
        "people": "12",
        "ticket": "PREMIUM",
        "action": "skip",
        "subject": "[Soft Block] maybe",
    },
]


def sheet_text(rows: list[dict[str, str]]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in CSV_COLUMNS})
    return output.getvalue()


def old_events() -> list[CalendarEvent]:
    """operations@ as it is before the take-over: the old free-text events (not 'uid-gone')."""
    events = []
    for number, row in enumerate(ROWS, start=1):
        if row["event_id"] == "uid-gone":
            continue
        events.append(
            CalendarEvent(
                id=f"outlook-{number}",
                date=row["date"],
                start=600,
                end=720,
                subject=row["subject"],
                body="Forwarded email ...",
                ical_uid=row["event_id"],
                created_by="sales@example.com",
            )
        )
    return events


@pytest.fixture
def calendar() -> FakeGateway:
    return FakeGateway(old_events())


@pytest.fixture
def closed_day(database: Database) -> None:
    with database.make_session() as db:
        db.add(models.DaySettings(date="2026-11-21", closed=True))
        db.commit()


def run(database: Database, calendar: FakeGateway, *, commit: bool) -> TakeOverResult:
    with database.make_session() as db:
        return apply_sheet(
            database,
            db,
            calendar,
            read_sheet(sheet_text(ROWS)),
            today=TODAY,
            now=NOW,
            commit=commit,
        )


def outcomes(result: TakeOverResult) -> dict[str, str]:
    return {item.row.event_id: item.outcome for item in result.outcomes}


def count(database: Database, table: type[models.Base]) -> int:
    with database.make_session() as db:
        return db.scalar(select(func.count()).select_from(table)) or 0


EXPECTED = {
    "uid-past": OUTCOME_HISTORY,
    "uid-past-cancelled": OUTCOME_HISTORY,
    "uid-sunshine": OUTCOME_BOOKED,
    "uid-closed": OUTCOME_NO_FIT,
    "uid-gone": OUTCOME_NO_EVENT,
    "uid-big": OUTCOME_OVER_100,
    "uid-no-people": OUTCOME_NOT_READY,
    "uid-skip": OUTCOME_SKIPPED,
}


@pytest.mark.usefixtures("closed_day")
def test_rehearsal_stores_nothing_and_leaves_outlook_alone(
    database: Database, calendar: FakeGateway
) -> None:
    result = run(database, calendar, commit=False)
    assert outcomes(result) == EXPECTED
    assert result.committed is False
    assert count(database, models.Booking) == 0
    assert count(database, models.PastBooking) == 0
    assert count(database, models.Customer) == 0
    assert calendar.calls == []
    booked = [item for item in result.outcomes if item.outcome == OUTCOME_BOOKED]
    assert booked[0].message == "Tingkat · Sunshine TourGroup · 30 pax · 11:00–11:30"


@pytest.mark.usefixtures("closed_day")
def test_commit_books_future_rows_and_keeps_past_ones_as_history(
    database: Database, calendar: FakeGateway
) -> None:
    result = run(database, calendar, commit=True)
    assert outcomes(result) == EXPECTED
    assert result.booked_dates == ("2026-11-20",)

    with database.make_session() as db:
        past = {p.source_event_id: p for p in db.scalars(select(models.PastBooking))}
        assert set(past) == {"uid-past", "uid-past-cancelled"}
        bluebird = past["uid-past"]
        assert (bluebird.date, bluebird.start, bluebird.end) == ("2026-10-20", 600, 720)
        assert (bluebird.people, bluebird.ticket_type, bluebird.type_source) == (
            20,
            "PREMIUM",
            "subject",
        )
        assert (bluebird.client, bluebird.status, bluebird.was_soft_block) == (
            "Bluebird Primary",
            "confirmed",
            False,
        )
        assert bluebird.original_subject == "[Confirmed] Bluebird Primary PHE- 20 pax"
        cancelled = past["uid-past-cancelled"]
        assert (cancelled.status, cancelled.start, cancelled.end) == ("cancelled", 0, 1440)
        assert (cancelled.people, cancelled.ticket_type, cancelled.type_source) == (
            None,
            None,
            None,
        )

        [booking] = db.scalars(select(models.Booking))
        assert booking.source_event_id == "uid-sunshine"
        assert booking.outlook_event_id == "outlook-3"  # the existing event, taken over
        assert (booking.date, booking.start, booking.ticket_type) == ("2026-11-20", 660, "TINGKAT")
        assert (booking.adults, booking.children, booking.status) == (20, 10, "confirmed")
        assert booking.customer.name == "Sunshine TourGroup"
        assert booking.created_by == "import-outlook"
        assert sorted((s.adults, s.children) for s in booking.sessions) == [(5, 10), (15, 0)]
        actions = list(db.scalars(select(models.AuditLog.action)))
        assert actions == ["booking.take_over"]
        booking_id = booking.id

    # Taken over in place: the same event, rewritten; no event created or deleted.
    assert calendar.calls == [("update", "outlook-3")]
    event = calendar.events["outlook-3"]
    assert event.subject == "Tingkat · Sunshine TourGroup · 30 pax · 11:00–11:30"
    assert event.booking_id == booking_id
    assert (event.start, event.end) == (660, 690)
    assert event.ical_uid == "uid-sunshine"
    assert event.created_by == "sales@example.com"
    # Events of rows that weren't booked keep their old subject.
    assert calendar.events["outlook-4"].subject == "[Confirmed] Hornbill Primary CHE 10 pax"


@pytest.mark.usefixtures("closed_day")
def test_running_again_skips_what_was_taken_over(database: Database, calendar: FakeGateway) -> None:
    run(database, calendar, commit=True)
    calendar.calls.clear()

    again = run(database, calendar, commit=True)
    assert outcomes(again)["uid-past"] == OUTCOME_ALREADY
    assert outcomes(again)["uid-sunshine"] == OUTCOME_ALREADY
    assert outcomes(again)["uid-closed"] == OUTCOME_NO_FIT  # still listed
    assert count(database, models.Booking) == 1
    assert count(database, models.PastBooking) == 2
    assert calendar.calls == []


def test_later_rows_see_earlier_bookings(database: Database) -> None:
    """Five full groups at 10:00 on one day: the fifth doesn't fit (4 zones)."""
    rows = [
        {
            "event_id": f"uid-{n}",
            "date": "2026-11-20",
            "start": "10:00",
            "people": "25",
            "ticket": "PREMIUM",
            "client": f"Group {n}",
        }
        for n in range(1, 6)
    ]
    calendar = FakeGateway(
        [
            CalendarEvent(
                id="", date="2026-11-20", start=600, end=720, subject="", ical_uid=r["event_id"]
            )
            for r in rows
        ]
    )
    with database.make_session() as db:
        result = apply_sheet(
            database, db, calendar, read_sheet(sheet_text(rows)), today=TODAY, now=NOW, commit=True
        )
    assert [item.outcome for item in result.outcomes] == [OUTCOME_BOOKED] * 4 + [OUTCOME_NO_FIT]


# --- the command -------------------------------------------------------------------------------


@pytest.fixture
def sheet_file(tmp_path: Path) -> Path:
    path = tmp_path / "operations-review.csv"
    path.write_text(sheet_text(ROWS), encoding="utf-8-sig")
    return path


@pytest.fixture
def use_database(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("GRAPH_MODE", raising=False)


@pytest.mark.usefixtures("use_database")
def test_command_rehearses_then_commits(
    sheet_file: Path, database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["--review", str(sheet_file), "--today", TODAY]
    assert import_outlook.main(args) == 0
    out = capsys.readouterr().out
    assert "Read 8 rows from operations-review.csv. Take-over date 2026-11-11" in out
    assert "GRAPH_MODE is fake" in out
    # The sheet's own copy of the calendar has every event, and no day is closed here.
    assert "  booked           3" in out
    assert "  over 100         1" in out
    assert "-> Tingkat · Sunshine TourGroup · 30 pax · 11:00–11:30" in out
    assert "not ready: no headcount: fill in people" in out
    assert "Rehearsal: nothing was stored." in out
    database = Database(database_url)
    assert count(database, models.Booking) == 0

    assert import_outlook.main([*args, "--commit"]) == 0
    out = capsys.readouterr().out
    assert "Stored. Outlook events rewritten in place on 3 dates." in out
    assert count(database, models.Booking) == 3
    assert count(database, models.PastBooking) == 2
    database.engine.dispose()


@pytest.mark.usefixtures("use_database")
def test_command_books_later_groups_on_the_take_over_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """At 15:00 on the take-over date, the 10:00 group of that day is still taken over."""
    monkeypatch.setattr(import_outlook, "singapore_now", lambda: Now(today=TODAY, minute=900))
    path = tmp_path / "today.csv"
    row = {"event_id": "u", "date": TODAY, "start": "10:00", "people": "5", "ticket": "CLASSIC"}
    path.write_text(sheet_text([row]), encoding="utf-8")
    assert import_outlook.main(["--review", str(path)]) == 0
    assert "  booked           1" in capsys.readouterr().out


@pytest.mark.usefixtures("use_database")
def test_command_reads_excel_csv_in_the_windows_code_page(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "excel.csv"
    row = {
        "event_id": "u",
        "date": "20/11/2026",
        "start": "9:30",
        "people": "5",
        "ticket": "CLASSIC",
        "client": "Café Lumière",
    }
    path.write_bytes(sheet_text([row]).encode("cp1252"))
    assert import_outlook.main(["--review", str(path), "--today", TODAY]) == 0
    out = capsys.readouterr().out
    assert "2026-11-20   09:30  Café Lumière (line 2)" in out


@pytest.mark.usefixtures("use_database")
def test_command_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    no_columns = tmp_path / "wrong.csv"
    no_columns.write_text("date,start\n2026-11-20,10:00\n", encoding="utf-8")
    assert import_outlook.main(["--review", str(no_columns)]) == 2
    assert "Use the sheet written by the dry run" in capsys.readouterr().err

    good = tmp_path / "good.csv"
    good.write_text(sheet_text(ROWS), encoding="utf-8")
    monkeypatch.setenv("GRAPH_MODE", "real")
    assert import_outlook.main(["--review", str(good)]) == 2
    assert "GRAPH_MODE=real" in capsys.readouterr().err
