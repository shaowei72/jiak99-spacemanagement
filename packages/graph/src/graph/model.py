"""What a calendar event looks like to the app.

Like the engine, this package uses no `datetime` objects: a date is a 'YYYY-MM-DD' string and a time
is minutes since midnight (660 = 11:00), in the venue's local time. Only the real gateway converts
to and from Microsoft Graph's own date format.
"""

from dataclasses import dataclass

# Categories the app sets besides the ticket type (SPEC §5.4 "Sync rules").
CATEGORY_CONFLICT = "Conflict"
CATEGORY_NEEDS_FORMAT = "Needs format"


@dataclass(frozen=True)
class CalendarEvent:
    """One event in the Bookings calendar.

    `id` is Outlook's event id ("" for an event not yet created). `booking_id` is the app's booking id,
    kept in an open extension on the event; None means the app hasn't taken this event over (yet).
    `created_by` is the organiser's email address when known, else "".
    """

    id: str
    date: str
    start: int
    end: int
    subject: str
    body: str = ""
    categories: tuple[str, ...] = ()
    booking_id: str | None = None
    created_by: str = ""
