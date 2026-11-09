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
    """One group moving through its zones (its route), starting at `start`.

    A group over 25 is split into sub-groups, one session each. `adults` and `children` are the
    people in this session.
    `route` is the zone letters in visiting order, e.g. 'ABDC'. It is None while the session is
    "floating" (the app hasn't fixed an order yet).
    """

    id: str
    date: str
    start: int
    ticket_type: TicketType
    adults: int = 0
    children: int = 0
    route: str | None = None
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
        """True if the engine must not give this session another route.

        That is the case when ops pinned it, the day is locked, or it has started/finished.
        A session without a route can't be fixed: there is nothing to keep.
        """
        return self.route is not None and (self.pinned or self.status != "open")


@dataclass(frozen=True)
class Blackout:
    """A period on one date when nothing can be sold in any zone.

    Any 30-min zone slot it touches is blocked (a blackout from 12:15 blocks 12:00–12:30).
    """

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
