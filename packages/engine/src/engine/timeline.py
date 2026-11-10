"""How groups occupy zone slots, and which free slots are "dead" (SPEC §3 and §4.2).

The day of each zone is cut into 24 slots of 30 minutes (09:00–21:00). A group's route puts it
in one zone after another, so it takes one or two slots in each zone it visits.

To keep the search fast, a set of zone slots is stored as one whole number used as a row of
on/off bits ("a mask"): bit number `zone_index * 24 + slot_index` is 1 if that slot is in the
set. Combining sets is then a single operation: `a | b` (union), `a & b` (overlap), `a & ~b`
(remove). For example the slot A 09:00 is bit 0, A 09:30 is bit 1, B 09:00 is bit 24.

Three kinds of things occupy a zone slot:

* visits:    a group is in the zone;
* resets:    the zone is being reset after a visit (turnaround), so no other group may enter;
* blackouts: nothing can be sold (per-date overrides).

A free slot is sellable if one more group of any ticket except Tingkat could still use it;
otherwise it is dead (SPEC §4.2).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cache, lru_cache
from typing import NamedTuple

from engine.model import Blackout, Session, Settings
from engine.rules import (
    CLOSE,
    GRID,
    OPEN,
    ROUTES,
    SLOTS_PER_DAY,
    TICKET_MINUTES,
    TICKET_TYPES,
    ZONES,
    TicketType,
    visit_minutes,
)

ALL_SLOTS = (1 << (len(ZONES) * SLOTS_PER_DAY)) - 1  # every zone slot of the day

# The longest route covers 6 slots (3 h). A slot can only be used by a route starting at most
# this many slots earlier, which keeps the dead-slot check local.
LONGEST_ROUTE_SLOTS = max(TICKET_MINUTES.values()) // GRID


@dataclass(frozen=True)
class Visit:
    """One group in one zone from `start` up to (not including) `end`."""

    zone_id: str
    start: int
    end: int


@dataclass(frozen=True)
class Gap:
    """A run of dead (unsellable) slots in one zone."""

    zone_id: str
    start: int
    end: int

    @property
    def minutes(self) -> int:
        return self.end - self.start


class Footprint(NamedTuple):
    """The zone slots a route takes: the visits, and the reset time after each visit."""

    visits: int
    resets: int


# --- time and slots -----------------------------------------------------------------------------


def round_up_to_grid(minute: int) -> int:
    """The first slot boundary at or after `minute` (10:45 -> 11:00)."""
    steps = (minute - OPEN + GRID - 1) // GRID
    return OPEN + steps * GRID


def slot_index(minute: int) -> int:
    """09:00 -> 0, 09:30 -> 1, ... 20:30 -> 23."""
    return (minute - OPEN) // GRID


def slot_mask(zone_id: str, start: int, end: int) -> int:
    """All slots of one zone that the period `start`–`end` touches, as a mask."""
    first = max(0, slot_index(start))
    last = min(SLOTS_PER_DAY, slot_index(round_up_to_grid(end)))
    if last <= first:
        return 0
    offset = ZONES.index(zone_id) * SLOTS_PER_DAY
    return ((1 << (last - first)) - 1) << (offset + first)


@cache
def window_mask(first_slot: int, last_slot: int) -> int:
    """Slots `first_slot` up to (not including) `last_slot`, in every zone."""
    first = max(0, first_slot)
    last = min(SLOTS_PER_DAY, last_slot)
    if last <= first:
        return 0
    one_zone = ((1 << (last - first)) - 1) << first
    mask = 0
    for zone_index in range(len(ZONES)):
        mask |= one_zone << (zone_index * SLOTS_PER_DAY)
    return mask


def cells_of(mask: int) -> list[tuple[str, int]]:
    """The (zone, slot start time) of every slot in a mask, zone by zone."""
    found = []
    for bit in range(len(ZONES) * SLOTS_PER_DAY):
        if mask >> bit & 1:
            zone_index, slot = divmod(bit, SLOTS_PER_DAY)
            found.append((ZONES[zone_index], OPEN + slot * GRID))
    return found


# --- routes ---------------------------------------------------------------------------------------


def route_visits(start: int, ticket_type: TicketType, route: str) -> tuple[Visit, ...]:
    """The visits of a group starting at `start`, one per zone letter, back to back."""
    visits = []
    time = start
    for zone_id in route:
        end = time + visit_minutes(ticket_type, zone_id)
        visits.append(Visit(zone_id, time, end))
        time = end
    return tuple(visits)


def reset_minutes(settings: Settings) -> int:
    """Turnaround, as used by the engine: 0, or rounded up to whole 30-min slots."""
    return round_up_to_grid(OPEN + max(0, settings.turnaround_minutes)) - OPEN


@cache
def footprint(start: int, ticket_type: TicketType, route: str, reset: int) -> Footprint:
    """The slots a route takes, with `reset` minutes of turnaround after each visit."""
    visits = 0
    resets = 0
    for visit in route_visits(start, ticket_type, route):
        visits |= slot_mask(visit.zone_id, visit.start, visit.end)
        if reset > 0:
            resets |= slot_mask(visit.zone_id, visit.end, min(CLOSE, visit.end + reset))
    return Footprint(visits, resets)


def fits(place: Footprint, visits: int, taken: int) -> bool:
    """True if a route can be added.

    `taken` = everything occupied (visits, resets, blackouts). The new visits need free slots;
    the new reset time must not run into another group's visit (it may run into a blackout).
    """
    return place.visits & taken == 0 and place.resets & visits == 0


@cache
def sellable_routes(start_slot: int, reset: int) -> tuple[Footprint, ...]:
    """Footprints of every non-Tingkat route starting at this slot and ending by closing time."""
    if not 0 <= start_slot < SLOTS_PER_DAY:
        return ()
    start = OPEN + start_slot * GRID
    found = []
    for ticket_type in TICKET_TYPES:
        if ticket_type == "TINGKAT" or start + TICKET_MINUTES[ticket_type] > CLOSE:
            continue
        for route in ROUTES[ticket_type]:
            found.append(footprint(start, ticket_type, route, reset))
    return tuple(found)


# --- dead slots ---------------------------------------------------------------------------------


def dead_mask(
    visits: int, resets: int, blocked: int, reset: int, first_slot: int, last_slot: int
) -> int:
    """Dead slots between `first_slot` and `last_slot` (exclusive), in every zone.

    A free slot is sellable if some non-Tingkat route that fits would use it. Only routes
    starting up to LONGEST_ROUTE_SLOTS - 1 slots before the window can reach into it, and
    whether they fit depends only on slots near the window. So we keep just those slots and
    remember the answer: the search asks the same question many times.
    """
    nearby = window_mask(
        first_slot - LONGEST_ROUTE_SLOTS + 1, last_slot + LONGEST_ROUTE_SLOTS + reset // GRID
    )
    return _dead_mask_nearby(
        visits & nearby, resets & nearby, blocked & nearby, reset, first_slot, last_slot
    )


@lru_cache(maxsize=200_000)
def _dead_mask_nearby(
    visits: int, resets: int, blocked: int, reset: int, first_slot: int, last_slot: int
) -> int:
    taken = visits | resets | blocked
    unsold = window_mask(first_slot, last_slot) & ~taken
    if unsold == 0:
        return 0
    for start_slot in range(first_slot - LONGEST_ROUTE_SLOTS + 1, last_slot):
        for route_visits_mask, route_resets_mask in sellable_routes(start_slot, reset):
            # The same test as `fits`, written out here because this loop runs very often.
            if (
                route_visits_mask & unsold
                and route_visits_mask & taken == 0
                and route_resets_mask & visits == 0
            ):
                unsold &= ~route_visits_mask
                if unsold == 0:
                    return 0
    return unsold


def blackout_mask(blackouts: Iterable[Blackout]) -> int:
    """All zone slots touched by the blackouts."""
    mask = 0
    for blackout in blackouts:
        for zone_id in ZONES:
            mask |= slot_mask(zone_id, max(OPEN, blackout.start), min(CLOSE, blackout.end))
    return mask


def plan_masks(
    sessions: Iterable[Session], route_of: Mapping[str, str], settings: Settings
) -> tuple[int, int]:
    """(visits, resets) of a plan, as masks. Assumes the plan is valid."""
    reset = reset_minutes(settings)
    visits = 0
    resets = 0
    for session in sessions:
        place = footprint(session.start, session.ticket_type, route_of[session.id], reset)
        visits |= place.visits
        resets |= place.resets
    return visits, resets


def gaps_of(mask: int) -> list[Gap]:
    """Turn a mask of dead slots into runs per zone, e.g. A 09:00–10:00."""
    gaps: list[Gap] = []
    for zone_id, start in cells_of(mask):
        last = gaps[-1] if gaps else None
        if last is not None and last.zone_id == zone_id and last.end == start:
            gaps[-1] = Gap(zone_id, last.start, start + GRID)
        else:
            gaps.append(Gap(zone_id, start, start + GRID))
    return gaps


def plan_dead_mask(
    sessions: Iterable[Session],
    route_of: Mapping[str, str],
    settings: Settings,
    blackouts: Iterable[Blackout] = (),
) -> int:
    """Dead slots over all zones for a given plan (session id -> route), as a mask."""
    visits, resets = plan_masks(sessions, route_of, settings)
    blocked = blackout_mask(blackouts)
    return dead_mask(visits, resets, blocked, reset_minutes(settings), 0, SLOTS_PER_DAY)


def dead_gaps(
    sessions: Iterable[Session],
    route_of: Mapping[str, str],
    settings: Settings,
    blackouts: Iterable[Blackout] = (),
) -> list[Gap]:
    """Dead slots over all zones for a given plan (session id -> route), as runs per zone."""
    return gaps_of(plan_dead_mask(sessions, route_of, settings, blackouts))
