"""Property-based tests (SPEC §7): random valid days, checked against simple rules and brute force."""

import itertools
import math
from collections.abc import Mapping, Sequence

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from engine import (
    Enquiry,
    NewPlacement,
    Session,
    Settings,
    SplitPlacement,
    assign,
    check_enquiry,
    dead_gaps,
    route_visits,
)
from engine.rules import CLOSE, OPEN, ROUTES, START_TIMES, TICKET_MINUTES, TICKET_TYPES, ZONES
from engine_helpers import TODAY, schedule

SLOT = 30


@st.composite
def random_day(draw: st.DrawFn, max_sessions: int = 8) -> tuple[list[Session], Settings]:
    """A random day: sessions at valid times, some pinned to a route, maybe with turnaround."""
    count = draw(st.integers(min_value=0, max_value=max_sessions))
    sessions = []
    for number in range(count):
        ticket_type = draw(st.sampled_from(TICKET_TYPES))
        latest = CLOSE - TICKET_MINUTES[ticket_type]
        start = draw(st.sampled_from([s for s in START_TIMES if s <= latest]))
        pinned_route = draw(st.one_of(st.none(), st.none(), st.sampled_from(ROUTES[ticket_type])))
        sessions.append(
            Session(
                id=f"s{number}",
                date=TODAY,
                start=start,
                ticket_type=ticket_type,
                adults=draw(st.integers(min_value=1, max_value=25)),
                route=pinned_route,
                pinned=pinned_route is not None,
            )
        )
    turnaround = draw(st.sampled_from([0, 0, 15, 30]))
    return sessions, Settings(turnaround_minutes=turnaround)


# --- a slow, obviously-correct model of the rules ----------------------------------------------

Cell = tuple[str, int]  # (zone, slot start)


def cells(start: int, end: int, zone: str) -> set[Cell]:
    return {(zone, minute) for minute in range(start, end, SLOT)}


def reset_cells(end: int, zone: str, cfg: Settings) -> set[Cell]:
    """The slots a zone is being reset after a visit ending at `end`."""
    if cfg.turnaround_minutes == 0:
        return set()
    reset_end = min(CLOSE, end + math.ceil(cfg.turnaround_minutes / SLOT) * SLOT)
    return cells(end, reset_end, zone)


def occupancy(
    sessions: Sequence[Session], route_of: Mapping[str, str], cfg: Settings
) -> tuple[set[Cell], set[Cell]] | None:
    """(visit cells, reset cells) of a plan, or None if two groups share a zone slot."""
    visits: set[Cell] = set()
    resets: set[Cell] = set()
    for s in sessions:
        for v in route_visits(s.start, s.ticket_type, route_of[s.id]):
            visits_here = cells(v.start, v.end, v.zone_id)
            if visits_here & (visits | resets):
                return None
            visits |= visits_here
            resets |= reset_cells(v.end, v.zone_id, cfg)
    if visits & resets:
        return None
    return visits, resets


def naive_dead_cells(visits: set[Cell], resets: set[Cell], cfg: Settings) -> set[Cell]:
    """Free cells that no non-Tingkat group could use, checked the slow way (SPEC §4.2)."""
    sellable: set[Cell] = set()
    for ticket_type in TICKET_TYPES:
        if ticket_type == "TINGKAT":
            continue
        for start in START_TIMES:
            if start + TICKET_MINUTES[ticket_type] > CLOSE:
                continue
            for route in ROUTES[ticket_type]:
                new_visits: set[Cell] = set()
                new_resets: set[Cell] = set()
                for v in route_visits(start, ticket_type, route):
                    new_visits |= cells(v.start, v.end, v.zone_id)
                    new_resets |= reset_cells(v.end, v.zone_id, cfg)
                if new_visits & (visits | resets) or new_resets & visits:
                    continue
                sellable |= new_visits
    every_cell = {(zone, m) for zone in ZONES for m in range(OPEN, CLOSE, SLOT)}
    return every_cell - visits - resets - sellable


def all_route_choices(sessions: Sequence[Session]) -> list[dict[str, str]]:
    floating = [s for s in sessions if not s.is_fixed]
    fixed = {s.id: s.route for s in sessions if s.is_fixed and s.route is not None}
    options = [ROUTES[s.ticket_type] for s in floating]
    return [
        {**fixed, **{s.id: r for s, r in zip(floating, routes, strict=True)}}
        for routes in itertools.product(*options)
    ]


