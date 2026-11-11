"""The subject and body the app writes for a booking (SPEC §5.4).

Subject: 'PHE+CT · Tan family · 18 pax · ABDC · 11:00–14:00'
         (short name · customer · people · route · times). A split group lists every sub-group's
         route: 'ABCD/DCBA'. The route part is left out while the plan hasn't given every group a route.
Body:    a fixed layout, so staff always find things in the same place. Empty parts are left out.
         A note from the app (e.g. why an Outlook change wasn't made) goes on top. An event the app
         took over keeps its old text below a divider line; the app never changes that part.
"""

import re
from dataclasses import dataclass

from engine.rules import TICKET_MINUTES, TICKET_NAMES, TICKET_SHORT_NAMES, TicketType
from engine.text import format_time
from graph.model import CATEGORY_CONFLICT, CalendarEvent
from graph.template import ticket_from_words

DIVIDER = "── Before the app ──"

_PEOPLE = re.compile(r"^(\d+)\s*pax$", re.IGNORECASE)


@dataclass(frozen=True)
class BookingInfo:
    """What the calendar shows about one booking. `routes` has one entry per sub-group (None = not
    planned yet). `contact` is ready to show, e.g. 'Mrs Tan, 9123 4567 / tan@example.com', or ''."""

    id: str
    date: str
    start: int
    ticket_type: TicketType
    customer: str
    adults: int
    children: int
    routes: tuple[str | None, ...]
    status: str = "confirmed"
    contact: str = ""
    notes: str = ""

    @property
    def end(self) -> int:
        """When the stay ends, in minutes since midnight."""
        return self.start + TICKET_MINUTES[self.ticket_type]

    @property
    def people(self) -> int:
        """Adults and children together."""
        return self.adults + self.children


def times_text(info: BookingInfo) -> str:
    """'11:00–14:00'."""
    return f"{format_time(info.start)}–{format_time(info.end)}"


def routes_text(info: BookingInfo) -> str:
    """'ABDC', or 'ABCD/DCBA' for a split group; '' while any sub-group has no route yet."""
    known: list[str] = []
    for route in info.routes:
        if route is None:
            return ""
        known.append(route)
    return "/".join(known)


def ticket_category(ticket_type: TicketType) -> str:
    """The Outlook category for a ticket type (its colour is set up once in the mailbox): 'PHE+CT'."""
    return TICKET_SHORT_NAMES[ticket_type]


def booking_subject(info: BookingInfo) -> str:
    """'PHE+CT · Tan family · 18 pax · ABDC · 11:00–14:00'."""
    parts = [TICKET_SHORT_NAMES[info.ticket_type], info.customer, f"{info.people} pax"]
    route = routes_text(info)
    if route:
        parts.append(route)
    parts.append(times_text(info))
    return " · ".join(parts)


def booking_body(info: BookingInfo) -> str:
    """The fixed body layout, one item group per line. Empty parts and an empty notes line are left out."""
    lines: list[str] = []
    lines.append(
        f"Booking: {info.id}   Ticket: {TICKET_NAMES[info.ticket_type]}   Status: {info.status}"
    )

    customer_line = f"Customer: {info.customer}"
    if info.contact:
        customer_line += f"   Contact: {info.contact}"
    lines.append(customer_line)

    people_line = f"People: {info.people}"
    if info.children > 0:
        people_line += f" ({info.adults} adults, {info.children} children)"
    route = routes_text(info)
    if route:
        people_line += f"   Route: {route}"
    people_line += f"   Times: {times_text(info)}"
    lines.append(people_line)

    if info.notes.strip():
        lines.append(f"Notes: {info.notes.strip()}")
    return "\n".join(lines)


def booking_event(
    info: BookingInfo,
    event_id: str = "",
    created_by: str = "",
    *,
    note: str = "",
    kept: str | None = None,
) -> CalendarEvent:
    """The whole calendar event for a booking. Pass `event_id` to update an existing event in place.

    `note`: a message from the app for whoever looks at the event; it goes on top and adds the
    'Conflict' category. `kept`: text kept from before the app took the event over (None: none).
    """
    app_part = booking_body(info)
    categories = [ticket_category(info.ticket_type)]
    if note:
        app_part = f"{note}\n\n{app_part}"
        categories.append(CATEGORY_CONFLICT)
    return CalendarEvent(
        id=event_id,
        date=info.date,
        start=info.start,
        end=info.end,
        subject=booking_subject(info),
        body=compose_body(app_part, kept),
        categories=tuple(categories),
        booking_id=info.id,
        created_by=created_by,
    )


# --- text kept from before the app (SPEC §5.4 "Text kept from before the app") ----------------------


def split_body(body: str) -> tuple[str, str | None]:
    """(the app's part, the kept text below the divider). The kept text is None if there is no
    divider line, and exactly as it was otherwise."""
    lines = body.split("\n")
    for number, line in enumerate(lines):
        if line.strip() == DIVIDER:
            above = "\n".join(lines[:number]).rstrip()
            below = "\n".join(lines[number + 1 :])
            return above, below
    return body, None


def kept_text(event: CalendarEvent) -> str | None:
    """The text the app must keep when it rewrites `event`: whatever is below the divider; for an
    event the app hasn't taken over yet (no booking id), the whole body; else None."""
    _app_part, below = split_body(event.body)
    if below is not None:
        return below
    if event.booking_id is None and event.body.strip():
        return event.body
    return None


def compose_body(app_part: str, kept: str | None) -> str:
    """The app's part, then the divider and the kept text (if any)."""
    if kept is None:
        return app_part
    return f"{app_part}\n\n{DIVIDER}\n{kept}"


# --- reading what people changed in Outlook ---------------------------------------------------


def visible_state(event: CalendarEvent) -> str:
    """The parts of an event a person sees and may change in Outlook: date, times and subject.
    The app stores this after each write; if the event no longer matches, someone changed it."""
    return f"{event.date} {event.start}-{event.end} {event.subject}"


def read_booking_subject(subject: str) -> tuple[TicketType, int] | None:
    """The ticket type and headcount from a subject in the app's format
    ('PHE+CT · Tan family · 18 pax · ABDC · 11:00–14:00'), or None if it can't be read."""
    parts = [part.strip() for part in subject.split("·")]
    if len(parts) < 3:
        return None
    ticket_type = ticket_from_words(parts[0])
    if ticket_type is None:
        return None
    for part in parts[1:]:
        match = _PEOPLE.match(part)
        if match:
            return ticket_type, int(match.group(1))
    return None
