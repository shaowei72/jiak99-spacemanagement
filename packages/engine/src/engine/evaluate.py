"""Evaluate one enquiry exactly as asked (SPEC §4.3).

`check_enquiry` (in enquiry.py) uses this for the request itself and for every alternative.

A zone slot holds one group at most: once a group is in a zone for a slot, that slot is sold
out, however many seats are left (SPEC §2). Groups over 25 are split evenly into sub-groups,
each with its own route.
"""

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
    GRID,
    SLOTS_PER_DAY,
    START_TIMES,
    TICKET_MINUTES,
    ZONE_CAPACITY,
    ZONE_SETS,
    ZONES,
    TicketType,
    visit_minutes,
)
from engine.text import (
    duration_label,
    format_time,
    join_words,
    plural,
    slot_label,
    ticket_label,
)
from engine.timeline import (
    Gap,
    blackout_mask,
    dead_mask,
    gaps_of,
    plan_masks,
    reset_minutes,
    slot_mask,
)

Verdict = Literal["ACCEPT_GOOD", "ACCEPT_COSTLY", "NOT_POSSIBLE"]


# --- result types ----------------------------------------------------------------------------


@dataclass(frozen=True)
class NewPlacement:
    """The group stays together, on `route` (e.g. 'ADBC'; may change until the day is locked)."""

    people: int
    route: str
    kind: Literal["new"] = "new"


