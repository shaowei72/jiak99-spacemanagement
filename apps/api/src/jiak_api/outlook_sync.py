"""Keeping the Bookings calendar in Outlook in step with the app (SPEC §5.4 "App → Outlook").

After any change to a date (a booking made, edited, cancelled; the day planned, locked, a group
pinned), the routes call `OutlookUpdates.dates_changed(date)`. The date is noted as waiting, and
once the reply has been sent every booking on that date is compared with its Outlook event and the
event is created, rewritten or deleted to match. Doing a whole date at once is simple and also
catches route changes of other groups.

It is safe to run again at any time (idempotent): an event that already matches is left alone.
If Outlook can't be reached, the booking itself still stands; the date stays waiting and the
sweep (`outlook_inbound`) tries again every few minutes.

Two things the sync leaves to the sweep, because a person did them in Outlook (SPEC §5.4):
- an event the app wrote that is now gone (deleted in Outlook: the sweep cancels the booking);
- an event whose date, times or subject changed since the app wrote it (the sweep re-checks it).
"""

import logging
from collections.abc import Iterable
from dataclasses import replace
from typing import Annotated, cast

from fastapi import BackgroundTasks, Depends, Request
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from engine.rules import TicketType
from graph.fake import FakeGateway
from graph.format import BookingInfo, booking_event, kept_text, visible_state
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


def wanted_event(booking: models.Booking, existing: CalendarEvent | None) -> CalendarEvent:
    """The event the booking should have: the app's layout and note, plus the text kept from
    before the app (if `existing` has any). Outlook's own details of `existing` are kept."""
    if existing is None:
        return booking_event(booking_info(booking), note=booking.outlook_note)
    wanted = booking_event(
        booking_info(booking), note=booking.outlook_note, kept=kept_text(existing)
    )
    return replace(
        wanted,
        id=existing.id,
        created_by=existing.created_by,
        ical_uid=existing.ical_uid,
        created=existing.created,
    )


# --- the state of the Outlook link ---------------------------------------------------------------


def outlook_state(db: Session) -> models.OutlookState:
    """The one row describing the Outlook link (made on first use)."""
    state = db.get(models.OutlookState, 1)
    if state is None:
        state = models.OutlookState(id=1, last_error="")
        db.add(state)
        db.flush()
    return state


def check_calendar(database: Database, gateway: GraphGateway) -> None:
    """If the calendar isn't the one the stored event ids belong to (a new fake calendar, another
    mailbox), forget what the app knew about its events: the next sync writes them all afresh,
    and the sweep never mistakes them for events deleted in Outlook."""
    current = gateway.calendar_id()
    with database.make_session() as db:
        state = outlook_state(db)
        if state.calendar_id != current:
            if state.calendar_id is not None:
                log.warning(
                    "Outlook calendar changed (%s -> %s): writing every event afresh",
                    state.calendar_id,
                    current,
                )
            db.execute(update(models.Booking).values(outlook_seen=None))
            state.calendar_id = current
        db.commit()


def record_ok(database: Database, *, wrote: bool = False, read: bool = False) -> None:
    """Note that writing to / reading from Outlook just worked."""
    with database.make_session() as db:
        state = outlook_state(db)
        moment = models.utc_now()
        if wrote:
            state.last_write_ok = moment
        if read:
            state.last_read_ok = moment
        db.commit()


def record_error(database: Database, message: str) -> None:
    """Note what went wrong last, for the warning banner."""
    with database.make_session() as db:
        state = outlook_state(db)
        state.last_error = message[:500]
        state.last_error_at = models.utc_now()
        db.commit()


def sync_paused(database: Database) -> bool:
    """True after `uv run restore` until the owner resumes Outlook sync (SPEC §5.6)."""
    with database.make_session() as db:
        state = db.get(models.OutlookState, 1)
        return state is not None and state.sync_paused


def mark_waiting(database: Database, dates: Iterable[str]) -> None:
    """Note dates whose bookings still have to be written to Outlook."""
    with database.make_session() as db:
        for date in set(dates):
            if db.get(models.OutlookPending, date) is None:
                db.add(models.OutlookPending(date=date))
        db.commit()


def waiting_dates(db: Session) -> list[str]:
    """Dates noted as waiting (e.g. because Outlook couldn't be reached)."""
    return list(db.scalars(select(models.OutlookPending.date).order_by(models.OutlookPending.date)))


# --- syncing -----------------------------------------------------------------------------------


