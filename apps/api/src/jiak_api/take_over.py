"""Applying the corrected review sheet: the one-off take-over of the Bookings calendar (SPEC §5.4,
step 4). `import-outlook --review FILE` calls `apply_sheet`.

- past rows become PastBooking rows (history only);
- future rows become real bookings through the normal enquiry check (groups over 25 split evenly).
  Their Outlook event is taken over in place: found by its iCalendar UID, linked to the booking,
  and then rewritten in the app's format by the normal sync. No second event is made;
- rows that don't fit, aren't ready, are over 100 people or whose event is gone are listed, and
  nothing is stored for them (their Outlook event is left as it is);
- a row already taken over (same event id) is skipped, so running it again is safe.

Without `commit`, everything is checked exactly the same way and then rolled back: a rehearsal
that shows what would happen.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine import Enquiry
from engine.text import format_time
from graph.format import booking_subject
from graph.gateway import GraphGateway
from graph.model import CalendarEvent
from graph.sheet import (
    DECISION_BOOK,
    DECISION_HISTORY,
    DECISION_NOT_READY,
    DECISION_OVER_100,
    DECISION_SKIP,
    SheetRow,
    decide,
    past_times,
    ticket_source,
)
from jiak_api import audit, models
from jiak_api.bookings import check, new_sessions
from jiak_api.clock import Now
from jiak_api.db import Database
from jiak_api.outlook_sync import booking_info, sync_dates

TAKE_OVER_ACTOR = "import-outlook"

# What happened to a row (besides the sheet's own decisions in graph.sheet).
OUTCOME_BOOKED = "booked"
OUTCOME_HISTORY = "history"
OUTCOME_SKIPPED = "skipped"
OUTCOME_ALREADY = "already done"
OUTCOME_OVER_100 = "over 100"
OUTCOME_NOT_READY = "not ready"
OUTCOME_NO_FIT = "doesn't fit"
OUTCOME_NO_EVENT = "event not found"

# Outcomes a person has to act on, in the order the report lists them.
TO_SORT_OUT = (OUTCOME_NO_FIT, OUTCOME_NO_EVENT, OUTCOME_OVER_100, OUTCOME_NOT_READY)


@dataclass(frozen=True)
class RowOutcome:
    """What the take-over did (or would do) with one sheet row."""

    row: SheetRow
    outcome: str
    message: str = ""  # why, or for a booking the new Outlook subject


@dataclass(frozen=True)
class TakeOverResult:
    outcomes: tuple[RowOutcome, ...]
    committed: bool
    booked_dates: tuple[str, ...]  # dates whose Outlook events were (or would be) rewritten

    def count(self, outcome: str) -> int:
        return sum(1 for item in self.outcomes if item.outcome == outcome)


def taken_over_ids(db: Session) -> set[str]:
    """Event ids already stored by an earlier take-over (as history or as a booking)."""
    past = db.scalars(select(models.PastBooking.source_event_id))
    booked = db.scalars(
        select(models.Booking.source_event_id).where(models.Booking.source_event_id.is_not(None))
    )
    return {event_id for event_id in [*past, *booked] if event_id}


def customer_name(row: SheetRow) -> str:
    """The client column, else the old subject (the customer table needs a name)."""
    name = row.client or row.subject or "(no name)"
    return name[:200]


def past_booking(row: SheetRow) -> models.PastBooking:
    """The history row for a past event."""
    start, end = past_times(row)
    people = row.people
    if people is None and (row.adults is not None or row.children is not None):
        people = (row.adults or 0) + (row.children or 0)
    return models.PastBooking(
        date=row.date,
        start=start,
        end=end,
        people=people,
        ticket_type=row.ticket,
        type_source=ticket_source(row),
        client=row.client[:200],
        status="cancelled" if row.cancelled else "confirmed",
        was_soft_block=row.soft_block,
        source_event_id=row.event_id,
        original_subject=row.subject[:500],
    )


def book_row(
    db: Session,
    gateway: GraphGateway,
    row: SheetRow,
    enquiry: Enquiry,
    now: Now,
) -> RowOutcome:
    """Book one future row if its Outlook event is there and it fits. Stores nothing otherwise."""
    event: CalendarEvent | None = gateway.find_event_by_ical_uid(row.event_id)
    if event is None:
        return RowOutcome(
            row, OUTCOME_NO_EVENT, "no Outlook event with this event_id (deleted?); not booked"
        )
    result = check(db, enquiry, now)
    if result.verdict == "NOT_POSSIBLE":
        return RowOutcome(row, OUTCOME_NO_FIT, result.reason)

    booking = models.Booking(
        customer=models.Customer(name=customer_name(row)),
        date=enquiry.date,
        start=enquiry.start,
        ticket_type=enquiry.ticket_type,
        adults=enquiry.adults,
        children=enquiry.children,
        status="confirmed",  # soft blocks count as confirmed (SPEC §5.4)
        notes=f"Taken over from Outlook: {row.subject}",
        created_by=TAKE_OVER_ACTOR,
        outlook_event_id=event.id,
        source_event_id=row.event_id,
    )
    booking.sessions.extend(new_sessions(enquiry))
    db.add(booking)
    db.flush()  # the next check sees this booking; gives the rows ids for the audit entry
    audit.record(
        db,
        actor=TAKE_OVER_ACTOR,
        action="booking.take_over",
        entity="booking",
        entity_id=booking.id,
        before=None,
        after=audit.booking_snapshot(booking),
    )
    return RowOutcome(row, OUTCOME_BOOKED, booking_subject(booking_info(booking)))


def apply_sheet(
    database: Database,
    db: Session,
    gateway: GraphGateway,
    rows: list[SheetRow],
    *,
    today: str,
    now: Now,
    commit: bool,
) -> TakeOverResult:
    """Apply the sheet's rows (see the module docstring). `today` is the take-over date.

    All dates with something to book are locked for the whole run, so no one books them at the
    same moment. With `commit`, the bookings and history are stored and then the Outlook events
    of the booked dates are rewritten; without it, everything is rolled back.
    """
    already = taken_over_ids(db)
    decisions = [(row, decide(row, today)) for row in rows]
    book_dates = sorted({row.date for row, decision in decisions if decision.kind == DECISION_BOOK})

    outcomes: list[RowOutcome] = []
    booked_dates: set[str] = set()
    with database.date_locks(db, book_dates):
        # Earlier times first, so a day fills up in the order the groups come.
        in_order = sorted(decisions, key=lambda pair: (pair[0].date, pair[0].start or 0))
        for row, decision in in_order:
            if decision.kind in (DECISION_HISTORY, DECISION_BOOK) and row.event_id in already:
                outcomes.append(RowOutcome(row, OUTCOME_ALREADY, "taken over in an earlier run"))
            elif decision.kind == DECISION_HISTORY:
                db.add(past_booking(row))
                outcomes.append(RowOutcome(row, OUTCOME_HISTORY))
            elif decision.kind == DECISION_BOOK:
                assert row.ticket is not None and row.start is not None  # decide() checked
                enquiry = Enquiry(
                    date=row.date,
                    start=row.start,
                    ticket_type=row.ticket,
                    adults=decision.adults,
                    children=decision.children,
                )
                outcome = book_row(db, gateway, row, enquiry, now)
                if outcome.outcome == OUTCOME_BOOKED:
                    booked_dates.add(row.date)
                outcomes.append(outcome)
            elif decision.kind == DECISION_SKIP:
                outcomes.append(RowOutcome(row, OUTCOME_SKIPPED, decision.reason))
            elif decision.kind == DECISION_OVER_100:
                outcomes.append(RowOutcome(row, OUTCOME_OVER_100, decision.reason))
            else:
                assert decision.kind == DECISION_NOT_READY
                outcomes.append(RowOutcome(row, OUTCOME_NOT_READY, decision.reason))
        if commit:
            db.commit()
        else:
            db.rollback()

    if commit:
        # The normal sync rewrites each linked event in place (subject, body, times, booking id).
        sync_dates(database, gateway, booked_dates)

    outcomes.sort(key=lambda item: item.row.line)
    return TakeOverResult(
        outcomes=tuple(outcomes), committed=commit, booked_dates=tuple(sorted(booked_dates))
    )


def row_label(row: SheetRow) -> str:
    """'2026-11-20 11:00  Sunshine TourGroup (line 7)' for the report."""
    start = format_time(row.start) if row.start is not None else "all day"
    name = row.client or row.subject or row.event_id
    return f"{row.date or '?':<10} {start:>7}  {name} (line {row.line})"
