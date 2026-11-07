"""Checking, booking and cancelling (SPEC §5.3). Routes call these; they hold the rules.

Every change to a date happens under that date's lock, and the engine check is re-run inside the
lock. So two sales staff booking the last zone at the same moment can't both get it.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from engine import Enquiry, EnquiryResult, check_enquiry
from engine.evaluate import zone_sizes
from jiak_api import audit, models
from jiak_api.clock import Now
from jiak_api.db import Database
from jiak_api.schedule import load_around, load_settings


class NoLongerFits(Exception):
    """The booking can't be made (any more). Carries a fresh check with alternatives."""

    def __init__(self, result: EnquiryResult) -> None:
        super().__init__(result.reason)
        self.result = result


class BookingNotFound(Exception):
    pass


class BookingNotChangeable(Exception):
    """E.g. cancelling a booking that is already cancelled."""


@dataclass(frozen=True)
class CustomerDetails:
    name: str
    phone: str | None = None
    email: str | None = None
    company: str | None = None


def check(db: Session, enquiry: Enquiry, now: Now) -> EnquiryResult:
    """The engine's verdict and alternatives for an enquiry. Stores nothing."""
    settings = load_settings(db)
    schedule = load_around(db, enquiry.date, settings)
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
) -> models.Booking:
    """Book the enquiry if it still fits; otherwise raise NoLongerFits with a fresh check."""
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
        )
        # One floating session per zone the group takes. Ops gives them zones later.
        for adults, children in split_group(enquiry.adults, enquiry.children):
            booking.sessions.append(
                models.Session(
                    date=enquiry.date,
                    start=enquiry.start,
                    ticket_type=enquiry.ticket_type,
                    adults=adults,
                    children=children,
                    zone_id=None,
                )
            )
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


def cancel_booking(
    database: Database, db: Session, booking_id: str, *, actor: str
) -> models.Booking:
    """Cancel a booking and free its zones (its sessions are deleted)."""
    booking = db.get(models.Booking, booking_id)
    if booking is None:
        raise BookingNotFound(booking_id)
    with database.date_lock(db, booking.date):
        db.refresh(booking)  # re-read now that we hold the lock
        if booking.status != "confirmed":
            raise BookingNotChangeable(f"Booking is {booking.status}, not confirmed.")
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


def split_group(adults: int, children: int) -> list[tuple[int, int]]:
    """(adults, children) per zone for one group. Adults fill zones first.

    30 adults + 10 children -> [(25, 0), (5, 10)].
    """
    parts: list[tuple[int, int]] = []
    adults_left = adults
    for size in zone_sizes(adults + children):
        adults_here = min(size, adults_left)
        parts.append((adults_here, size - adults_here))
        adults_left -= adults_here
    return parts
