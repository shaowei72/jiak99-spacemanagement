"""Outlook → App: the sweep (SPEC §5.4 "Outlook → App" and "When the app is down").

Every few minutes, and when the app starts, the sweep:

1. writes to Outlook what is still waiting: dates that couldn't be written (e.g. Outlook was down)
   and bookings that have no event yet;
2. reads every event from today to about six months ahead (in full, so nothing changed during an
   outage or while the laptop slept is missed);
3. looks at the app's own events (they carry a booking id):
   - changed in Outlook (date, times or subject) → the change is re-checked like an edit in the
     app. If it fits, the booking changes; if not, or if it can't be read, the booking stays as
     it was, the event is put back and a note at the top says why ("Conflict");
   - deleted in Outlook → the booking is cancelled;
4. takes in hand-typed entries, oldest first (whoever booked first keeps the slot):
   - '[PHE] 11:00 · 10A 2C · Tan family' that fits → booked; the event becomes the booking's event,
     with what was typed in its body kept below the divider;
   - that format but no room → a note with the reason and options, category "Conflict";
   - looks like a booking but isn't in that format → a note with the format, "Needs format";
   - anything else (meetings, site visits) is left alone.
   A marked entry is looked at again only when its date, times or subject change.

No email is sent; everything shows in the calendar itself.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import cast

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from engine import Enquiry, EnquiryResult
from engine.dates import add_days
from engine.text import format_time, slot_label
from graph.format import compose_body, kept_text, read_booking_subject, visible_state
from graph.gateway import EventNotFound, GraphGateway
from graph.model import CATEGORY_CONFLICT, CATEGORY_NEEDS_FORMAT, CalendarEvent
from graph.oldstyle import read_old_entry
from graph.template import parse_template
from jiak_api import models
from jiak_api.bookings import (
    BookingChanges,
    BookingNotChangeable,
    CustomerDetails,
    NoLongerFits,
    cancel_booking,
    create_booking,
    edit_booking,
)
from jiak_api.clock import Now
from jiak_api.db import Database
from jiak_api.outlook_sync import (
    check_calendar,
    record_error,
    record_ok,
    sync_dates,
    sync_paused,
    waiting_dates,
)

log = logging.getLogger(__name__)

DAYS_AHEAD = 186  # about six months
OUTLOOK_ACTOR = "outlook"
# More bookings than this missing from Outlook at once looks like a fault (wrong calendar, a
# failed read), not people deleting events: nothing is cancelled and the error is shown.
MOST_DELETIONS_AT_ONCE = 5

TEMPLATE_HELP = (
    "Type the subject like:  [PHE] 11:00 · 10A 2C · Tan family\n"
    "(ticket in brackets: Tingkat, CHE, CHE+CT, PHE or PHE+CT; start time; adults with A and "
    "children with C; the customer). The app then books it."
)


class TooManyMissing(Exception):
    """Many of the app's events are missing at once; see MOST_DELETIONS_AT_ONCE."""


@dataclass
class SweepResult:
    """What one sweep did, as short lines ('2026-12-01 10:00 Tan family: booked')."""

    booked: list[str] = field(default_factory=list)
    not_booked: list[str] = field(default_factory=list)  # Conflict or Needs format
    changed: list[str] = field(default_factory=list)  # edits made in Outlook, accepted
    put_back: list[str] = field(default_factory=list)  # edits made in Outlook, not accepted
    cancelled: list[str] = field(default_factory=list)  # deleted in Outlook
    dates: set[str] = field(default_factory=set)  # dates to write to Outlook afterwards


# --- notes shown at the top of an event --------------------------------------------------------


def options_text(result: EnquiryResult) -> str:
    """'Options that fit: 2026-12-01 11:30 · PHE (start 30 min later); ...' or ''."""
    if not result.alternatives:
        return ""
    parts = [
        f"{alt.date} {slot_label(alt.start, alt.ticket_type)} ({alt.change_summary})"
        for alt in result.alternatives
    ]
    return "Options that fit: " + "; ".join(parts)