def brute_force(sessions: list[Session], cfg: Settings) -> int | None:
    """Fewest dead minutes over every possible route combination, or None if none is valid."""
    best = None
    for route_of in all_route_choices(sessions):
        if occupancy(sessions, route_of, cfg) is None:
            continue
        dead = sum(g.minutes for g in dead_gaps(sessions, route_of, cfg))
        best = dead if best is None else min(best, dead)
    return best


def combinations(sessions: Sequence[Session]) -> int:
    return math.prod(len(ROUTES[s.ticket_type]) for s in sessions if not s.is_fixed)


# --- the properties ---------------------------------------------------------------------------


@settings(max_examples=300, deadline=None)
@given(random_day())
def test_assign_never_shares_a_zone_slot_and_never_moves_fixed_routes(
    day: tuple[list[Session], Settings],
) -> None:
    sessions, cfg = day
    plan = assign(sessions, cfg)
    if not plan.ok:
        return
    route_of = dict(plan.route_of)
    assert set(route_of) == {s.id for s in sessions}
    for s in sessions:
        assert route_of[s.id] in ROUTES[s.ticket_type]
        if s.is_fixed:
            assert route_of[s.id] == s.route
        visits = route_visits(s.start, s.ticket_type, route_of[s.id])
        # No gaps between a group's own visits, and the stay ends on time.
        assert all(a.end == b.start for a, b in itertools.pairwise(visits))
        assert visits[-1].end == s.start + TICKET_MINUTES[s.ticket_type] <= CLOSE
    assert occupancy(sessions, route_of, cfg) is not None
    # The search's own count must match counting the dead slots of the plan it returns.
    assert plan.dead_minutes == sum(g.minutes for g in dead_gaps(sessions, route_of, cfg))


@settings(max_examples=150, deadline=None)
@given(random_day(max_sessions=5))
def test_dead_slots_match_the_slow_definition(day: tuple[list[Session], Settings]) -> None:
    sessions, cfg = day
    plan = assign(sessions, cfg)
    if not plan.ok:
        return
    result = occupancy(sessions, plan.route_of, cfg)
    assert result is not None
    visits, resets = result
    expected = naive_dead_cells(visits, resets, cfg)
    found = {
        (g.zone_id, m)
        for g in dead_gaps(sessions, plan.route_of, cfg)
        for m in range(g.start, g.end, SLOT)
    }
    assert found == expected


@settings(max_examples=200, deadline=None)
@given(random_day(max_sessions=4))
def test_assign_matches_brute_force(day: tuple[list[Session], Settings]) -> None:
    """Finds a plan whenever one exists, and its dead minutes are the true minimum."""
    sessions, cfg = day
    assume(combinations(sessions) <= 2000)
    plan = assign(sessions, cfg)
    expected = brute_force(sessions, cfg)
    if expected is None:
        assert not plan.ok
    else:
        assert plan.ok
        assert plan.dead_minutes == expected


@settings(max_examples=300, deadline=None)
@given(random_day(max_sessions=6))
def test_first_feasible_mode_agrees_on_feasibility(day: tuple[list[Session], Settings]) -> None:
    sessions, cfg = day
    assert assign(sessions, cfg, optimise=False).ok == assign(sessions, cfg).ok


@settings(max_examples=200, deadline=None)
@given(
    random_day(),
    st.sampled_from(START_TIMES),
    st.sampled_from(TICKET_TYPES),
    st.integers(min_value=1, max_value=80),
)
def test_check_never_accepts_what_cannot_be_placed(
    day: tuple[list[Session], Settings], start: int, ticket_type: str, people: int
) -> None:
    sessions, cfg = day
    if not assign(sessions, cfg).ok:
        return  # random days can be broken already (clashing pins); not a real day state
    request = Enquiry(TODAY, start, ticket_type, adults=people)  # type: ignore[arg-type]
    result = check_enquiry(schedule(*sessions), cfg, request, today=TODAY)

    if result.verdict == "NOT_POSSIBLE":
        return
    if isinstance(result.placement, NewPlacement):
        sizes: tuple[int, ...] = (result.placement.people,)
        routes: tuple[str, ...] = (result.placement.route,)
    else:
        assert isinstance(result.placement, SplitPlacement)
        sizes = result.placement.new_sessions
        routes = result.placement.routes
    # Every accepted booking is split evenly, and its sub-groups fit on the routes given.
    assert sum(sizes) == people
    assert len(sizes) == -(-people // 25)
    assert max(sizes) - min(sizes) <= 1
    new = [
        Session(f"new{i}", TODAY, start, request.ticket_type, size, route=route, pinned=True)
        for i, (size, route) in enumerate(zip(sizes, routes, strict=True))
    ]
    assert assign([*sessions, *new], cfg, optimise=False).ok
