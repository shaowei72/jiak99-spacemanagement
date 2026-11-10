"""Evaluate one enquiry exactly as asked (SPEC §4.3).

`check_enquiry` (in enquiry.py) uses this for the request itself and for every alternative.

A zone slot holds one group at most: once a group is in a zone for a slot, that slot is sold
out, however many seats are left (SPEC §2). Groups over 25 are split evenly into sub-groups,
each with its own route.
"""

import math
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
    footprint,
    gaps_of,
    plan_dead_mask,
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

    25 -> (25,), 26 -> (13, 13), 40 -> (20, 20), 27 -> (14, 13). No people -> no sub-groups.
    """
    if people <= 0:
        return ()
    count = math.ceil(people / ZONE_CAPACITY)  # 25 people -> 1, 26 people -> 2
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


@dataclass(frozen=True)
class _Baseline:
    """A day's best plan before anything new is booked, and its dead slots (as a mask)."""

    plan: Assignment
    dead: int


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

    def evaluate(self, enquiry: Enquiry, *, explain: bool = True) -> EnquiryResult:
        """The verdict on `enquiry` exactly as asked, without alternatives.

        With `explain=False` a request that doesn't fit gets a short reason instead of a full
        explanation, which takes longer to work out. Alternatives use this: one that doesn't
        fit is never shown.
        """
        day = self.day(enquiry.date)
        problem = validity_problem(
            enquiry, day, self.settings, today=self.today, now_minute=self.now_minute
        )
        if problem is not None:
            return not_possible(problem)

        sizes = group_sizes(enquiry.headcount)
        new = new_floating_sessions(enquiry, sizes)
        all_sessions = day.sessions + new
        after = assign(all_sessions, self.settings, blackouts=day.blackouts, budget=self.budget)
        if not after.ok:
            if not explain:
                return not_possible("No room.")
            return not_possible(_no_room_reason(day, enquiry, sizes, self.settings))

        before = self._baseline(day)
        routes = tuple(after.route_of[s.id] for s in new)
        delta = after.dead_minutes - before.plan.dead_minutes

        drawbacks: list[str] = []
        if delta > 0:
            after_dead = plan_dead_mask(all_sessions, after.route_of, self.settings, day.blackouts)
            new_dead = after_dead & ~before.dead
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

    def _baseline(self, day: DayState) -> _Baseline:
        """The day's best plan before anything new is booked.

        Only asked for once the request fits, so the day without it fits too.
        """
        if day.date not in self._baselines:
            plan = assign(day.sessions, self.settings, blackouts=day.blackouts, budget=self.budget)
            dead = plan_dead_mask(day.sessions, plan.route_of, self.settings, day.blackouts)
            self._baselines[day.date] = _Baseline(plan, dead)
        return self._baselines[day.date]


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
    enquiry: Enquiry,
    sizes: Sequence[int],
    settings: Settings,
) -> str:
    """Explain why no plan works, in terms sales can repeat to the customer.

    Only what is true in every possible plan of the day counts: a group the app could still
    move elsewhere doesn't make a slot "in use". Goes from the plainest reason to the most
    general one.
    """
    ticket_type = enquiry.ticket_type
    start = enquiry.start
    end = start + TICKET_MINUTES[ticket_type]
    span = _span(start, end)

    # Zones every allowed zone set includes (A for Classic), and zones any of them includes.
    zone_sets = ZONE_SETS[ticket_type]
    every_set_needs = [zone for zone in ZONES if all(zone in zones for zones in zone_sets)]
    any_set_uses = [zone for zone in ZONES if any(zone in zones for zones in zone_sets)]

    window = 0
    for zone in any_set_uses:
        window |= slot_mask(zone, start, end)
    taken = _always_taken(day, settings, window)

    def free_mask(zone_id: str) -> int:
        """The zone's slots in the requested time that some plan keeps free."""
        return slot_mask(zone_id, start, end) & ~taken

    def free_slots(zone_id: str) -> int:
        """At most this many of the zone's slots in the requested time can be free at once."""
        all_slots = slot_mask(zone_id, start, end).bit_count()
        needed = _needed_slots(day, zone_id, start, end)
        return max(0, min(free_mask(zone_id).bit_count(), all_slots - needed))

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
            visit_slots = visit_minutes(ticket_type, zone) // GRID
            room = min(
                _room_for_visits(free_mask(zone), zone, start, end, visit_slots),
                free_slots(zone) // visit_slots,
            )
            if room < len(sizes):
                return (
                    f"{_describe_split(enquiry, sizes)} needs {_split_text(sizes)}, "
                    f"but Zone {zone} has room for only {plural(room, 'group')} in "
                    f"{format_time(start)}–{format_time(end)}. "
                    "Try booking part of the group at another time."
                )

    # 4. The zones are free here and there, but not in an order that works.
    return (
        f"No order of zones fits {format_time(start)}–{format_time(end)} around the other groups."
    )


def _always_taken(day: DayState, settings: Settings, window: int) -> int:
    """The slots in `window` that are in use in every valid plan of the day, as a mask.

    Blackouts and fixed groups always count. A movable group only counts where no plan can
    move it away: for each slot it uses in some plan, we ask for a plan that keeps that slot
    free (with `avoid`). If there is none, the slot is always in use.
    """
    taken = blackout_mask(day.blackouts)
    reset = reset_minutes(settings)
    for session in day.sessions:
        if session.is_fixed:
            assert session.route is not None  # is_fixed guarantees this
            place = footprint(session.start, session.ticket_type, session.route, reset)
            taken |= place.visits | place.resets

    plan = assign(day.sessions, settings, blackouts=day.blackouts, optimise=False)
    if not plan.ok:
        return taken  # the day doesn't fit as it is, so only the certain slots count
    visits, resets = plan_masks(day.sessions, plan.route_of, settings)
    maybe_taken = (visits | resets) & window & ~taken

    for bit_number in range(len(ZONES) * SLOTS_PER_DAY):
        slot = 1 << bit_number
        if maybe_taken & slot == 0:
            continue
        keeps_it_free = assign(
            day.sessions, settings, blackouts=day.blackouts, optimise=False, avoid=slot
        )
        if not keeps_it_free.ok:
            taken |= slot
    return taken


def _room_for_visits(free: int, zone_id: str, start: int, end: int, visit_slots: int) -> int:
    """How many visits of `visit_slots` slots in a row fit into the free slots of one zone.

    Free, taken, free, free with 2-slot visits -> room for 1.
    """
    room = 0
    run = 0  # free slots in a row so far
    for time in range(start, end, GRID):
        if free & slot_mask(zone_id, time, time + GRID):
            run += 1
        else:
            room += run // visit_slots
            run = 0
    return room + run // visit_slots


def _needed_slots(day: DayState, zone_id: str, start: int, end: int) -> int:
    """How many of a zone's slots between `start` and `end` the day's groups use in any plan.

    Counts the groups that are at the venue only within that time and must visit the zone
    (every route of their ticket goes through it): each needs its visit there, whatever order
    the app picks.
    """
    needed = 0
    for session in day.sessions:
        inside = start <= session.start and session.start + session.minutes <= end
        if inside and all(zone_id in zones for zones in ZONE_SETS[session.ticket_type]):
            needed += visit_minutes(session.ticket_type, zone_id) // GRID
    return needed
