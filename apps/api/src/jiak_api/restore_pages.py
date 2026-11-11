"""The after-restore list (SPEC §5.6): after `uv run restore`, what differs between Outlook and the
restored database. It only lists; the owner fixes things in the app, then resumes Outlook sync.

Outlook is newer than the backup, so it lists:
1. Outlook events with a booking id the database doesn't have: booked after the backup. Book them
   in the app, then delete the old event in Outlook;
2. bookings whose Outlook event is gone: most likely cancelled after the backup. Cancel them in the
   app, or leave them if they are still on;
3. bookings whose event shows another date, time, ticket, headcount or customer: most likely changed
   after the backup. Edit the booking to match, or leave it;
4. hand-typed entries, for information: the normal sweep takes them in after resuming.

"Resume Outlook sync" makes the database the truth again: every booking's event is written afresh
(missing ones made again, differing ones rewritten), events with an unknown booking id get the
category "Not in app" and a note (never deleted), then the normal sweep runs.
"""

from dataclasses import dataclass, field, replace
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from engine.dates import add_days
from engine.text import format_time
from graph.format import compose_body, read_booking_subject, split_body
from graph.gateway import EventNotFound, GraphGateway
from graph.model import CATEGORY_NOT_IN_APP, CalendarEvent
from graph.template import parse_template
from jiak_api import models
from jiak_api.clock import Now
from jiak_api.db import Database
from jiak_api.ops_pages import page
from jiak_api.outlook_inbound import DAYS_AHEAD, looks_like_a_booking, sweep_and_record
from jiak_api.outlook_sync import customer_text, outlook_state, sync_dates
from jiak_api.routes import DatabaseDep, NowDep, OperationsUser

router = APIRouter()

NOT_IN_APP_NOTE = (
    "NOT IN THE APP: this booking was made after the backup the app was restored from.\n"
    "Book it in the app, then delete this event."
)


@dataclass
class Difference:
    """One line of the list: the booking (if any), the event (if any) and what differs."""

    when: str  # '2026-12-01 11:00'
    name: str
    detail: str
    booking_id: str | None = None


@dataclass
class RestoreList:
    unknown: list[Difference] = field(default_factory=list)  # 1. booked after the backup
    gone: list[Difference] = field(default_factory=list)  # 2. event gone
    changed: list[Difference] = field(default_factory=list)  # 3. event differs
    typed: list[Difference] = field(default_factory=list)  # 4. hand-typed

    @property
    def count(self) -> int:
        return len(self.unknown) + len(self.gone) + len(self.changed)


def what_differs(booking: models.Booking, event: CalendarEvent) -> list[str]:
    """How the event differs from the booking, e.g. ['time: Outlook 10:00, app 11:00']."""
    found: list[str] = []
    if event.date != booking.date:
        found.append(f"date: Outlook {event.date}, app {booking.date}")
    if event.start != booking.start:
        found.append(f"time: Outlook {format_time(event.start)}, app {format_time(booking.start)}")
    read = read_booking_subject(event.subject)
    people = booking.adults + booking.children
    if read is None:
        found.append(f"subject: Outlook '{event.subject}'")
    else:
        ticket, event_people = read
        if ticket != booking.ticket_type:
            found.append(f"ticket: Outlook {ticket}, app {booking.ticket_type}")
        if event_people != people:
            found.append(f"people: Outlook {event_people}, app {people}")
        parts = [part.strip() for part in event.subject.split("·")]
        customer = customer_text(booking.customer)
        if len(parts) > 1 and parts[1] != customer:
            found.append(f"customer: Outlook '{parts[1]}', app '{customer}'")
    return found


