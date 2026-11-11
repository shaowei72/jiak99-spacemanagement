"""Keeping the Bookings calendar in Outlook in step with the app (SPEC §5.4 "App → Outlook").

After any change to a date (a booking made, edited, cancelled; the day planned, locked, a group
pinned), the routes call `OutlookUpdates.dates_changed(date)`. Once the reply has been sent, every
booking on that date is compared with its Outlook event and the event is created, rewritten or
deleted to match. Doing a whole date at once is simple and also catches route changes of other groups.

It is safe to run again at any time (idempotent): an event that already matches is left alone.
If Outlook can't be reached, the booking itself still stands; the error is logged and the next
sync of that date puts things right.
"""

import logging
from collections.abc import Iterable
from dataclasses import replace
from typing import Annotated, cast

from fastapi import BackgroundTasks, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.rules import TicketType
from graph.fake import FakeGateway
from graph.format import BookingInfo, booking_event
from graph.gateway import EventNotFound, GraphGateway
from graph.model import CalendarEvent
from jiak_api import models
from jiak_api.config import AppConfig
from jiak_api.db import Database

log = logging.getLogger(__name__)


def make_gateway(config: AppConfig) -> GraphGateway:
    """The calendar to use: the in-memory fake unless GRAPH_MODE=real (CLAUDE.md)."""
    if config.graph_mode == "real":
        raise NotImplementedError(
            "GRAPH_MODE=real: the real Microsoft Graph gateway comes in a later part of "
            "Milestone 4. Leave GRAPH_MODE unset (or 'fake') for now."
        )
    return FakeGateway()


# --- what the calendar shows for a booking -----------------------------------------------------


def contact_text(customer: models.Customer) -> str:
    """'9123 4567 / tan@example.com', or '' when there is neither."""
    parts = [part for part in (customer.phone, customer.email) if part]
    return " / ".join(parts)


def customer_text(customer: models.Customer) -> str:
    """'Tan family', or 'Mrs Lim (ACME Tours)' when a company is given."""
    if customer.company and customer.company != customer.name:
        return f"{customer.name} ({customer.company})"
    return customer.name


def booking_info(booking: models.Booking) -> BookingInfo:
    """The booking as the calendar shows it."""
    return BookingInfo(
        id=booking.id,
        date=booking.date,
        start=booking.start,
        ticket_type=cast(TicketType, booking.ticket_type),  # only valid codes are stored
        customer=customer_text(booking.customer),
        adults=booking.adults,
        children=booking.children,
        routes=tuple(session.route for session in booking.sessions),
        status=booking.status.replace("_", "-"),  # 'no_show' -> 'no-show'
        contact=contact_text(booking.customer),
        notes=booking.notes,
    )


# --- syncing -----------------------------------------------------------------------------------


def sync_booking(
    gateway: GraphGateway, booking: models.Booking, events_by_id: dict[str, CalendarEvent]
) -> None:
    """Create, rewrite or delete the booking's Outlook event so it matches the booking.

    `events_by_id`: the events already read for the booking's date (saves one call per booking).
    A cancelled booking's event is deleted. Stores the event id on the booking (caller commits).
    """
    event_id = booking.outlook_event_id

    if booking.status == "cancelled":
        if event_id is not None:
            try:  # noqa: SIM105 (plain try/except reads more easily than contextlib.suppress)
                gateway.delete_event(event_id)
            except EventNotFound:
                pass  # already gone
            booking.outlook_event_id = None
        return

    existing: CalendarEvent | None = None
    if event_id is not None:
        existing = events_by_id.get(event_id)
        if existing is None:  # the booking moved here from another date, or the event is gone
            try:
                existing = gateway.get_event(event_id)
            except EventNotFound:
                existing = None

    wanted = booking_event(booking_info(booking))
    if existing is None:
        created = gateway.create_event(wanted)
        booking.outlook_event_id = created.id
        return

    # Keep Outlook's own details (event id, who created it, its UID); everything else must match.
    wanted = replace(
        wanted, id=existing.id, created_by=existing.created_by, ical_uid=existing.ical_uid
    )
    if wanted != existing:
        gateway.update_event(wanted)


def sync_date(database: Database, db: Session, gateway: GraphGateway, date: str) -> None:
    """Bring every booking on `date` in step with Outlook. Holds the date lock while it works,
    so a booking made at the same moment can't get two events."""
    with database.date_lock(db, date):
        events = gateway.list_events(date, date)
        events_by_id = {event.id: event for event in events}
        rows = db.scalars(
            select(models.Booking).where(models.Booking.date == date).order_by(models.Booking.id)
        )
        for booking in rows:
            sync_booking(gateway, booking, events_by_id)
        db.commit()


def sync_dates(database: Database, gateway: GraphGateway, dates: Iterable[str]) -> None:
    """Sync each date in its own transaction. A failure is logged and the other dates still sync."""
    for date in sorted(set(dates)):
        with database.make_session() as db:
            try:
                sync_date(database, db, gateway, date)
            except Exception:
                db.rollback()
                log.exception(
                    "Outlook sync of %s failed; it will be retried on the next sync", date
                )


# --- for the routes ----------------------------------------------------------------------------


class OutlookUpdates:
    """Lets a route ask for Outlook to be updated once its reply has been sent."""

    def __init__(self, database: Database, gateway: GraphGateway, background: BackgroundTasks):
        self.database = database
        self.gateway = gateway
        self.background = background

    def dates_changed(self, *dates: str) -> None:
        """Sync these dates after the reply (FastAPI background task)."""
        self.background.add_task(sync_dates, self.database, self.gateway, list(dates))


def get_outlook(request: Request, background: BackgroundTasks) -> OutlookUpdates:
    """FastAPI dependency: the app's database and calendar, plus this request's background tasks."""
    database: Database = request.app.state.database
    gateway: GraphGateway = request.app.state.gateway
    return OutlookUpdates(database, gateway, background)


OutlookDep = Annotated[OutlookUpdates, Depends(get_outlook)]
