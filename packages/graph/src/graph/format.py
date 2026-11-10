"""The subject and body the app writes for a booking (SPEC §5.4).

Subject: 'PHE+CT · Tan family · 18 pax · ABDC · 11:00–14:00'
         (short name · customer · people · route · times). A split group lists every sub-group's
         route: 'ABCD/DCBA'. The route part is left out while the plan hasn't given every group a route.
Body:    a fixed layout, so staff always find things in the same place. Empty parts are left out.
"""

from dataclasses import dataclass

from engine.rules import TICKET_MINUTES, TICKET_NAMES, TICKET_SHORT_NAMES, TicketType
from engine.text import format_time
from graph.model import CalendarEvent


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


def booking_event(info: BookingInfo, event_id: str = "", created_by: str = "") -> CalendarEvent:
    """The whole calendar event for a booking. Pass `event_id` to update an existing event in place."""
    return CalendarEvent(
        id=event_id,
        date=info.date,
        start=info.start,
        end=info.end,
        subject=booking_subject(info),
        body=booking_body(info),
        categories=(ticket_category(info.ticket_type),),
        booking_id=info.id,
        created_by=created_by,
    )
