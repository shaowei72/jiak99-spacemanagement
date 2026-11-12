"""The review sheet for taking over the Bookings calendar (SPEC §5.4 one-off migration, steps 1-3).

Every booking-like event becomes one row with what the old-style reader found and a proposed
action. Nothing is stored: the owner corrects the sheet (e.g. fills in missing headcounts) and
`import-outlook --review FILE --commit` applies it later.

Proposed actions:
- 'history'          past event (before the take-over date): kept as history only
- 'book'             future event the app will try to book (normal enquiry check; may still not fit)
- 'fill in'          future event missing its headcount or ticket type: fill those in on the sheet
- 'check by hand'    future event the reader can't judge (all-day, or tagged "rescheduled")
- 'over 100'         future group over 100 people: not booked; ops blocks the venue on the Day plan
- 'skip (cancelled)' future event marked cancelled
"""

import csv
import io
from dataclasses import dataclass

from engine.rules import GRID, TICKET_MINUTES, TICKET_SHORT_NAMES
from engine.text import format_time
from graph.ics import MINUTES_PER_DAY, IcsEvent
from graph.oldstyle import STATUS_RESCHEDULED, STATUS_SOFT_BLOCK, ClientRule, read_old_entry

ACTION_HISTORY = "history"
ACTION_BOOK = "book"
ACTION_FILL_IN = "fill in"
ACTION_CHECK = "check by hand"
ACTION_OVER_100 = "over 100"
ACTION_SKIP = "skip (cancelled)"

MAX_PEOPLE = 100  # one start time takes at most 4 zones x 25 people

CSV_COLUMNS = (
    "date",
    "start",
    "end",
    "people",
    "adults",
    "children",
    "ticket",
    "client",
    "status",
    "soft_block",
    "cancelled",
    "when",
    "action",
    "notes",
    "people_found",
    "ticket_found",
    "subject",
    "event_id",
)


@dataclass(frozen=True)
class ReviewRow:
    """One line of the review sheet. Times are 'HH:MM'; `ticket` is a ticket code or ''."""

    event_id: str
    date: str
    start: str
    end: str
    people: int | None
    adults: int | None
    children: int | None
    ticket: str
    client: str
    status: str
    soft_block: bool
    cancelled: bool
    when: str  # 'past' or 'future'
    action: str
    notes: str
    people_found: str
    ticket_found: str
    subject: str


@dataclass(frozen=True)
class Review:
    """The review sheet plus the events that were left alone (meetings, site visits, ...)."""

    rows: tuple[ReviewRow, ...]
    others: tuple[IcsEvent, ...]


def time_text(minutes: int) -> str:
    """'10:00'; an end on a later day gets '+1d' etc."""
    days, minutes = divmod(minutes, MINUTES_PER_DAY)
    if days:
        return f"{format_time(minutes)}+{days}d"
    return format_time(minutes)


def _action(
    future: bool, cancelled: bool, all_day: bool, status: str, people: int | None, ticket: str
) -> str:
    """The proposed action for one row (see the module docstring)."""
    if not future:
        return ACTION_HISTORY
    if cancelled:
        return ACTION_SKIP
    if all_day or status == STATUS_RESCHEDULED:
        return ACTION_CHECK
    if people is not None and people > MAX_PEOPLE:
        return ACTION_OVER_100  # not booked, so a missing ticket type doesn't matter
    if people is None or not ticket:
        return ACTION_FILL_IN
    return ACTION_BOOK


def action_for(row: ReviewRow) -> str:
    """The proposed action for a row whose cells were changed (e.g. carried over from an
    earlier sheet)."""
    return _action(
        row.when == "future", row.cancelled, not row.start, row.status, row.people, row.ticket
    )


def review_row(event: IcsEvent, today: str, rules: list[ClientRule]) -> ReviewRow | None:
    """The review row for one event, or None if it isn't booking-like. `today` is the take-over
    date: events before it are 'past'."""
    entry = read_old_entry(event.subject, event.body, rules)
    if not entry.booking_like:
        return None
    cancelled = entry.cancelled or event.cancelled
    future = event.date >= today
    ticket = entry.ticket_type or ""
    headcount = entry.headcount

    notes = list(entry.notes)
    if headcount is None:
        hint = f" (body mentions {', '.join(entry.body_counts)})" if entry.body_counts else ""
        notes.append(f"no headcount found{hint}")
    if not ticket:
        notes.append("no ticket type found")
    if entry.status == STATUS_RESCHEDULED:
        notes.append("tagged rescheduled: is it still on this date?")
    if not entry.client:
        notes.append("no client name found")
    if event.all_day:
        notes.append("all-day event: no start time")
    else:
        if event.start % GRID:
            notes.append(f"starts at {format_time(event.start)}, not on the 30-min grid")
        if entry.ticket_type is not None:
            length = event.end - event.start
            expected = TICKET_MINUTES[entry.ticket_type]
            if length != expected:
                short = TICKET_SHORT_NAMES[entry.ticket_type]
                notes.append(f"event is {length} min, a {short} stay is {expected} min")
    if event.recurring:
        notes.append("repeating event: only its first date is read")

    people = headcount.people if headcount is not None else None
    return ReviewRow(
        event_id=event.uid,
        date=event.date,
        start="" if event.all_day else time_text(event.start),
        end="" if event.all_day else time_text(event.end),
        people=people,
        adults=headcount.adults if headcount is not None else None,
        children=headcount.children if headcount is not None else None,
        ticket=ticket,
        client=entry.client,
        status=entry.status,
        soft_block=entry.status == STATUS_SOFT_BLOCK,
        cancelled=cancelled,
        when="future" if future else "past",
        action=_action(future, cancelled, event.all_day, entry.status, people, ticket),
        notes="; ".join(notes),
        people_found=entry.people_found,
        ticket_found=entry.ticket_found,
        subject=event.subject,
    )


def build_review(events: list[IcsEvent], today: str, rules: list[ClientRule]) -> Review:
    """The review sheet for a calendar's events, sorted by date and start time."""
    rows: list[ReviewRow] = []
    others: list[IcsEvent] = []
    for event in sorted(events, key=lambda e: (e.date, e.start, e.uid)):
        row = review_row(event, today, rules)
        if row is None:
            others.append(event)
        else:
            rows.append(row)
    return Review(rows=tuple(rows), others=tuple(others))


def _cell(value: str | int | bool | None) -> str:
    """How a value is written in the CSV: None -> '', True -> 'yes', False -> 'no'."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def review_csv(rows: tuple[ReviewRow, ...] | list[ReviewRow]) -> str:
    """The review sheet as CSV text, one row per event, with a header row."""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow([_cell(getattr(row, column)) for column in CSV_COLUMNS])
    return output.getvalue()


def count_actions(rows: tuple[ReviewRow, ...] | list[ReviewRow]) -> dict[str, int]:
    """How many rows have each proposed action, in a fixed order (actions with no rows left out)."""
    order = (
        ACTION_BOOK,
        ACTION_FILL_IN,
        ACTION_CHECK,
        ACTION_OVER_100,
        ACTION_SKIP,
        ACTION_HISTORY,
    )
    counts: dict[str, int] = {}
    for action in order:
        number = sum(1 for row in rows if row.action == action)
        if number:
            counts[action] = number
    return counts
