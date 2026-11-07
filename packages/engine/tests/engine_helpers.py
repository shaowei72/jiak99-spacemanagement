"""Shared helpers for engine tests: build days and enquiries with readable 'HH:MM' times."""

import itertools

from engine import DayState, Enquiry, Session, Settings
from engine.rules import TicketType

TODAY = "2026-12-01"
DEFAULTS = Settings()
_ids = itertools.count(1)


def t(hhmm: str) -> int:
    """'10:30' -> 630 minutes since midnight."""
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def session(
    start: str,
    ticket_type: TicketType,
    people: int,
    *,
    zone: str | None = None,
    pinned: bool = False,
    date: str = TODAY,
) -> Session:
    return Session(
        id=f"s{next(_ids)}",
        date=date,
        start=t(start),
        ticket_type=ticket_type,
        adults=people,
        zone_id=zone,
        pinned=pinned,
    )


def schedule(*sessions: Session, date: str = TODAY) -> dict[str, DayState]:
    return {date: DayState(date=date, sessions=tuple(sessions))}


def enquiry(start: str, ticket_type: TicketType, people: int, *, date: str = TODAY) -> Enquiry:
    return Enquiry(date=date, start=t(start), ticket_type=ticket_type, adults=people)
