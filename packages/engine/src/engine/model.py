"""Domain types used by the engine (SPEC §3).

Times are minutes since midnight (int). Dates are 'YYYY-MM-DD' strings in Singapore time.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from engine.rules import TICKET_MINUTES, TicketType

SessionStatus = Literal["open", "locked", "in_progress", "done"]


@dataclass(frozen=True)
class Session:
    """One group in one zone for one time slot (a zone holds one group at a time).

    A group over 25 has one session per zone it takes. `adults` and `children` are the people
    in this session.
    `zone_id` is None while the session is "floating" (not yet given a zone).
    """

    id: str
    date: str
    start: int
    ticket_type: TicketType
    adults: int = 0
    children: int = 0
    zone_id: str | None = None
    pinned: bool = False
    status: SessionStatus = "open"

    @property
    def headcount(self) -> int:
        return self.adults + self.children

    @property
    def minutes(self) -> int:
        return TICKET_MINUTES[self.ticket_type]

    @property
    def is_fixed(self) -> bool:
        """True if the engine must not move this session to another zone.

        That is the case when ops pinned it, the day is locked, or it has started/finished.
        A session without a zone can't be fixed: there is nothing to keep.
        """
        return self.zone_id is not None and (self.pinned or self.status != "open")


@dataclass(frozen=True)
class Blackout:
    """A period on one date when nothing can be sold in any zone."""

    start: int
    end: int


@dataclass(frozen=True)
class DayState:
    """Everything the engine needs to know about one date."""

    date: str
    sessions: tuple[Session, ...] = ()
    closed: bool = False
    blackouts: tuple[Blackout, ...] = ()


# All known days, by date. A date that is missing is an ordinary open day with no sessions.
Schedule = Mapping[str, DayState]


def day_of(schedule: Schedule, date: str) -> DayState:
    """The state of `date`, or an empty open day if the schedule has nothing for it."""
    return schedule.get(date) or DayState(date=date)


@dataclass(frozen=True)
class Price:
    """Ticket price per person, in cents."""

    adult_cents: int
    child_cents: int


@dataclass(frozen=True)
class Settings:
    """Configurable settings (SPEC §2). The defaults are the spec defaults."""

    turnaround_minutes: int = 0
    small_group_warn_below: int = 0
    alternative_day_search: int = 7
    availability_calendar_days: int = 60
    prices: Mapping[TicketType, Price] | None = None
    search_budget_ms: int = 200


@dataclass(frozen=True)
class Enquiry:
    """A customer's request, as asked."""

    date: str
    start: int
    ticket_type: TicketType
    adults: int
    children: int = 0

    @property
    def headcount(self) -> int:
        return self.adults + self.children
