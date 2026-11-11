"""Checking, booking, editing, cancelling and no-shows (SPEC §5.3). Routes call these; they
hold the rules.

Every change to a date happens under that date's lock, and the engine check is re-run inside the
lock. So two sales staff booking the last zone at the same moment can't both get it.
"""

from collections.abc import Collection
from dataclasses import dataclass
from typing import cast

from sqlalchemy.orm import Session

from engine import Enquiry, EnquiryResult, check_enquiry
from engine.evaluate import group_sizes
from engine.rules import TicketType
from engine.text import format_time
from jiak_api import audit, models
from jiak_api.clock import Now
from jiak_api.db import Database
from jiak_api.schedule import load_around, load_settings, without_sessions


class NoLongerFits(Exception):
    """The booking can't be made (any more). Carries a fresh check with alternatives."""

    def __init__(self, result: EnquiryResult) -> None:
        super().__init__(result.reason)
        self.result = result


class BookingNotFound(Exception):
    pass


class BookingNotChangeable(Exception):
    """E.g. cancelling a booking that is already cancelled."""


class BookingChangedMeanwhile(BookingNotChangeable):
    """Someone else moved the booking to another date while we waited for the lock."""

    def __init__(self) -> None:
        super().__init__("Someone else changed this booking just now. Please try again.")


@dataclass(frozen=True)
class CustomerDetails:
    name: str
    phone: str | None = None
    email: str | None = None
    company: str | None = None


@dataclass(frozen=True)
class BookingChanges:
    """What an edit changes. `None` means "keep as it is"."""

    date: str | None = None
    start: int | None = None
    ticket_type: TicketType | None = None
    adults: int | None = None
    children: int | None = None
    notes: str | None = None
    customer: CustomerDetails | None = None


def check(
    db: Session, enquiry: Enquiry, now: Now, ignore_sessions: Collection[str] = ()
) -> EnquiryResult:
    """The engine's verdict and alternatives for an enquiry. Stores nothing.

    `ignore_sessions`: session ids to leave out, so an edited booking doesn't clash with itself.
    """
    settings = load_settings(db)
    schedule = load_around(db, enquiry.date, settings)
    if ignore_sessions:
        schedule = without_sessions(schedule, ignore_sessions)
    return check_enquiry(schedule, settings, enquiry, today=now.today, now_minute=now.minute)


def create_booking(
    database: Database,
    db: Session,
    enquiry: Enquiry,
    customer: CustomerDetails,
    *,
    notes: str,
    actor: str,
    now: Now,
    outlook_event_id: str | None = None,
) -> models.Booking:
    """Book the enquiry if it still fits; otherwise raise NoLongerFits with a fresh check.

    `outlook_event_id`: an existing Outlook event this booking takes over (a hand-typed entry),
    linked in the same transaction so no second event is ever made for it.
    """
    with database.date_lock(db, enquiry.date):
        result = check(db, enquiry, now)
        if result.verdict == "NOT_POSSIBLE":
            raise NoLongerFits(result)

        customer_row = models.Customer(
            name=customer.name,
            phone=customer.phone,
            email=customer.email,
            company=customer.company,
        )
        booking = models.Booking(
            customer=customer_row,
            date=enquiry.date,
            start=enquiry.start,
            ticket_type=enquiry.ticket_type,
            adults=enquiry.adults,
            children=enquiry.children,
            status="confirmed",
            notes=notes,
            created_by=actor,
            outlook_event_id=outlook_event_id,
        )
        booking.sessions.extend(new_sessions(enquiry))
        db.add(booking)
        db.flush()  # gives the new rows their ids, for the audit entry
        audit.record(
            db,
            actor=actor,
            action="booking.create",
            entity="booking",
            entity_id=booking.id,
            before=None,
            after=audit.booking_snapshot(booking),
        )
        db.commit()
    return booking


def edit_booking(
    database: Database,
    db: Session,
    booking_id: str,
    changes: BookingChanges,
    *,
    actor: str,
    now: Now,
) -> models.Booking:
    """Change a confirmed booking.

    A new date, time, ticket type or headcount is re-checked, ignoring the booking's own sessions.
    If it doesn't fit: NoLongerFits, and nothing changes. Notes and customer details can always
    be changed, even after the visit.
    """
    booking = find_booking(db, booking_id)
    old_date = booking.date
    new_date = changes.date if changes.date is not None else old_date
    with database.date_locks(db, [old_date, new_date]):
        refresh_under_lock(db, booking, locked_dates=[old_date, new_date])
        before = audit.booking_snapshot(booking)

        current = enquiry_of(booking)
        wanted = Enquiry(
            date=new_date,
            start=changes.start if changes.start is not None else current.start,
            ticket_type=(
                changes.ticket_type if changes.ticket_type is not None else current.ticket_type
            ),
            adults=changes.adults if changes.adults is not None else current.adults,
            children=changes.children if changes.children is not None else current.children,
        )
        if wanted != current:
            own_sessions = [session.id for session in booking.sessions]
            result = check(db, wanted, now, ignore_sessions=own_sessions)
            if result.verdict == "NOT_POSSIBLE":
                raise NoLongerFits(result)
            move_booking(booking, wanted)

        if changes.notes is not None:
            booking.notes = changes.notes
        if changes.customer is not None:
            booking.customer.name = changes.customer.name
            booking.customer.phone = changes.customer.phone
            booking.customer.email = changes.customer.email
            booking.customer.company = changes.customer.company

        db.flush()  # gives new sessions their ids, for the audit entry
        after = audit.booking_snapshot(booking)
        if after != before:  # nothing changed -> no audit entry
            booking.outlook_note = ""  # an earlier note from the app no longer applies
            audit.record(
                db,
                actor=actor,
                action="booking.edit",
                entity="booking",
                entity_id=booking.id,
                before=before,
                after=after,
            )
        db.commit()
    return booking


