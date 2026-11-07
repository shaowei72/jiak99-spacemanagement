"""Availability board: what can be sold to a group of N on one day (SPEC §4.5)."""

from dataclasses import dataclass
from typing import Literal

from engine.assign import assign
from engine.evaluate import new_floating_sessions, validity_problem, zone_sizes
from engine.model import Enquiry, Schedule, Settings, day_of
from engine.rules import START_TIMES, TICKET_TYPES, TicketType

SlotStatus = Literal["available", "full", "not_offered"]


@dataclass(frozen=True)
class Slot:
    """One start time × ticket type on the board.

    available:   enough free zones can be found for the group.
    full:        not enough free zones (a zone taken by any group is sold out).
    not_offered: the slot can't be sold at all (ends after close, past, blackout, closed day...).
    """

    start: int
    ticket_type: TicketType
    status: SlotStatus


def availability(
    schedule: Schedule,
    settings: Settings,
    date: str,
    people: int,
    *,
    today: str,
    now_minute: int | None = None,
) -> tuple[Slot, ...]:
    """The board for `date` and a group of `people`, in start-time then ticket-type order."""
    day = day_of(schedule, date)
    slots: list[Slot] = []
    for start in START_TIMES:
        for ticket_type in TICKET_TYPES:
            enquiry = Enquiry(date, start, ticket_type, adults=people)
            if validity_problem(enquiry, day, settings, today=today, now_minute=now_minute):
                slots.append(Slot(start, ticket_type, "not_offered"))
                continue
            new = new_floating_sessions(enquiry, zone_sizes(people))
            fits = assign(day.sessions + new, settings, blackouts=day.blackouts, optimise=False)
            slots.append(Slot(start, ticket_type, "available" if fits.ok else "full"))
    return tuple(slots)
