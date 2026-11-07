"""Zone assignment (SPEC §4.1).

Give every session a zone so that no two sessions in a zone overlap, keep fixed sessions where
they are, and among all valid plans pick one with the fewest dead minutes.

How the search works
--------------------
Floating sessions are placed one at a time, in order of start time. For each one we try every
zone it fits in. Because we go in start-time order, once a session is placed in a zone, nothing
placed later can go before it in that zone. So for the rest of the search, a zone is fully
described by two things:

* `free_from`: when it is free again (rounded up to the grid), and
* which of its fixed blocks (pinned sessions, blackouts) are still ahead.

Two zones with the same description are interchangeable, so we only try one of them (this is
the "symmetry breaking" from the spec). And whenever we meet the same set of zone descriptions
again at the same step, the best answer from there is already known, so we look it up instead of
searching again (memoisation). Together this makes the search exact and fast.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise

from engine.budget import SearchBudget
from engine.model import Blackout, Session, Settings
from engine.rules import CLOSE, OPEN, ZONES
from engine.timeline import (
    Block,
    blackout_block,
    dead_minutes_in_gap,
    occupancy_end,
    round_up_to_grid,
    session_block,
)


@dataclass(frozen=True)
class Assignment:
    """Result of `assign`.

    ok:           False if the sessions can't all fit, whatever we do.
    zone_of:      session id -> zone id, for every session (fixed ones included).
    dead_minutes: total unsellable minutes over all zones under this plan.
    optimal:      False if the time budget ran out before the best plan was proven.
    """

    ok: bool
    zone_of: Mapping[str, str] = field(default_factory=dict)
    dead_minutes: int = 0
    optimal: bool = True


INFEASIBLE = Assignment(ok=False)


def assign(
    sessions: Iterable[Session],
    settings: Settings,
    *,
    blackouts: Iterable[Blackout] = (),
    budget: SearchBudget | None = None,
    optimise: bool = True,
) -> Assignment:
    """Find zones for all sessions of one day.

    With `optimise=False` the first valid plan is returned (enough to answer "does it fit?").
    """
    blackout_blocks = [blackout_block(b) for b in blackouts]
    fixed_blocks: dict[str, list[Block]] = {zone: list(blackout_blocks) for zone in ZONES}
    zone_of: dict[str, str] = {}
    floating: list[Session] = []

    for session in sessions:
        if session.is_fixed:
            assert session.zone_id is not None  # is_fixed guarantees this
            if session.zone_id not in fixed_blocks:
                return INFEASIBLE  # pinned to a zone that doesn't exist
            fixed_blocks[session.zone_id].append(session_block(session, settings))
            zone_of[session.id] = session.zone_id
        else:
            floating.append(session)

    if any(_fixed_sessions_overlap(blocks) for blocks in fixed_blocks.values()):
        return INFEASIBLE

    search = _Search(floating, fixed_blocks, settings, budget, optimise)
    result = search.run()
    if result is None:
        return INFEASIBLE
    dead_minutes, floating_zones = result
    zone_of.update(floating_zones)
    return Assignment(
        ok=True, zone_of=zone_of, dead_minutes=dead_minutes, optimal=not search.cut_short
    )


def _fixed_sessions_overlap(blocks: Sequence[Block]) -> bool:
    session_blocks = sorted(b for b in blocks if b.is_session)
    return any(a.end > b.start for a, b in pairwise(session_blocks))


# A zone during the search: (free_from, index of its next fixed block not yet reached).
_ZoneState = tuple[int, int]
# All zones, in ZONES order.
_State = tuple[_ZoneState, ...]
# What makes a zone different from another one: free_from and the fixed blocks still ahead.
_ZoneKey = tuple[int, tuple[Block, ...]]


class _Search:
    def __init__(
        self,
        floating: list[Session],
        fixed_blocks: dict[str, list[Block]],
        settings: Settings,
        budget: SearchBudget | None,
        optimise: bool,
    ) -> None:
        # Longer sessions first among equal starts: they are the hardest to fit.
        self.floating = sorted(floating, key=lambda s: (s.start, -s.minutes, s.id))
        self.zones = tuple(fixed_blocks)
        self.blocks = tuple(tuple(sorted(fixed_blocks[z])) for z in self.zones)
        # remaining[z][i] = the fixed blocks of zone z from index i on (precomputed once).
        self.remaining = tuple(
            tuple(blocks[i:] for i in range(len(blocks) + 1)) for blocks in self.blocks
        )
        self.settings = settings
        self.budget = budget
        self.optimise = optimise
        self.cut_short = False
        # (step, sorted zone keys) -> (best dead minutes from here or None, zone key chosen)
        self.memo: dict[tuple[int, tuple[_ZoneKey, ...]], tuple[int | None, _ZoneKey | None]] = {}

    def run(self) -> tuple[int, dict[str, str]] | None:
        start: _State = tuple((OPEN, 0) for _ in self.zones)
        cost = self._best(0, start)
        if cost is None:
            return None
        return cost, self._read_plan(start)

    # --- the search ---------------------------------------------------------------------------

    def _best(self, step: int, state: _State) -> int | None:
        """Fewest dead minutes for placing floating[step:] onwards, or None if impossible."""
        memo_key = (step, self._key(state))
        if memo_key in self.memo:
            return self.memo[memo_key][0]

        if step == len(self.floating):
            result: int | None = self._finish_cost(state)
            self.memo[memo_key] = (result, None)
            return result

        session = self.floating[step]
        reached, reach_cost = self._advance(state, session.start)
        best_cost: int | None = None
        best_choice: _ZoneKey | None = None
        tried: set[_ZoneKey] = set()

        for zone in self._zones_to_try(reached, session):
            choice = self._zone_key(zone, reached[zone])
            if choice in tried:
                continue  # an identical zone was already tried
            tried.add(choice)
            rest = self._best(step + 1, self._place(reached, zone, session))
            if rest is None:
                continue
            gap_cost = dead_minutes_in_gap(
                reached[zone][0], session.start, ends_at_session=True, settings=self.settings
            )
            if best_cost is None or gap_cost + rest < best_cost:
                best_cost, best_choice = gap_cost + rest, choice
            if not self.optimise:
                break
            if self.budget is not None and self.budget.expired():
                self.cut_short = True
                break

        result = None if best_cost is None else reach_cost + best_cost
        self.memo[memo_key] = (result, best_choice)
        return result

    def _zones_to_try(self, state: _State, session: Session) -> list[int]:
        """Zones the session fits in, tightest fit first (smallest gap before it)."""
        fitting = [z for z in range(len(self.zones)) if self._fits(state, z, session)]
        return sorted(fitting, key=lambda z: (-state[z][0], z))

    def _fits(self, state: _State, zone: int, session: Session) -> bool:
        free_from, next_block = state[zone]
        if free_from > session.start:
            return False
        blocks = self.blocks[zone]
        if next_block < len(blocks):
            end = occupancy_end(session.start, session.minutes, self.settings)
            return end <= blocks[next_block].start
        return True

    def _place(self, state: _State, zone: int, session: Session) -> _State:
        end = occupancy_end(session.start, session.minutes, self.settings)
        new_zone: _ZoneState = (round_up_to_grid(end), state[zone][1])
        return (*state[:zone], new_zone, *state[zone + 1 :])

    def _advance(self, state: _State, time: int) -> tuple[_State, int]:
        """Move all zones forward to `time`.

        Fixed blocks starting at or before `time` become part of each zone's past. The gaps in
        front of them can no longer change, so their dead minutes are counted now.
        """
        cost = 0
        new_state: list[_ZoneState] = []
        for zone, (free_from, next_block) in enumerate(state):
            blocks = self.blocks[zone]
            while next_block < len(blocks) and blocks[next_block].start <= time:
                block = blocks[next_block]
                cost += dead_minutes_in_gap(
                    free_from, block.start, ends_at_session=block.is_session, settings=self.settings
                )
                free_from = max(free_from, round_up_to_grid(block.end))
                next_block += 1
            new_state.append((free_from, next_block))
        return tuple(new_state), cost

    def _finish_cost(self, state: _State) -> int:
        """Dead minutes left in every zone once all floating sessions are placed."""
        reached, cost = self._advance(state, CLOSE)
        for free_from, _ in reached:
            cost += dead_minutes_in_gap(
                free_from, CLOSE, ends_at_session=False, settings=self.settings
            )
        return cost

    # --- helpers ------------------------------------------------------------------------------

    def _zone_key(self, zone: int, zone_state: _ZoneState) -> _ZoneKey:
        free_from, next_block = zone_state
        return (free_from, self.remaining[zone][next_block])

    def _key(self, state: _State) -> tuple[_ZoneKey, ...]:
        return tuple(sorted(self._zone_key(z, zs) for z, zs in enumerate(state)))

    def _read_plan(self, state: _State) -> dict[str, str]:
        """Walk the remembered choices to get a zone for every floating session."""
        zone_of: dict[str, str] = {}
        for step, session in enumerate(self.floating):
            _, choice = self.memo[(step, self._key(state))]
            reached, _ = self._advance(state, session.start)
            zone = next(
                z for z in range(len(self.zones)) if self._zone_key(z, reached[z]) == choice
            )
            zone_of[session.id] = self.zones[zone]
            state = self._place(reached, zone, session)
        return zone_of