def change_not_made_note(reason: str, result: EnquiryResult | None = None) -> str:
    """The note on an app event whose Outlook change the app didn't make."""
    lines = [f"CHANGE NOT MADE by the app: {reason}"]
    if result is not None and options_text(result):
        lines.append(options_text(result))
    lines.append("The booking is as shown. Make changes in the app (Bookings).")
    return "\n".join(lines)


def not_booked_note(reason: str, result: EnquiryResult | None = None) -> str:
    """The note on a hand-typed entry the app couldn't book."""
    lines = [f"NOT BOOKED by the app: {reason}"]
    if result is not None and options_text(result):
        lines.append(options_text(result))
    lines.append("Change this event's subject, date or time and the app tries again.")
    return "\n".join(lines)


def with_note(event: CalendarEvent, note: str, category: str) -> CalendarEvent:
    """`event` with the app's note on top (what was typed kept below the divider) and `category`
    instead of any earlier app category."""
    kept = kept_text(event) or ""
    others = [c for c in event.categories if c not in (CATEGORY_CONFLICT, CATEGORY_NEEDS_FORMAT)]
    return replace(event, body=compose_body(note, kept), categories=(*others, category))


def label(date: str, start: int, name: str) -> str:
    return f"{date} {format_time(start)} {name}"


# --- the app's own events ------------------------------------------------------------------------


def read_change(booking: models.Booking, event: CalendarEvent) -> BookingChanges | str | None:
    """What a person changed on the booking's event: the changes to make, a reason it can't be
    read, or None when nothing that matters changed (e.g. only the end time or the route text).

    Date and start come from the event's times; ticket and headcount from its subject. A new
    headcount keeps the children (as far as they fit) and counts the rest as adults.
    """
    read = read_booking_subject(event.subject)
    if read is None:
        return "the new subject can't be read"
    ticket_type, people = read
    if people < 1:
        return "the headcount must be at least 1"
    children = min(booking.children, people)
    adults = people - children
    same = (
        event.date == booking.date
        and event.start == booking.start
        and ticket_type == booking.ticket_type
        and adults == booking.adults
        and children == booking.children
    )
    if same:
        return None
    return BookingChanges(
        date=event.date,
        start=event.start,
        ticket_type=ticket_type,
        adults=adults,
        children=children,
    )


def remember(
    database: Database, booking_id: str, event: CalendarEvent, note: str | None
) -> models.Booking | None:
    """Store that the app has dealt with `event` as it is now (and set the note, unless None), so
    the next sync rewrites it to match the booking."""
    with database.make_session() as db:
        booking = db.get(models.Booking, booking_id)
        if booking is None:  # pragma: no cover (bookings are never deleted)
            return None
        with database.date_lock(db, booking.date):
            db.refresh(booking)
            booking.outlook_seen = visible_state(event)
            if note is not None:
                booking.outlook_note = note
            db.commit()
        return booking


def handle_changed_event(
    database: Database,
    db: Session,
    booking: models.Booking,
    event: CalendarEvent,
    now: Now,
    result: SweepResult,
) -> None:
    """A person changed the booking's event in Outlook: make the change if it fits, else put the
    event back with a note."""
    name = booking.customer.name
    old_date = booking.date
    result.dates.update({old_date, event.date})
    if booking.status != "confirmed":
        remember(database, booking.id, event, None)  # e.g. a no-show: just put it back
        result.put_back.append(label(old_date, booking.start, f"{name}: not confirmed"))
        return

    change = read_change(booking, event)
    if change is None:
        remember(database, booking.id, event, None)
        return
    if isinstance(change, str):
        remember(database, booking.id, event, change_not_made_note(change))
        result.put_back.append(label(old_date, booking.start, f"{name}: {change}"))
        return
    try:
        edit_booking(database, db, booking.id, change, actor=OUTLOOK_ACTOR, now=now)
    except NoLongerFits as error:
        db.rollback()
        note = change_not_made_note(error.result.reason, error.result)
        remember(database, booking.id, event, note)
        result.put_back.append(label(old_date, booking.start, f"{name}: {error.result.reason}"))
        return
    except BookingNotChangeable as error:  # pragma: no cover (changed by someone meanwhile)
        db.rollback()
        remember(database, booking.id, event, change_not_made_note(str(error)))
        return
    remember(database, booking.id, event, "")
    result.changed.append(label(event.date, event.start, f"{name}: changed in Outlook"))


