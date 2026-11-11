"""Outlook → App: the sweep (SPEC §5.4 "Outlook → App", "When the app is down", "Text kept from
before the app"). Uses the in-memory fake calendar; nothing talks to Microsoft."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from engine import Enquiry
from graph.fake import FakeGateway
from graph.format import DIVIDER, visible_state
from graph.model import CalendarEvent
from jiak_api import bookings, models
from jiak_api.clock import Now
from jiak_api.db import Database
from jiak_api.main import create_app
from jiak_api.outlook_inbound import TEMPLATE_HELP, TooManyMissing, sweep, sweep_and_record
from jiak_api.outlook_sync import sync_dates

DATE = "2026-12-01"
NOW = Now(today="2026-11-07", minute=8 * 60)
TYPED_BODY = "Called by Mrs Tan, 9123 4567. Halal set for 2."


@pytest.fixture
def calendar() -> FakeGateway:
    return FakeGateway()


def book(
    database: Database,
    calendar: FakeGateway,
    name: str = "Tan family",
    start: int = 660,
    ticket: str = "PREMIUM",
    adults: int = 15,
    children: int = 3,
    date: str = DATE,
) -> str:
    """A booking made in the app, with its Outlook event written. Returns its id."""
    enquiry = Enquiry(date=date, start=start, ticket_type=ticket, adults=adults, children=children)  # type: ignore[arg-type]
    with database.make_session() as db:
        booking = bookings.create_booking(
            database, db, enquiry, bookings.CustomerDetails(name=name), notes="", actor="t", now=NOW
        )
        booking_id = booking.id
    assert sync_dates(database, calendar, [date])
    return booking_id


def typed(
    calendar: FakeGateway,
    subject: str,
    date: str = DATE,
    start: int = 600,
    created: str = "",
    body: str = TYPED_BODY,
) -> CalendarEvent:
    """An entry typed by hand in Outlook."""
    return calendar.create_event(
        CalendarEvent(
            id="",
            date=date,
            start=start,
            end=start + 60,
            subject=subject,
            body=body,
            created_by="sales@example.com",
            created=created,
        )
    )


def load(database: Database, booking_id: str) -> models.Booking:
    with database.make_session() as db:
        booking = db.get(models.Booking, booking_id)
        assert booking is not None
        _ = booking.customer.name, list(booking.sessions)  # load before the session closes
        return booking


def all_bookings(database: Database) -> list[models.Booking]:
    with database.make_session() as db:
        rows = list(db.scalars(select(models.Booking)))
        for row in rows:
            _ = row.customer.name
        return rows


def event_of(calendar: FakeGateway, database: Database, booking_id: str) -> CalendarEvent:
    event_id = load(database, booking_id).outlook_event_id
    assert event_id is not None
    return calendar.events[event_id]


def close_day(database: Database, date: str = DATE) -> None:
    with database.make_session() as db:
        db.add(models.DaySettings(date=date, closed=True))
        db.commit()


# --- hand-typed entries ---------------------------------------------------------------------------


def test_a_typed_entry_that_fits_is_booked_in_place(
    database: Database, calendar: FakeGateway
) -> None:
    entry = typed(calendar, "[PHE] 10:00 · 10A 2C · Lim family")
    calendar.calls.clear()

    result = sweep(database, calendar, NOW)

    assert result.booked == ["2026-12-01 10:00 Lim family: booked"]
    [booking] = all_bookings(database)
    assert (booking.date, booking.start, booking.ticket_type) == (DATE, 600, "PREMIUM")
    assert (booking.adults, booking.children, booking.customer.name) == (10, 2, "Lim family")
    assert booking.created_by == "outlook (sales@example.com)"
    assert booking.outlook_event_id == entry.id
    # The same event, rewritten: no second event.
    assert calendar.calls == [("update", entry.id)]
    event = calendar.events[entry.id]
    assert event.subject == "PHE · Lim family · 12 pax · 10:00–12:00"
    assert event.booking_id == booking.id
    assert (event.start, event.end) == (600, 720)
    assert event.body.endswith(f"\n\n{DIVIDER}\n{TYPED_BODY}")  # what was typed is kept
    assert event.created_by == "sales@example.com"


def test_a_typed_entry_with_no_room_is_marked_conflict_once(
    database: Database, calendar: FakeGateway
) -> None:
    close_day(database)
    entry = typed(calendar, "[CHE] 10:00 · 6A · Ong party")

    result = sweep(database, calendar, NOW)

    assert result.booked == []
    assert all_bookings(database) == []
    event = calendar.events[entry.id]
    assert event.categories == ("Conflict",)
    assert event.body.startswith("NOT BOOKED by the app: ")
    assert event.body.endswith(f"\n\n{DIVIDER}\n{TYPED_BODY}")
    assert event.subject == "[CHE] 10:00 · 6A · Ong party"  # left for sales to change

    calendar.calls.clear()
    sweep(database, calendar, NOW)
    assert calendar.calls == []  # unchanged since marked: left alone

    # Sales move it to the next day: booked, and the typed text is kept once (no old note).
    calendar.events[entry.id] = replace(calendar.events[entry.id], date="2026-12-02")
    result = sweep(database, calendar, NOW)
    assert result.booked == ["2026-12-02 10:00 Ong party: booked"]
    event = calendar.events[entry.id]
    assert event.categories == ("CHE",)
    assert event.body.count("NOT BOOKED") == 0
    assert event.body.endswith(f"\n\n{DIVIDER}\n{TYPED_BODY}")
    with database.make_session() as db:
        assert db.get(models.OutlookFlag, entry.id) is None


def test_options_are_shown_when_there_are_some(database: Database, calendar: FakeGateway) -> None:
    for name in ("A", "B", "C", "D"):
        book(database, calendar, name=name, start=600, adults=25, children=0)
    entry = typed(calendar, "[PHE] 10:00 · 20A · Late group")
    sweep(database, calendar, NOW)
    body = calendar.events[entry.id].body
    assert "\nOptions that fit: 2026-12-02 10:00 · PHE" in body


def test_a_booking_like_entry_in_another_format_needs_format(
    database: Database, calendar: FakeGateway
) -> None:
    entry = typed(calendar, "Bluebird Primary 20 pax PHE")
    meeting = typed(calendar, "Weekly meeting", start=780)
    sweep(database, calendar, NOW)

    event = calendar.events[entry.id]
    assert event.categories == ("Needs format",)
    assert TEMPLATE_HELP in event.body
    assert calendar.events[meeting.id] == meeting  # meetings are left alone

    # Sales retype it: now it is booked.
    calendar.events[entry.id] = replace(event, subject="[PHE] 10:00 · 20A · Bluebird Primary")
    result = sweep(database, calendar, NOW)
    assert result.booked == ["2026-12-01 10:00 Bluebird Primary: booked"]
    assert calendar.events[entry.id].categories == ("PHE",)


def test_typed_entries_are_taken_in_oldest_first(database: Database, calendar: FakeGateway) -> None:
    """Three full groups at 10:00 leave room for one more. Two were typed during an outage;
    the one typed first gets it, even though it is listed second."""
    for name in ("A", "B", "C"):
        book(database, calendar, name=name, start=600, adults=25, children=0)
    later = typed(calendar, "[PHE] 10:00 · 25A · Typed later", created="2026-11-07T10:00:00Z")
    earlier = typed(calendar, "[PHE] 10:00 · 25A · Typed first", created="2026-11-07T09:00:00Z")

    result = sweep(database, calendar, NOW)

    assert result.booked == ["2026-12-01 10:00 Typed first: booked"]
    assert calendar.events[earlier.id].booking_id is not None
    assert calendar.events[later.id].categories == ("Conflict",)


# --- the app's own events changed in Outlook ----------------------------------------------------


def test_a_move_in_outlook_that_fits_moves_the_booking(
    database: Database, calendar: FakeGateway
) -> None:
    booking_id = book(database, calendar)
    event = event_of(calendar, database, booking_id)
    calendar.events[event.id] = replace(event, date="2026-12-02", start=600, end=720)

    result = sweep(database, calendar, NOW)

    assert result.changed == ["2026-12-02 10:00 Tan family: changed in Outlook"]
    booking = load(database, booking_id)
    assert (booking.date, booking.start, booking.outlook_note) == ("2026-12-02", 600, "")
    moved = calendar.events[event.id]
    assert moved.subject == "PHE · Tan family · 18 pax · 10:00–12:00"
    assert moved.categories == ("PHE",)
    with database.make_session() as db:
        actions = list(
            db.scalars(
                select(models.AuditLog.actor).where(models.AuditLog.action == "booking.edit")
            )
        )
    assert actions == ["outlook"]


def test_a_new_headcount_in_the_subject_changes_the_booking(
    database: Database, calendar: FakeGateway
) -> None:
    booking_id = book(database, calendar)  # 15 adults, 3 children
    event = event_of(calendar, database, booking_id)
    calendar.events[event.id] = replace(event, subject=event.subject.replace("18 pax", "20 pax"))
    sweep(database, calendar, NOW)
    booking = load(database, booking_id)
    assert (booking.adults, booking.children) == (17, 3)
    assert "20 pax" in calendar.events[event.id].subject


def test_a_move_that_does_not_fit_is_put_back_with_a_note(
    database: Database, calendar: FakeGateway
) -> None:
    booking_id = book(database, calendar)
    original = event_of(calendar, database, booking_id)
    close_day(database, "2026-12-02")
    calendar.events[original.id] = replace(original, date="2026-12-02")

    result = sweep(database, calendar, NOW)

    assert len(result.put_back) == 1
    booking = load(database, booking_id)
    assert booking.date == DATE  # unchanged
    event = calendar.events[original.id]
    assert (event.date, event.start, event.subject) == (DATE, 660, original.subject)
    assert event.categories == ("PHE", "Conflict")
    assert event.body.startswith("CHANGE NOT MADE by the app: ")

    calendar.calls.clear()
    sweep(database, calendar, NOW)
    assert calendar.calls == []  # settled: nothing more to do

    # The next change in the app clears the note.
    with database.make_session() as db:
        bookings.edit_booking(
            database, db, booking_id, bookings.BookingChanges(notes="Halal x2"), actor="t", now=NOW
        )
    sync_dates(database, calendar, [DATE])
    event = calendar.events[original.id]
    assert event.categories == ("PHE",)
    assert "CHANGE NOT MADE" not in event.body


def test_an_unreadable_subject_is_put_back_with_a_note(
    database: Database, calendar: FakeGateway
) -> None:
    booking_id = book(database, calendar)
    original = event_of(calendar, database, booking_id)
    calendar.events[original.id] = replace(original, subject="typed over")
    sweep(database, calendar, NOW)
    event = calendar.events[original.id]
    assert event.subject == original.subject
    assert event.body.startswith("CHANGE NOT MADE by the app: the new subject can't be read")


def test_a_change_that_does_not_matter_is_put_back_quietly(
    database: Database, calendar: FakeGateway
) -> None:
    booking_id = book(database, calendar)
    original = event_of(calendar, database, booking_id)
    calendar.events[original.id] = replace(original, end=original.end + 30)  # dragged longer
    result = sweep(database, calendar, NOW)
    assert result.put_back == [] and result.changed == []
    assert calendar.events[original.id] == original


def test_the_sync_never_overwrites_a_change_the_sweep_hasnt_seen(
    database: Database, calendar: FakeGateway
) -> None:
    booking_id = book(database, calendar)
    original = event_of(calendar, database, booking_id)
    moved = replace(original, start=840, end=960)
    calendar.events[original.id] = moved
    book(database, calendar, name="Other group", start=600)  # syncs the same date
    assert calendar.events[original.id] == moved  # left for the sweep
    sweep(database, calendar, NOW)
    assert load(database, booking_id).start == 840


def test_a_no_show_changed_in_outlook_is_put_back(
    database: Database, calendar: FakeGateway
) -> None:
    booking_id = book(database, calendar)
    with database.make_session() as db:
        bookings.mark_no_show(
            database, db, booking_id, actor="t", now=Now(today=DATE, minute=12 * 60)
        )
    sync_dates(database, calendar, [DATE])
    event = event_of(calendar, database, booking_id)
    calendar.events[event.id] = replace(event, start=600)
    result = sweep(database, calendar, NOW)
    assert result.put_back == ["2026-12-01 11:00 Tan family: not confirmed"]
    assert calendar.events[event.id].start == 660


# --- the app's own events deleted in Outlook ------------------------------------------------------


def test_deleting_the_event_in_outlook_cancels_the_booking(
    database: Database, calendar: FakeGateway
) -> None:
    booking_id = book(database, calendar)
    calendar.delete_event(event_of(calendar, database, booking_id).id)

    result = sweep(database, calendar, NOW)

    assert result.cancelled == ["2026-12-01 11:00 Tan family: deleted in Outlook"]
    booking = load(database, booking_id)
    assert (booking.status, booking.outlook_event_id, booking.sessions) == ("cancelled", None, [])
    assert calendar.events == {}  # not made again


def test_an_event_moved_beyond_the_dates_read_is_found_and_put_back(
    database: Database, calendar: FakeGateway
) -> None:
    """Moved past the six months the sweep reads: found anyway (not taken as deleted), and put
    back, because it is outside the booking window too."""
    booking_id = book(database, calendar)
    event = event_of(calendar, database, booking_id)
    calendar.events[event.id] = replace(event, date="2027-08-02")
    result = sweep(database, calendar, NOW)
    assert result.cancelled == []
    assert "outside the 6-month booking window" in result.put_back[0]
    booking = load(database, booking_id)
    assert (booking.status, booking.date) == ("confirmed", DATE)
    assert calendar.events[event.id].date == DATE


def test_many_missing_events_at_once_cancel_nothing(
    database: Database, calendar: FakeGateway
) -> None:
    ids = [book(database, calendar, name=f"G{n}", date=f"2026-12-{n:02d}") for n in range(1, 8)]
    calendar.events.clear()
    with pytest.raises(TooManyMissing, match="7 bookings' Outlook events are missing at once"):
        sweep(database, calendar, NOW)
    assert {load(database, i).status for i in ids} == {"confirmed"}

    sweep_and_record(database, calendar, lambda: NOW)
    with database.make_session() as db:
        state = db.get(models.OutlookState, 1)
        assert state is not None
        assert "missing at once" in state.last_error


def test_a_new_calendar_gets_every_event_afresh(database: Database, calendar: FakeGateway) -> None:
    booking_id = book(database, calendar)
    new_calendar = FakeGateway()  # e.g. the dev server restarted with a new fake
    result = sweep(database, new_calendar, NOW)
    assert result.cancelled == []
    booking = load(database, booking_id)
    assert booking.status == "confirmed"
    [event] = new_calendar.events.values()
    assert event.booking_id == booking_id


# --- catching up -------------------------------------------------------------------------------------


class DownCalendar(FakeGateway):
    """Outlook during an outage: nothing can be read or written."""

    def list_events(self, first_date: str, last_date: str) -> list[CalendarEvent]:
        raise ConnectionError("Outlook is down")


def test_after_an_outage_waiting_dates_and_new_bookings_are_written(
    database: Database,
) -> None:
    down = DownCalendar()
    client = TestClient(create_app(database, clock=lambda: NOW, gateway=down))
    body = {
        "date": DATE,
        "start": "10:00",
        "type": "CLASSIC",
        "adults": 6,
        "customer": {"name": "Ong party"},
    }
    response = client.post("/api/bookings", json=body)
    assert response.status_code == 201, response.text
    with database.make_session() as db:
        assert list(db.scalars(select(models.OutlookPending.date))) == [DATE]
        state = db.get(models.OutlookState, 1)
        assert state is not None and "Outlook is down" in state.last_error

    sweep_and_record(database, down, lambda: NOW)  # still down: recorded, nothing raised

    working = FakeGateway()
    sweep(database, working, NOW)
    [event] = working.events.values()
    assert event.subject == "CHE · Ong party · 6 pax · 10:00–11:00"
    with database.make_session() as db:
        assert list(db.scalars(select(models.OutlookPending.date))) == []
        state = db.get(models.OutlookState, 1)
        assert state is not None and state.last_read_ok is not None


def test_bookings_without_an_event_get_one(database: Database, calendar: FakeGateway) -> None:
    enquiry = Enquiry(date=DATE, start=600, ticket_type="CLASSIC", adults=6)
    with database.make_session() as db:  # e.g. seeded: no sync was asked for
        bookings.create_booking(
            database,
            db,
            enquiry,
            bookings.CustomerDetails(name="Seeded"),
            notes="",
            actor="t",
            now=NOW,
        )
    sweep(database, calendar, NOW)
    [event] = calendar.events.values()
    assert event.subject.startswith("CHE · Seeded")


def test_the_app_sweeps_when_it_starts(database: Database, calendar: FakeGateway) -> None:
    entry = typed(calendar, "[PHE] 10:00 · 10A · Lim family")
    app = create_app(database, clock=lambda: NOW, gateway=calendar, sweep_minutes=60)
    with TestClient(app):
        for _ in range(200):  # the first sweep runs in a worker thread right away
            if calendar.events[entry.id].booking_id is not None:
                break
            import time

            time.sleep(0.01)
    assert calendar.events[entry.id].booking_id is not None
    assert visible_state(calendar.events[entry.id]).endswith("10:00–12:00")


def test_an_event_with_a_booking_id_the_app_doesnt_know_is_left_alone(
    database: Database, calendar: FakeGateway
) -> None:
    """E.g. after restoring an older backup (SPEC §5.6): never deleted or booked by the sweep."""
    stranger = calendar.create_event(
        CalendarEvent(
            id="", date=DATE, start=600, end=720, subject="PHE · Gone · 5 pax", booking_id="nope"
        )
    )
    calendar.calls.clear()
    result = sweep(database, calendar, NOW)
    assert (result.booked, result.cancelled, result.not_booked) == ([], [], [])
    assert calendar.events[stranger.id] == stranger
    assert calendar.calls == []