def compare(db: Session, gateway: GraphGateway, today: str) -> RestoreList:
    """The after-restore list (module docstring). Reads Outlook; changes nothing."""
    events = gateway.list_events(today, add_days(today, DAYS_AHEAD))
    listed = {event.id: event for event in events}
    result = RestoreList()

    for event in events:
        when = f"{event.date} {format_time(event.start)}"
        if event.booking_id is None:
            if parse_template(event.subject) is not None or looks_like_a_booking(event.subject):
                result.typed.append(Difference(when, event.subject, "typed in Outlook"))
            continue
        if db.get(models.Booking, event.booking_id) is None:
            result.unknown.append(Difference(when, event.subject, "not in the app"))

    bookings = db.scalars(
        select(models.Booking)
        .where(
            models.Booking.status == "confirmed",
            models.Booking.date >= today,
            models.Booking.outlook_event_id.is_not(None),
        )
        .order_by(models.Booking.date, models.Booking.start)
    )
    for booking in bookings:
        when = f"{booking.date} {format_time(booking.start)}"
        name = customer_text(booking.customer)
        event_id = booking.outlook_event_id
        assert event_id is not None  # selected above
        found = listed.get(event_id)
        if found is None:
            try:
                found = gateway.get_event(event_id)  # moved beyond the dates read?
            except EventNotFound:
                result.gone.append(Difference(when, name, "event gone from Outlook", booking.id))
                continue
        differences = what_differs(booking, found)
        if differences:
            result.changed.append(Difference(when, name, "; ".join(differences), booking.id))
    return result


def mark_not_in_app(gateway: GraphGateway, event: CalendarEvent) -> None:
    """Put the 'Not in app' note and category on an event whose booking the app doesn't have.
    Its whole old body is kept below the divider; the event is never deleted."""
    if CATEGORY_NOT_IN_APP in event.categories:
        return
    _app_part, below = split_body(event.body)
    kept = below if below is not None else event.body
    gateway.update_event(
        replace(
            event,
            body=compose_body(NOT_IN_APP_NOTE, kept),
            categories=(*event.categories, CATEGORY_NOT_IN_APP),
        )
    )


def resume(database: Database, gateway: GraphGateway, now: Now) -> list[str]:
    """Resume Outlook sync (module docstring). Returns the dates to write to Outlook."""
    events = gateway.list_events(now.today, add_days(now.today, DAYS_AHEAD))
    with database.make_session() as db:
        for event in events:
            if event.booking_id is not None and db.get(models.Booking, event.booking_id) is None:
                mark_not_in_app(gateway, event)
        state = outlook_state(db)
        state.sync_paused = False
        db.execute(update(models.Booking).values(outlook_seen=None))  # write every event afresh
        dates = db.scalars(
            select(models.Booking.date)
            .where(models.Booking.status != "cancelled", models.Booking.date >= now.today)
            .distinct()
        )
        to_write = list(dates)
        db.commit()
    return to_write


def resume_in_background(
    database: Database, gateway: GraphGateway, dates: list[str], now: Now
) -> None:
    """After the reply: write every booking's event, then sweep."""
    sync_dates(database, gateway, dates)
    sweep_and_record(database, gateway, lambda: now)


@router.get("/restore-check")
def restore_check(
    request: Request, user: OperationsUser, database: DatabaseDep, now: NowDep
) -> Response:
    """The after-restore list."""
    gateway: GraphGateway = request.app.state.gateway
    context: dict[str, Any] = {"listing": None, "error": ""}
    with database.make_session() as db:
        state = outlook_state(db)
        context["paused"] = state.sync_paused
        context["restored_from"] = state.restored_from
        if state.sync_paused:
            try:
                context["listing"] = compare(db, gateway, now.today)
            except Exception as error:
                context["error"] = f"Outlook couldn't be read: {error}"
        db.commit()
    return page(request, "restore_check.html", context)


@router.post("/restore-check/resume")
def restore_resume(
    request: Request,
    user: OperationsUser,
    database: DatabaseDep,
    now: NowDep,
    background: BackgroundTasks,
) -> Response:
    """Resume Outlook sync, then come back to the page (which then says so)."""
    gateway: GraphGateway = request.app.state.gateway
    dates = resume(database, gateway, now)
    background.add_task(resume_in_background, database, gateway, dates, now)
    return RedirectResponse("/restore-check", status_code=303)