def handle_app_events(
    database: Database,
    gateway: GraphGateway,
    events: list[CalendarEvent],
    now: Now,
    last_date: str,
    result: SweepResult,
) -> None:
    """Edits and deletions made in Outlook to the app's own events (module docstring, step 3)."""
    listed = {event.id: event for event in events}
    with database.make_session() as db:
        # Changed: events whose date, times or subject differ from what the app wrote.
        for event in events:
            if event.booking_id is None:
                continue
            booking = db.get(models.Booking, event.booking_id)
            if booking is None or booking.outlook_event_id != event.id:
                continue  # not this app's booking (e.g. restored from an older backup), or a copy
            if booking.outlook_seen is None or booking.status == "cancelled":
                continue  # not written yet / its event is being deleted: the sync does it
            if visible_state(event) != booking.outlook_seen:
                handle_changed_event(database, db, booking, event, now, result)

        # Deleted: bookings whose event the app wrote and that isn't there any more.
        rows = list(
            db.scalars(
                select(models.Booking).where(
                    models.Booking.status == "confirmed",
                    models.Booking.date >= now.today,
                    models.Booking.date <= last_date,
                    models.Booking.outlook_event_id.is_not(None),
                    models.Booking.outlook_seen.is_not(None),
                )
            )
        )
        missing: list[models.Booking] = []
        for booking in rows:
            event_id = cast(str, booking.outlook_event_id)
            if event_id in listed:
                continue
            try:
                moved = gateway.get_event(event_id)  # moved beyond the dates read?
            except EventNotFound:
                missing.append(booking)
                continue
            if visible_state(moved) != booking.outlook_seen:
                handle_changed_event(database, db, booking, moved, now, result)
        if len(missing) > MOST_DELETIONS_AT_ONCE:
            raise TooManyMissing(
                f"{len(missing)} bookings' Outlook events are missing at once; none were "
                "cancelled. Check that the app uses the right calendar."
            )
        for booking in missing:
            cancel_booking(database, db, booking.id, actor=OUTLOOK_ACTOR)
            result.cancelled.append(
                label(booking.date, booking.start, f"{booking.customer.name}: deleted in Outlook")
            )
            result.dates.add(booking.date)


# --- hand-typed entries ---------------------------------------------------------------------------


def looks_like_a_booking(subject: str) -> bool:
    """The take-over's test: a status tag, headcount or ticket word in the subject."""
    return read_old_entry(subject, "", []).booking_like


def mark(
    database: Database,
    gateway: GraphGateway,
    event: CalendarEvent,
    note: str,
    category: str,
) -> None:
    """Put the app's note on a hand-typed entry and remember it, so it is left alone until changed."""
    gateway.update_event(with_note(event, note, category))
    with database.make_session() as db:
        flag = db.get(models.OutlookFlag, event.id)
        if flag is None:
            flag = models.OutlookFlag(event_id=event.id, seen="", outcome="")
            db.add(flag)
        flag.seen = visible_state(event)
        flag.outcome = category
        flag.at = models.utc_now()
        db.commit()


