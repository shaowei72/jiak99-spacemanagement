"""Route assignment (SPEC §4.1).

Give every session a route (an order of zones) so that no zone slot holds two groups, keep
fixed sessions on their route, and among all valid plans pick one with the fewest dead minutes.

How the search works
--------------------
Floating sessions are placed one at a time, in order of start time. For each one we try every
route its ticket allows (up to 24) that still fits.

Because we go in start-time order, everything placed later starts at or after the current
session. So once the search has moved far enough past a slot, nothing can change whether that
slot is dead any more: we count it ("settle" it) and forget about it. A slot is settled once
the current start is more than a route's length (plus reset time) after it.

What is left to decide then depends only on the slots that aren't settled yet. Whenever the
search meets the same unsettled slots again at the same step, the best answer from there is
already known, so we look it up instead of searching again (memoisation). Together this makes
the search exact and fast: we find a plan whenever one exists, and the one we return has the
fewest dead minutes (unless the time budget runs out first).
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from engine.budget import SearchBudget
from engine.model import Blackout, Session, Settings
from engine.rules import CLOSE, GRID, OPEN, ROUTES, SLOTS_PER_DAY, TICKET_MINUTES, is_valid_route
from engine.timeline import (
    LONGEST_ROUTE_SLOTS,
    Footprint,
    blackout_mask,
    dead_mask,
    fits,
    footprint,
    reset_minutes,
    slot_index,
    window_mask,
)


@dataclass(frozen=True)
class Assignment:
    """Result of `assign`.

    ok:           False if the sessions can't all fit, whatever we do.
    route_of:     session id -> route, for every session (fixed ones included).
    dead_minutes: total unsellable minutes over all zones under this plan.
    optimal:      False if the time budget ran out before the best plan was proven.
    """

    ok: bool
    route_of: Mapping[str, str] = field(default_factory=dict)
    dead_minutes: int = 0
    optimal: bool = True


INFEASIBLE = Assignment(ok=False)


def is_schedulable(session: Session) -> bool:
    """True if a session's ticket and start make sense: known ticket, on the grid, ends by 21:00."""
    if session.ticket_type not in TICKET_MINUTES:
        return False
    if session.start < OPEN or (session.start - OPEN) % GRID != 0:
        return False
    return session.start + TICKET_MINUTES[session.ticket_type] <= CLOSE


def assign(
    sessions: Iterable[Session],
    settings: Settings,
    *,
    blackouts: Iterable[Blackout] = (),
    budget: SearchBudget | None = None,
    optimise: bool = True,
) -> Assignment:
    """Find routes for all sessions of one day.

    With `optimise=False` the first valid plan is returned (enough to answer "does it fit?").
    """
    reset = reset_minutes(settings)
    blocked = blackout_mask(blackouts)
    visits = 0
    resets = 0
    route_of: dict[str, str] = {}
    floating: list[Session] = []

    for session in sessions:
        if not is_schedulable(session):
            return INFEASIBLE
        if session.is_fixed:
            assert session.route is not None  # is_fixed guarantees this
            if not is_valid_route(session.ticket_type, session.route):
                return INFEASIBLE
            place = footprint(session.start, session.ticket_type, session.route, reset)
            # Fixed groups were checked against blackouts when they were booked, so only
            # clashes between groups make the day impossible.
            if not fits(place, visits, visits | resets):
                return INFEASIBLE
            visits |= place.visits
            resets |= place.resets
            route_of[session.id] = session.route
        else:
            floating.append(session)

    search = _Search(floating, visits, resets, blocked, reset, budget, optimise)
    result = search.run()
    if result is None:
        return INFEASIBLE
    dead_slots, floating_routes = result
    route_of.update(floating_routes)
    return Assignment(
        ok=True,
        route_of=route_of,
        dead_minutes=dead_slots * GRID,
        optimal=not search.cut_short,
    )


# The occupied slots during the search: (visits, resets).
_State = tuple[int, int]
# A remembered state: (step, visits, resets), with only the slots that still matter kept.
_Key = tuple[int, int, int]

NO_LIMIT = 10**9  # more dead slots than a day can have
NOT_EXACT = -1  # in best_memo: the value is only a lower bound, no route was chosen


