"""How sessions occupy a zone, and which free gaps are "dead" (SPEC §3 and §4.2).

A zone's day runs from OPEN to CLOSE. Things that occupy it are called blocks: sessions
(including their turnaround) and blackouts. The free time between blocks is a gap. A gap is
dead (unsellable) if not even the shortest ticket fits into it.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import NamedTuple

from engine.model import Blackout, Session, Settings
from engine.rules import CLOSE, GRID, OPEN, SHORTEST_TICKET_MINUTES, ZONES


class Block(NamedTuple):
    """Something occupying a zone from `start` up to (not including) `end`."""

    start: int
    end: int
    is_session: bool  # False for a blackout


@dataclass(frozen=True)
class Gap:
    """A dead (unsellable) gap in one zone."""

    zone_id: str
    start: int
    end: int

    @property
    def minutes(self) -> int:
        return self.end - self.start


def occupancy_end(start: int, minutes: int, settings: Settings) -> int:
    """When a zone is free again: session end plus turnaround, but never after closing."""
    return min(start + minutes + settings.turnaround_minutes, CLOSE)


def session_block(session: Session, settings: Settings) -> Block:
    return Block(session.start, occupancy_end(session.start, session.minutes, settings), True)


def blackout_block(blackout: Blackout) -> Block:
    return Block(blackout.start, blackout.end, False)


def round_up_to_grid(minute: int) -> int:
    """The first start time on the 30-min grid at or after `minute` (10:45 -> 11:00)."""
    steps = (minute - OPEN + GRID - 1) // GRID
    return OPEN + steps * GRID


def dead_minutes_in_gap(
    gap_start: int, gap_end: int, *, ends_at_session: bool, settings: Settings
) -> int:
    """Length of the gap if it is dead, else 0.

    `gap_start` must already be on the grid. A gap is sellable if the shortest ticket fits,
    plus turnaround when another session follows (no turnaround is needed before closing
    time or a blackout).
    """
    length = gap_end - gap_start
    if length <= 0:
        return 0
    needed = SHORTEST_TICKET_MINUTES
    if ends_at_session:
        needed += settings.turnaround_minutes
    return 0 if length >= needed else length


def dead_gaps_in_zone(zone_id: str, blocks: Iterable[Block], settings: Settings) -> list[Gap]:
    """All dead gaps of one zone, from opening to closing time."""
    gaps: list[Gap] = []
    free_from = OPEN
    for block in sorted(blocks):
        if dead_minutes_in_gap(
            free_from, block.start, ends_at_session=block.is_session, settings=settings
        ):
            gaps.append(Gap(zone_id, free_from, block.start))
        # Rounding up to the grid is structural turnaround, never counted as dead.
        free_from = max(free_from, round_up_to_grid(block.end))
    if dead_minutes_in_gap(free_from, CLOSE, ends_at_session=False, settings=settings):
        gaps.append(Gap(zone_id, free_from, CLOSE))
    return gaps


def dead_gaps(
    sessions: Iterable[Session],
    zone_of: Mapping[str, str],
    settings: Settings,
    blackouts: Iterable[Blackout] = (),
) -> list[Gap]:
    """Dead gaps over all zones for a given assignment (session id -> zone)."""
    blocks: dict[str, list[Block]] = {
        zone: [blackout_block(b) for b in blackouts] for zone in ZONES
    }
    for session in sessions:
        blocks[zone_of[session.id]].append(session_block(session, settings))
    gaps: list[Gap] = []
    for zone in ZONES:
        gaps.extend(dead_gaps_in_zone(zone, blocks[zone], settings))
    return gaps