def handle_typed_event(
    database: Database,
    gateway: GraphGateway,
    event: CalendarEvent,
    now: Now,
    result: SweepResult,
) -> None:
    """A hand-typed entry (no booking id): book it, or mark why not (module docstring, step 4)."""
    with database.make_session() as db:
        flag = db.get(models.OutlookFlag, event.id)
        if flag is not None and flag.seen == visible_state(event):
            return  # marked before and unchanged since
        parsed = parse_template(event.subject)
        if parsed is None:
            if looks_like_a_booking(event.subject) or flag is not None:
                mark(
                    database,
                    gateway,
                    event,
                    not_booked_note("the app can't read this subject.\n" + TEMPLATE_HELP),
                    CATEGORY_NEEDS_FORMAT,
                )
                result.not_booked.append(label(event.date, event.start, f"{event.subject}: format"))
            return

        enquiry = Enquiry(
            date=event.date,
            start=parsed.start,
            ticket_type=parsed.ticket_type,
            adults=parsed.adults,
            children=parsed.children,
        )
        typed_by = event.created_by or "unknown"
        try:
            create_booking(
                database,
                db,
                enquiry,
                CustomerDetails(name=parsed.customer[:200]),
                notes="",
                actor=f"{OUTLOOK_ACTOR} ({typed_by})",
                now=now,
                outlook_event_id=event.id,
            )
        except NoLongerFits as error:
            db.rollback()
            mark(
                database,
                gateway,
                event,
                not_booked_note(error.result.reason, error.result),
                CATEGORY_CONFLICT,
            )
            result.not_booked.append(
                label(event.date, parsed.start, f"{parsed.customer}: {error.result.reason}")
            )
            return
        if flag is not None:
            db.delete(flag)
            db.commit()
        result.booked.append(label(event.date, parsed.start, f"{parsed.customer}: booked"))
        result.dates.add(event.date)


# --- the sweep ----------------------------------------------------------------------------------------


def bookings_not_written(db: Session, today: str) -> list[str]:
    """Dates of current bookings not yet written to this calendar: no event yet (e.g. seeded, or
    made while Outlook was down), or written to another calendar before (see check_calendar)."""
    rows = db.scalars(
        select(models.Booking.date)
        .where(
            models.Booking.status != "cancelled",
            models.Booking.date >= today,
            or_(
                models.Booking.outlook_event_id.is_(None),
                models.Booking.outlook_seen.is_(None),
            ),
        )
        .distinct()
    )
    return list(rows)


def sweep(database: Database, gateway: GraphGateway, now: Now) -> SweepResult:
    """One sweep (module docstring). Raises if Outlook can't be read; the caller records it.
    Does nothing while Outlook sync is paused after a restore (SPEC §5.6)."""
    if sync_paused(database):
        return SweepResult()
    check_calendar(database, gateway)
    with database.make_session() as db:
        to_write = set(waiting_dates(db)) | set(bookings_not_written(db, now.today))
    sync_dates(database, gateway, to_write)

    last_date = add_days(now.today, DAYS_AHEAD)
    events = gateway.list_events(now.today, last_date)
    result = SweepResult()
    handle_app_events(database, gateway, events, now, last_date, result)

    typed = [event for event in events if event.booking_id is None]
    typed.sort(key=lambda event: (event.created, event.id))  # oldest first
    for event in typed:
        handle_typed_event(database, gateway, event, now, result)

    sync_dates(database, gateway, result.dates)
    record_ok(database, read=True)
    return result


def sweep_and_record(database: Database, gateway: GraphGateway, clock: Callable[[], Now]) -> None:
    """One sweep for the background loop: errors are logged and shown in the warning, never raised."""
    try:
        result = sweep(database, gateway, clock())
    except Exception as error:
        log.exception("Outlook sweep failed")
        record_error(database, f"Reading Outlook failed: {error}")
        return
    for line in result.booked + result.not_booked + result.changed + result.put_back:
        log.info("Outlook: %s", line)
    for line in result.cancelled:
        log.info("Outlook: %s", line)
