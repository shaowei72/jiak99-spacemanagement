"""An in-memory Bookings calendar, for development and tests. Never talks to Microsoft."""

from dataclasses import replace

from graph.gateway import EventNotFound
from graph.model import CalendarEvent


class FakeGateway:
    """Keeps events in a dict. `calls` records every write, so tests can check what the app did."""

    def __init__(self, events: list[CalendarEvent] | None = None) -> None:
        self.events: dict[str, CalendarEvent] = {}
        self.calls: list[tuple[str, str]] = []  # (action, event id), e.g. ("update", "fake-3")
        self._next_number = 1
        for event in events or []:
            if event.id:
                self.events[event.id] = event
            else:
                self.create_event(event)
        self.calls.clear()  # events given at the start are not calls made by the app

    def list_events(self, first_date: str, last_date: str) -> list[CalendarEvent]:
        """Events starting on `first_date` up to and including `last_date`, sorted by date and start."""
        found = [e for e in self.events.values() if first_date <= e.date <= last_date]
        found.sort(key=lambda e: (e.date, e.start, e.id))
        return found

    def get_event(self, event_id: str) -> CalendarEvent:
        """The event with this id. Raises `EventNotFound`."""
        if event_id not in self.events:
            raise EventNotFound(event_id)
        return self.events[event_id]

    def create_event(self, event: CalendarEvent) -> CalendarEvent:
        """Add `event` with a new id ('fake-1', 'fake-2', ...) and return it."""
        new_id = f"fake-{self._next_number}"
        self._next_number += 1
        created = replace(event, id=new_id)
        self.events[new_id] = created
        self.calls.append(("create", new_id))
        return created

    def update_event(self, event: CalendarEvent) -> CalendarEvent:
        """Replace the event with the same id. Raises `EventNotFound`."""
        if event.id not in self.events:
            raise EventNotFound(event.id)
        self.events[event.id] = event
        self.calls.append(("update", event.id))
        return event

    def delete_event(self, event_id: str) -> None:
        """Remove the event. Raises `EventNotFound`."""
        if event_id not in self.events:
            raise EventNotFound(event_id)
        del self.events[event_id]
        self.calls.append(("delete", event_id))