class _Search:
    """The search for one day. Two questions, each with its own memory:

    * `_can_finish`: can the remaining sessions still be placed at all? Only slots from the
      current start time on matter for that, so many different pasts look the same here.
    * `_best`: the fewest dead slots still to be counted. It only ever tries routes after
      which `_can_finish` says yes, so it never wanders into dead ends. When the time budget
      runs out it takes the first such route at every step and is done at once.
    """

    def __init__(
        self,
        floating: list[Session],
        visits: int,
        resets: int,
        blocked: int,
        reset: int,
        budget: SearchBudget | None,
        optimise: bool,
    ) -> None:
        # Longer sessions first among equal starts: they are the hardest to fit.
        self.floating = sorted(
            floating, key=lambda s: (s.start, -TICKET_MINUTES[s.ticket_type], s.id)
        )
        self.options: list[list[tuple[str, Footprint]]] = [
            [
                (route, footprint(s.start, s.ticket_type, route, reset))
                for route in ROUTES[s.ticket_type]
            ]
            for s in self.floating
        ]
        self.start_state: _State = (visits, resets)
        self.blocked = blocked
        self.reset = reset
        self.budget = budget
        self.optimise = optimise
        self.cut_short = False

        # Slots that matter for "can the rest still fit?" at step k: from its start time on.
        self.future = [window_mask(slot_index(s.start), SLOTS_PER_DAY) for s in self.floating]
        # settled[k] = slots before this index are settled once session k is about to be placed.
        # A slot can be affected by anything up to a route's length plus the reset time after it.
        reach = LONGEST_ROUTE_SLOTS + reset // GRID
        self.settled = [max(0, slot_index(s.start) - reach + 1) for s in self.floating]
        self.settled.append(SLOTS_PER_DAY)  # after the last session, everything is settled
        # Slots that matter for counting dead slots from step k on.
        self.relevant = [
            window_mask(first - LONGEST_ROUTE_SLOTS + 1, SLOTS_PER_DAY) for first in self.settled
        ]

        # _can_finish memory: key -> index of the first route that works, or -1 if none does.
        self.finish_memo: dict[_Key, int] = {}
        # _best memory: key -> (fewest dead slots from here, index of the route that gives it),
        # or (a lower bound, NOT_EXACT) if the search only learnt "no better than that".
        self.best_memo: dict[_Key, tuple[int, int]] = {}

    def run(self) -> tuple[int, dict[str, str]] | None:
        """(dead slots, route per floating session), or None if nothing fits."""
        if not self._can_finish(0, self.start_state):
            return None
        if not self.optimise:
            route_of, (visits, resets) = self._read_plan(self._first_choice)
            dead = dead_mask(visits, resets, self.blocked, self.reset, 0, SLOTS_PER_DAY)
            return dead.bit_count(), route_of
        already_dead = self._dead_between(self.start_state, 0, self.settled[0])
        rest = self._best(0, self.start_state)
        route_of, _ = self._read_plan(self._best_choice)
        return already_dead + rest, route_of

    # --- can the rest still fit? ----------------------------------------------------------------

    def _can_finish(self, step: int, state: _State) -> bool:
        """True if floating[step:] can all be placed, starting from `state`."""
        if step == len(self.floating):
            return True
        key = _key(step, state, self.future[step])
        if key not in self.finish_memo:
            self.finish_memo[key] = -1
            visits, resets = state
            taken = visits | resets | self.blocked
            for choice, (_, place) in enumerate(self.options[step]):
                after = (visits | place.visits, resets | place.resets)
                if fits(place, visits, taken) and self._can_finish(step + 1, after):
                    self.finish_memo[key] = choice
                    break
        return self.finish_memo[key] >= 0

    # --- the fewest dead slots --------------------------------------------------------------

    def _best(self, step: int, state: _State, limit: int = NO_LIMIT) -> int:
        """Fewest dead slots still to be counted when placing floating[step:].

        Only plans with fewer than `limit` dead slots are of interest (the caller already has
        a plan that good). If there is none, the answer is just some number >= `limit`: a
        lower bound, not the exact minimum. Only called for states where `_can_finish` is
        true, so there is always some plan.
        """
        if step == len(self.floating):
            return 0
        key = _key(step, state, self.relevant[step])
        if key in self.best_memo:
            value, choice = self.best_memo[key]
            if choice >= 0 or value >= limit:
                return value  # exact, or a lower bound that already rules this state out
        if self.budget is not None and self.budget.expired():
            self.cut_short = True
            return self._finish_quickly(step, state)

        # Slots that are dead now and lie before this session's start stay dead: everything
        # placed from here on starts later. So we must pay at least this much.
        lowest_possible = self._dead_between(
            state, self.settled[step], slot_index(self.floating[step].start)
        )
        if lowest_possible >= limit:
            self.best_memo[key] = (lowest_possible, NOT_EXACT)
            return lowest_possible

        visits, resets = state
        taken = visits | resets | self.blocked
        best: tuple[int, int] | None = None
        bound = limit  # we only want plans better than this
        lowest_rejected = NO_LIMIT  # the cheapest plan seen that wasn't better than `bound`

        for choice, (_, place) in enumerate(self.options[step]):
            after = (visits | place.visits, resets | place.resets)
            if not fits(place, visits, taken) or not self._can_finish(step + 1, after):
                continue
            dead_now = self._dead_between(after, self.settled[step], self.settled[step + 1])
            if dead_now >= bound:
                lowest_rejected = min(lowest_rejected, dead_now)
                continue
            cost = dead_now + self._best(step + 1, after, bound - dead_now)
            if cost < bound:
                best = (cost, choice)
                bound = cost
                if cost <= lowest_possible:
                    break  # can't do better than the slots already lost
            else:
                lowest_rejected = min(lowest_rejected, cost)
            if self.budget is not None and self.budget.expired():
                self.cut_short = True
                break

        if best is None:
            # Nothing beat `limit`; remember how good it could get at best.
            self.best_memo[key] = (lowest_rejected, NOT_EXACT)
            return lowest_rejected
        self.best_memo[key] = best
        return best[0]

    def _finish_quickly(self, step: int, state: _State) -> int:
        """Out of time: take the first route that fits at every step, count dead slots once."""
        visits, resets = state
        for later in range(step, len(self.floating)):
            choice = self.finish_memo[_key(later, (visits, resets), self.future[later])]
            _, place = self.options[later][choice]
            visits |= place.visits
            resets |= place.resets
        return self._dead_between((visits, resets), self.settled[step], SLOTS_PER_DAY)

    def _dead_between(self, state: _State, first_slot: int, last_slot: int) -> int:
        """Number of dead slots from `first_slot` up to `last_slot`, in every zone."""
        if last_slot <= first_slot:
            return 0
        visits, resets = state
        dead = dead_mask(visits, resets, self.blocked, self.reset, first_slot, last_slot)
        return dead.bit_count()

    # --- reading the answer -------------------------------------------------------------------

    def _first_choice(self, step: int, state: _State) -> int:
        """The first route that lets the rest fit (remembered by `_can_finish`)."""
        return self.finish_memo[_key(step, state, self.future[step])]

    def _best_choice(self, step: int, state: _State) -> int:
        """The route `_best` chose, or the first one that fits if it ran out of time there."""
        key = _key(step, state, self.relevant[step])
        if key in self.best_memo and self.best_memo[key][1] >= 0:
            return self.best_memo[key][1]
        return self._first_choice(step, state)

    def _read_plan(self, choose: Callable[[int, _State], int]) -> tuple[dict[str, str], _State]:
        """Walk the remembered choices to get a route for every floating session.

        Returns the routes and the final occupied slots.
        """
        route_of: dict[str, str] = {}
        visits, resets = self.start_state
        for step, session in enumerate(self.floating):
            choice = choose(step, (visits, resets))
            route, place = self.options[step][choice]
            route_of[session.id] = route
            visits |= place.visits
            resets |= place.resets
        return route_of, (visits, resets)


def _key(step: int, state: _State, keep: int) -> _Key:
    """The memory key for a state: only the slots in `keep` are remembered."""
    visits, resets = state
    return (step, visits & keep, resets & keep)
