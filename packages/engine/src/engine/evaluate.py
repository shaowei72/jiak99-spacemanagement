"""Evaluate one enquiry exactly as asked (SPEC §4.3).

`check_enquiry` (in enquiry.py) uses this for the request itself and for every alternative.

A booking always gets zones of its own: once a group takes a zone, that zone is sold out for its
time slot, however many seats are left (SPEC §2). Groups over 25 take several zones.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from engine.assign import Assignment, assign
from engine.budget import SearchBudget
from engine.dates import add_days, days_between, is_valid_date
from engine.model import DayState, Enquiry, Schedule, Session, Settings, day_of
from engine.rules import (
    BOOKING_WINDOW_DAYS,
    CLOSE,
    START_TIMES,
    TICKET_MINUTES,
    ZONE_CAPACITY,
    ZONES,
    TicketType,
)
from engine.text import (
    duration_label,
    format_time,
    join_words,
    plural,
    slot_label,
    ticket_label,
)
from engine.timeline import Gap, dead_gaps, occupancy_end, session_block

Verdict = Literal["ACCEPT_GOOD", "ACCEPT_COSTLY", "NOT_POSSIBLE"]


# --- result types ----------------------------------------------------------------------------


@dataclass(frozen=True)
class NewPlacement:
    """The group gets one zone of its own (which zone is chosen later)."""

    people: int
    kind: Literal["new"] = "new"


@dataclass(frozen=True)
class SplitPlacement:
    """A group over 25 gets several zones of its own, e.g. 40 people -> (25, 15)."""

    new_sessions: tuple[int, ...]  # people in each zone
    kind: Literal["split"] = "split"


Placement = NewPlacement | SplitPlacement


@dataclass(frozen=True)
class Alternative:
    """A better option to pitch to the customer (SPEC §4.4)."""

    date: str
    start: int
    ticket_type: TicketType
    verdict: Verdict
    change_summary: str
    gain_summary: str


@dataclass(frozen=True)
class EnquiryResult:
    verdict: Verdict
    placement: Placement | None
    dead_minutes_delta: int  # dead minutes after booking minus before
    zone_minutes_added: int  # zones taken × ticket length
    reason: str  # one line of plain English for sales
    alternatives: tuple[Alternative, ...] = ()

    @property
    def new_session_count(self) -> int:
        """Zones this booking would take (0 if it isn't possible)."""
        if isinstance(self.placement, NewPlacement):
            return 1
        if isinstance(self.placement, SplitPlacement):
            return len(self.placement.new_sessions)
        return 0


def not_possible(reason: str) -> EnquiryResult:
    return EnquiryResult("NOT_POSSIBLE", None, 0, 0, reason)


# --- rule 1: validity ------------------------------------------------------------------------


def validity_problem(
    enquiry: Enquiry,
    day: DayState,
    settings: Settings,
    *,
    today: str,
    now_minute: int | None = None,
) -> str | None:
    """Why the request can never be sold as asked, or None if it is a valid request."""
    if enquiry.adults < 0 or enquiry.children < 0 or enquiry.headcount < 1:
        return "A booking needs at least 1 person."
    if enquiry.ticket_type not in TICKET_MINUTES:
        return f"Unknown ticket type {enquiry.ticket_type!r}."
    if enquiry.start not in START_TIMES:
        return (
            f"Start times are every 30 min from {format_time(START_TIMES[0])} "
            f"to {format_time(START_TIMES[-1])}."
        )
    minutes = TICKET_MINUTES[enquiry.ticket_type]
    if enquiry.start + minutes > CLOSE:
        return (
            f"A {duration_label(enquiry.ticket_type)} {ticket_label(enquiry.ticket_type)} session "
            f"from {format_time(enquiry.start)} would end after the {format_time(CLOSE)} close."
        )
    if not is_valid_date(enquiry.date):
        return f"{enquiry.date!r} is not a valid date."
    days_ahead = days_between(today, enquiry.date)
    if days_ahead < 0:
        return "That date is in the past."
    if days_ahead > BOOKING_WINDOW_DAYS:
        last_day = add_days(today, BOOKING_WINDOW_DAYS)
        return f"{enquiry.date} is outside the 6-month booking window (last day {last_day})."
    if days_ahead == 0 and now_minute is not None and enquiry.start < now_minute:
        return "That start time has already passed today."
    if day.closed:
        return f"The venue is closed on {enquiry.date}."
    end = occupancy_end(enquiry.start, minutes, settings)
    for blackout in day.blackouts:
        if enquiry.start < blackout.end and blackout.start < end:
            return (
                f"Overlaps the {format_time(blackout.start)}–{format_time(blackout.end)} "
                f"blackout on {enquiry.date}."
            )
    return None


# --- how many zones a group takes ------------------------------------------------------------


def zone_sizes(people: int) -> tuple[int, ...]:
    """People per zone for one group: full zones of 25, then the rest (40 -> (25, 15))."""
    sizes: list[int] = []
    remaining = people
    while remaining > 0:
        take = min(ZONE_CAPACITY, remaining)
        sizes.append(take)
        remaining -= take
    return tuple(sizes)


def new_floating_sessions(enquiry: Enquiry, sizes: Sequence[int]) -> tuple[Session, ...]:
    """Placeholder sessions for the new sessions a booking would open."""
    return tuple(
        Session(
            id=f"(new {number})",
            date=enquiry.date,
            start=enquiry.start,
            ticket_type=enquiry.ticket_type,
            adults=people,
        )
        for number, people in enumerate(sizes, start=1)
    )


# --- the evaluator ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Baseline:
    """A day's best zone plan before anything new is booked."""

    plan: Assignment
    dead_gaps: tuple[Gap, ...]


class Evaluator:
    """Evaluates enquiries against a schedule.

    One enquiry check evaluates the same day many times (once per alternative), so each day's
    plan before booking is worked out once and remembered.
    """

    def __init__(
        self,
        schedule: Schedule,
        settings: Settings,
        *,
        today: str,
        now_minute: int | None = None,
        budget: SearchBudget | None = None,
    ) -> None:
        self.schedule = schedule
        self.settings = settings
        self.today = today
        self.now_minute = now_minute
        self.budget = budget
        self._baselines: dict[str, _Baseline] = {}

    def day(self, date: str) -> DayState:
        return day_of(self.schedule, date)

    def evaluate(self, enquiry: Enquiry) -> EnquiryResult:
        """The verdict on `enquiry` exactly as asked, without alternatives."""
        day = self.day(enquiry.date)
        problem = validity_problem(
            enquiry, day, self.settings, today=self.today, now_minute=self.now_minute
        )
        if problem is not None:
            return not_possible(problem)

        sizes = zone_sizes(enquiry.headcount)
        new = new_floating_sessions(enquiry, sizes)
        all_sessions = day.sessions + new
        after = assign(all_sessions, self.settings, blackouts=day.blackouts, budget=self.budget)
        if not after.ok:
            return not_possible(_no_room_reason(day, enquiry, len(new), self.settings))

        before = self._baseline(day)
        delta = after.dead_minutes - before.plan.dead_minutes
        after_gaps = dead_gaps(all_sessions, after.zone_of, self.settings, day.blackouts)

        drawbacks: list[str] = []
        if delta > 0:
            drawbacks.append(_describe_new_dead_gaps(before.dead_gaps, after_gaps))
        warn_below = self.settings.small_group_warn_below
        small = [people for people in sizes if warn_below > 0 and people < warn_below]
        if small:
            drawbacks.append(
                f"opens a whole zone for only {plural(min(small), 'person', 'people')}"
            )

        if drawbacks:
            verdict: Verdict = "ACCEPT_COSTLY"
            fit = f"Fits, but {join_words(drawbacks)}."
        else:
            verdict = "ACCEPT_GOOD"
            fit = "Fits in a free zone with no unsellable gaps."

        placement: Placement
        if len(sizes) == 1:
            placement = NewPlacement(sizes[0])
            reason = fit
        else:
            placement = SplitPlacement(sizes)
            reason = f"{_describe_split(enquiry, sizes)} {fit}"

        minutes_added = len(new) * TICKET_MINUTES[enquiry.ticket_type]
        return EnquiryResult(verdict, placement, delta, minutes_added, reason)

    def _baseline(self, day: DayState) -> _Baseline:
        if day.date not in self._baselines:
            plan = assign(day.sessions, self.settings, blackouts=day.blackouts, budget=self.budget)
            gaps = (
                dead_gaps(day.sessions, plan.zone_of, self.settings, day.blackouts)
                if plan.ok
                else []
            )
            self._baselines[day.date] = _Baseline(plan, tuple(gaps))
        return self._baselines[day.date]


# --- reason texts ----------------------------------------------------------------------------


def _describe_new_dead_gaps(before: Sequence[Gap], after: Sequence[Gap]) -> str:
    """The dead gaps that exist after booking but not before.

    Only called when dead minutes went up, so there is always at least one new gap.
    """
    new_spans = Counter((g.start, g.end) for g in after) - Counter((g.start, g.end) for g in before)
    parts = [
        f"{format_time(start)}–{format_time(end)} in {'one zone' if count == 1 else f'{count} zones'}"
        for (start, end), count in sorted(new_spans.items())
    ]
    return f"leaves {join_words(parts)} unsellable"


def _describe_split(enquiry: Enquiry, sizes: Sequence[int]) -> str:
    split = " + ".join(str(people) for people in sizes)
    slot = slot_label(enquiry.start, enquiry.ticket_type)
    return f"Group of {enquiry.headcount} at {slot} needs {len(sizes)} zones ({split})."


def _no_room_reason(day: DayState, enquiry: Enquiry, zones_needed: int, settings: Settings) -> str:
    """Explain why no zone plan works, in terms sales can repeat to the customer."""
    end = occupancy_end(enquiry.start, TICKET_MINUTES[enquiry.ticket_type], settings)
    blocks = [session_block(s, settings) for s in day.sessions]
    # The busiest moment within the request always starts at the request or at a session start.
    moments = sorted({enquiry.start} | {b.start for b in blocks if enquiry.start < b.start < end})
    for moment in moments:
        busy = sum(1 for b in blocks if b.start <= moment < b.end)
        free = len(ZONES) - busy
        if free < zones_needed:
            if free <= 0:
                return f"All {len(ZONES)} zones are in use at {format_time(moment)}."
            return (
                f"Only {plural(free, 'zone')} free at {format_time(moment)}; "
                f"this group needs {zones_needed}."
            )
    return (
        f"No zone is free for {format_time(enquiry.start)}–{format_time(end)} "
        "without moving pinned or locked sessions."
    )
