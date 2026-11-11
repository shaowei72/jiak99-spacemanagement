"""What a calendar event looks like to the app.

Like the engine, this package uses no `datetime` objects: a date is a 'YYYY-MM-DD' string and a time
is minutes since midnight (660 = 11:00), in the venue's local time. Only the real gateway converts
to and from Microsoft Graph's own date format.
"""

from dataclasses import dataclass

# Categories the app sets besides the ticket type (SPEC §5.4 "Sync rules").
CATEGORY_CONFLICT = "Conflict"
CATEGORY_NEEDS_FORMAT = "Needs format"
CATEGORY_NOT_IN_APP = "Not in app"


@dataclass(frozen=True)
class CalendarEvent:
    """One event in the Bookings calendar.

    `id` is Outlook's event id ("" for an event not yet created). `booking_id` is the app's booking id,
    kept in an open extension on the event; None means the app hasn't taken this event over (yet).
    `created_by` is the organiser's email address when known, else "".
    `ical_uid` is the event's iCalendar UID (Graph's `iCalUId`): the same id an .ics file saved from
    the calendar shows, so the take-over can find the event it read in the file. "" when unknown.
    `created` is when the event was made in Outlook, as sortable text ('2026-11-11T09:30:00Z'), so
    entries typed during an outage can be taken in oldest first. "" when unknown.
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
    ical_uid: str = ""
    created: str = ""