def find_event(
    gateway: GraphGateway, event_id: str, events_by_id: dict[str, CalendarEvent]
) -> CalendarEvent | None:
    """The event with this id: from the events already read, else asked for (it may be on
    another date). None if it is gone."""
    event = events_by_id.get(event_id)
    if event is not None:
        return event
    try:
        return gateway.get_event(event_id)
    except EventNotFound:
        return None


def sync_booking(
    gateway: GraphGateway, booking: models.Booking, events_by_id: dict[str, CalendarEvent]
) -> None:
    """Create, rewrite or delete the booking's Outlook event so it matches the booking.

    `events_by_id`: the events already read for the booking's date (saves one call per booking).
    A cancelled booking's event is deleted. Stores the event id and what the event shows on the
    booking (caller commits). Leaves alone an event deleted or changed in Outlook (module docstring).
    """
    event_id = booking.outlook_event_id

    if booking.status == "cancelled":
        if event_id is not None:
            try:  # noqa: SIM105 (plain try/except reads more easily than contextlib.suppress)
                gateway.delete_event(event_id)
            except EventNotFound:
                pass  # already gone
            booking.outlook_event_id = None
            booking.outlook_seen = None
        return

    existing = find_event(gateway, event_id, events_by_id) if event_id is not None else None
    if existing is None:
        if event_id is not None and booking.outlook_seen is not None:
            return  # the app wrote it and it is gone: deleted in Outlook, the sweep decides
        created = gateway.create_event(wanted_event(booking, None))
        booking.outlook_event_id = created.id
        booking.outlook_seen = visible_state(created)
        return

    if booking.outlook_seen is not None and visible_state(existing) != booking.outlook_seen:
        return  # changed in Outlook since the app wrote it: the sweep decides

    wanted = wanted_event(booking, existing)
    if wanted != existing:
        gateway.update_event(wanted)
    booking.outlook_seen = visible_state(wanted)


def sync_date(database: Database, db: Session, gateway: GraphGateway, date: str) -> None:
    """Bring every booking on `date` in step with Outlook. Holds the date lock while it works,
    so a booking made at the same moment can't get two events. The date is no longer waiting."""
    with database.date_lock(db, date):
        events = gateway.list_events(date, date)
        events_by_id = {event.id: event for event in events}
        rows = db.scalars(
            select(models.Booking).where(models.Booking.date == date).order_by(models.Booking.id)
        )
        for booking in rows:
            sync_booking(gateway, booking, events_by_id)
        db.execute(delete(models.OutlookPending).where(models.OutlookPending.date == date))
        db.commit()


def sync_dates(database: Database, gateway: GraphGateway, dates: Iterable[str]) -> bool:
    """Sync each date in its own transaction. A failure is logged and recorded, the date stays
    waiting, and the other dates still sync. Returns True if every date worked."""
    wanted = sorted(set(dates))
    if not wanted:
        return True
    if sync_paused(database):
        return True  # after a restore: nothing is written until the owner resumes (the dates wait)
    all_worked = True
    try:
        check_calendar(database, gateway)
    except Exception as error:
        log.exception("Outlook sync failed: can't reach the calendar")
        record_error(database, f"Writing to Outlook failed: {error}")
        return False
    for date in wanted:
        with database.make_session() as db:
            try:
                sync_date(database, db, gateway, date)
            except Exception as error:
                db.rollback()
                all_worked = False
                log.exception("Outlook sync of %s failed; the sweep will try again", date)
                record_error(database, f"Writing {date} to Outlook failed: {error}")
    if all_worked:
        record_ok(database, wrote=True)
    return all_worked


# --- for the routes ----------------------------------------------------------------------------


class OutlookUpdates:
    """Lets a route ask for Outlook to be updated once its reply has been sent."""

    def __init__(self, database: Database, gateway: GraphGateway, background: BackgroundTasks):
        self.database = database
        self.gateway = gateway
        self.background = background

    def dates_changed(self, *dates: str) -> None:
        """Note the dates as waiting, then sync them after the reply (FastAPI background task)."""
        mark_waiting(self.database, dates)
        self.background.add_task(sync_dates, self.database, self.gateway, list(dates))


def get_outlook(request: Request, background: BackgroundTasks) -> OutlookUpdates:
    """FastAPI dependency: the app's database and calendar, plus this request's background tasks."""
    database: Database = request.app.state.database
    gateway: GraphGateway = request.app.state.gateway
    return OutlookUpdates(database, gateway, background)


OutlookDep = Annotated[OutlookUpdates, Depends(get_outlook)]