@dataclass(frozen=True)
class SplitPlacement:
    """A group over 25 split into sub-groups, e.g. 40 people -> (20, 20), each on its own route."""

    new_sessions: tuple[int, ...]  # people in each sub-group
    routes: tuple[str, ...]  # the route of each sub-group, in the same order
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
    zone_minutes_added: int  # sub-groups × ticket length
    reason: str  # one line of plain English for sales
    alternatives: tuple[Alternative, ...] = ()

    @property
    def new_session_count(self) -> int:
        """Sub-groups this booking would make (0 if it isn't possible)."""
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
    end = enquiry.start + TICKET_MINUTES[enquiry.ticket_type]
    if end > CLOSE:
        return (
            f"A {duration_label(enquiry.ticket_type)} {ticket_label(enquiry.ticket_type)} "
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
    for blackout in day.blackouts:
        if enquiry.start < blackout.end and blackout.start < end:
            return (
                f"Overlaps the {format_time(blackout.start)}–{format_time(blackout.end)} "
                f"blackout on {enquiry.date}."
            )
    return None


# --- splitting big groups --------------------------------------------------------------------


def group_sizes(people: int) -> tuple[int, ...]:
    """People per sub-group: as few sub-groups as possible, sizes as equal as possible.

    25 -> (25,), 26 -> (13, 13), 40 -> (20, 20), 27 -> (14, 13).
    """
    count = -(-people // ZONE_CAPACITY)  # ceil(people / 25)
    base, extra = divmod(people, count)
    return tuple(base + 1 if number < extra else base for number in range(count))


def new_floating_sessions(enquiry: Enquiry, sizes: Sequence[int]) -> tuple[Session, ...]:
    """Placeholder sessions for the sub-groups a booking would make."""
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
        self._baselines: dict[str, Assignment] = {}

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

        before = self._baseline(day)
        sizes = group_sizes(enquiry.headcount)
        new = new_floating_sessions(enquiry, sizes)
        all_sessions = day.sessions + new
        after = assign(all_sessions, self.settings, blackouts=day.blackouts, budget=self.budget)
        if not after.ok:
            return not_possible(_no_room_reason(day, before, enquiry, sizes, self.settings))

        routes = tuple(after.route_of[s.id] for s in new)
        delta = after.dead_minutes - before.dead_minutes

        drawbacks: list[str] = []
        if delta > 0:
            new_dead = _dead_slots(day, all_sessions, after, self.settings) & ~_dead_slots(
                day, day.sessions, before, self.settings
            )
            drawbacks.append(f"leaves {_describe_slots(gaps_of(new_dead))} unsellable")
        warn_below = self.settings.small_group_warn_below
        small = [people for people in sizes if warn_below > 0 and people < warn_below]
        if small:
            drawbacks.append(f"takes whole zones for only {plural(min(small), 'person', 'people')}")

        route_text = f"route {routes[0]}" if len(routes) == 1 else f"routes {', '.join(routes)}"
        if drawbacks:
            verdict: Verdict = "ACCEPT_COSTLY"
            fit = f"Fits ({route_text}), but {join_words(drawbacks)}."
        else:
            verdict = "ACCEPT_GOOD"
            fit = f"Fits ({route_text}) with no unsellable slots."

        placement: Placement
        if len(sizes) == 1:
            placement = NewPlacement(sizes[0], routes[0])
            reason = fit
        else:
            placement = SplitPlacement(sizes, routes)
            reason = f"{_describe_split(enquiry, sizes)} is split into {_split_text(sizes)}. {fit}"

        minutes_added = len(new) * TICKET_MINUTES[enquiry.ticket_type]
        return EnquiryResult(verdict, placement, delta, minutes_added, reason)

    def _baseline(self, day: DayState) -> Assignment:
        """The day's best plan before anything new is booked."""
        if day.date not in self._baselines:
            self._baselines[day.date] = assign(
                day.sessions, self.settings, blackouts=day.blackouts, budget=self.budget
            )
        return self._baselines[day.date]


def _dead_slots(
    day: DayState, sessions: Sequence[Session], plan: Assignment, settings: Settings
) -> int:
    """The dead slots of a valid plan, as a mask."""
    visits, resets = plan_masks(sessions, plan.route_of, settings)
    blocked = blackout_mask(day.blackouts)
    return dead_mask(visits, resets, blocked, reset_minutes(settings), 0, SLOTS_PER_DAY)


# --- reason texts ----------------------------------------------------------------------------


def _describe_slots(gaps: Sequence[Gap]) -> str:
    """'B 20:00–20:30 and C 20:30–21:00'."""
    return join_words([f"{g.zone_id} {format_time(g.start)}–{format_time(g.end)}" for g in gaps])


def _describe_split(enquiry: Enquiry, sizes: Sequence[int]) -> str:
    return f"Group of {enquiry.headcount} at {slot_label(enquiry.start, enquiry.ticket_type)}"


def _split_text(sizes: Sequence[int]) -> str:
    """'2 groups (20 + 20)'."""
    return f"{plural(len(sizes), 'group')} ({' + '.join(str(people) for people in sizes)})"


def _span(start: int, end: int) -> str:
    """'at 10:00' for a single slot, else '10:00–11:00'."""
    if end - start == GRID:
        return f"at {format_time(start)}"
    return f"{format_time(start)}–{format_time(end)}"


def _zones_phrase(zones: Sequence[str]) -> str:
    """'Zone A' or 'Zones B, C and D'."""
    if len(zones) == 1:
        return f"Zone {zones[0]}"
    return f"Zones {join_words(list(zones))}"


def _no_room_reason(
    day: DayState,
    before: Assignment,
    enquiry: Enquiry,
    sizes: Sequence[int],
    settings: Settings,
) -> str:
    """Explain why no plan works, in terms sales can repeat to the customer.

    Looks at the day's current plan within the requested time, from the plainest reason to
    the most general one.
    """
    ticket_type = enquiry.ticket_type
    start = enquiry.start
    end = start + TICKET_MINUTES[ticket_type]
    span = _span(start, end)

    taken = blackout_mask(day.blackouts)
    if before.ok:
        visits, resets = plan_masks(day.sessions, before.route_of, settings)
        taken |= visits | resets

    def free_slots(zone_id: str) -> int:
        return (slot_mask(zone_id, start, end) & ~taken).bit_count()

    # Zones every allowed zone set includes (A for Classic), and zones any of them includes.
    zone_sets = ZONE_SETS[ticket_type]
    every_set_needs = [zone for zone in ZONES if all(zone in zones for zones in zone_sets)]
    any_set_uses = [zone for zone in ZONES if any(zone in zones for zones in zone_sets)]

    # 1. A zone every route needs is in use the whole time.
    full = [zone for zone in every_set_needs if free_slots(zone) == 0]
    if full:
        return f"{_zones_phrase(full)} {'is' if len(full) == 1 else 'are'} in use {span}."

    # 2. Every zone the ticket could use is in use the whole time.
    if all(free_slots(zone) == 0 for zone in any_set_uses):
        return f"{_zones_phrase(any_set_uses)} are in use {span}."

    # 3. A split group: a needed zone has room for fewer sub-groups than there are.
    if len(sizes) > 1:
        for zone in every_set_needs:
            room = free_slots(zone) // (visit_minutes(ticket_type, zone) // GRID)
            if room < len(sizes):
                return (
                    f"{_describe_split(enquiry, sizes)} needs {_split_text(sizes)}, "
                    f"but zone {zone} has room for only {plural(room, 'group')} in "
                    f"{format_time(start)}–{format_time(end)}. "
                    "Try booking part of the group at another time."
                )

    # 4. The zones are free here and there, but not in an order that works.
    return (
        f"No order of zones fits {format_time(start)}–{format_time(end)} around the other groups."
    )
