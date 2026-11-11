"""The calendar operations the app needs. The fake and the real gateway both follow this protocol."""

from typing import Protocol

from graph.model import CalendarEvent


class EventNotFound(Exception):
    """The event id is not (or no longer) in the calendar."""


class GraphGateway(Protocol):
    """Read and write events in the Bookings calendar (one mailbox, its main calendar)."""

    def calendar_id(self) -> str:
        """Which calendar this is (e.g. mailbox and calendar). If it changes, the app's stored event
        ids belong to another calendar and the app writes its events afresh."""
        ...

    def list_events(self, first_date: str, last_date: str) -> list[CalendarEvent]:
        """Events starting on `first_date` up to and including `last_date`, sorted by date and start."""
        ...

    def get_event(self, event_id: str) -> CalendarEvent:
        """The event with this id. Raises `EventNotFound`."""
        ...

    def find_event_by_ical_uid(self, ical_uid: str) -> CalendarEvent | None:
        """The event whose iCalendar UID is `ical_uid` (as in an .ics file), or None."""
        ...

    def create_event(self, event: CalendarEvent) -> CalendarEvent:
        """Add `event` (its `id` is ignored) and return it with the id Outlook gave it."""
        ...

    def update_event(self, event: CalendarEvent) -> CalendarEvent:
        """Replace the event with the same id. Raises `EventNotFound`."""
        ...

    def delete_event(self, event_id: str) -> None:
        """Remove the event. Raises `EventNotFound`."""
        ...