def cancel_booking(
    database: Database, db: Session, booking_id: str, *, actor: str
) -> models.Booking:
    """Cancel a booking and free its zone slots (its sessions are deleted)."""
    booking = find_booking(db, booking_id)
    with database.date_lock(db, booking.date):
        refresh_under_lock(db, booking, locked_dates=[booking.date])
        before = audit.booking_snapshot(booking)
        booking.status = "cancelled"
        booking.sessions.clear()  # delete-orphan removes the session rows
        audit.record(
            db,
            actor=actor,
            action="booking.cancel",
            entity="booking",
            entity_id=booking.id,
            before=before,
            after=audit.booking_snapshot(booking),
        )
        db.commit()
    return booking


def mark_no_show(
    database: Database, db: Session, booking_id: str, *, actor: str, now: Now
) -> models.Booking:
    """The group didn't come. Frees its zone slots (sessions deleted); the booking stays visible.

    Only allowed once the visit has started. Before that, cancel instead.
    """
    booking = find_booking(db, booking_id)
    with database.date_lock(db, booking.date):
        refresh_under_lock(db, booking, locked_dates=[booking.date])
        if not has_started(booking, now):
            raise BookingNotChangeable(
                f"The visit starts {booking.date} at {format_time(booking.start)}. "
                "Mark a no-show only after the start time; before that, cancel the booking."
            )
        before = audit.booking_snapshot(booking)
        booking.status = "no_show"
        booking.sessions.clear()  # delete-orphan removes the session rows
        audit.record(
            db,
            actor=actor,
            action="booking.no_show",
            entity="booking",
            entity_id=booking.id,
            before=before,
            after=audit.booking_snapshot(booking),
        )
        db.commit()
    return booking


# --- helpers ---------------------------------------------------------------------------------


def find_booking(db: Session, booking_id: str) -> models.Booking:
    """The booking with this id, or BookingNotFound."""
    booking = db.get(models.Booking, booking_id)
    if booking is None:
        raise BookingNotFound(booking_id)
    return booking


def refresh_under_lock(db: Session, booking: models.Booking, *, locked_dates: list[str]) -> None:
    """Re-read the booking now that we hold the lock, and make sure it may still be changed."""
    db.refresh(booking)
    if booking.date not in locked_dates:
        raise BookingChangedMeanwhile()
    if booking.status != "confirmed":
        raise BookingNotChangeable(f"Booking is {booking.status}, not confirmed.")


def enquiry_of(booking: models.Booking) -> Enquiry:
    """The booking's current date, time, ticket type and headcount, as an engine Enquiry."""
    return Enquiry(
        date=booking.date,
        start=booking.start,
        ticket_type=cast(TicketType, booking.ticket_type),  # only valid codes are stored
        adults=booking.adults,
        children=booking.children,
    )


def move_booking(booking: models.Booking, wanted: Enquiry) -> None:
    """Give the booking its new slot and headcount, and update its sessions to match.

    Same date, time and ticket type, and still the same number of sub-groups: the sessions keep
    their ids and routes; only the headcount changes. Anything else: new floating sessions.
    """
    parts = split_group(wanted.adults, wanted.children)
    same_slot = (
        wanted.date == booking.date
        and wanted.start == booking.start
        and wanted.ticket_type == booking.ticket_type
        and len(parts) == len(booking.sessions)
    )
    booking.date = wanted.date
    booking.start = wanted.start
    booking.ticket_type = wanted.ticket_type
    booking.adults = wanted.adults
    booking.children = wanted.children
    if same_slot:
        for session, (adults, children) in zip(booking.sessions, parts, strict=True):
            session.adults = adults
            session.children = children
    else:
        booking.sessions.clear()  # delete-orphan removes the old session rows
        booking.sessions.extend(new_sessions(wanted))


def new_sessions(enquiry: Enquiry) -> list[models.Session]:
    """One floating session per sub-group. The engine gives them routes when the day is planned."""
    sessions: list[models.Session] = []
    for adults, children in split_group(enquiry.adults, enquiry.children):
        sessions.append(
            models.Session(
                date=enquiry.date,
                start=enquiry.start,
                ticket_type=enquiry.ticket_type,
                adults=adults,
                children=children,
                route=None,
            )
        )
    return sessions


def has_started(booking: models.Booking, now: Now) -> bool:
    """True once the booking's start time has been reached (Singapore time)."""
    if now.today != booking.date:
        return now.today > booking.date  # 'YYYY-MM-DD' strings sort like dates
    return now.minute >= booking.start


def split_group(adults: int, children: int) -> list[tuple[int, int]]:
    """(adults, children) per sub-group, sizes as even as possible. Adults fill sub-groups first.

    30 adults + 10 children -> [(20, 0), (10, 10)].
    """
    parts: list[tuple[int, int]] = []
    adults_left = adults
    for size in group_sizes(adults + children):
        adults_here = min(size, adults_left)
        parts.append((adults_here, size - adults_here))
        adults_left -= adults_here
    return parts
