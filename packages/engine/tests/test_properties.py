"""Property-based tests (SPEC §7): random valid days, checked against simple rules and brute force."""

import itertools

from hypothesis import given, settings
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
)
from engine.evaluate import new_floating_sessions
from engine.rules import CLOSE, START_TIMES, TICKET_MINUTES, TICKET_TYPES, ZONES
from engine.timeline import session_block
from engine_helpers import TODAY, schedule


@st.composite
def random_day(draw: st.DrawFn, max_sessions: int = 8) -> tuple[list[Session], Settings]:
    """A random day: sessions at valid times, some pinned to a zone, maybe with turnaround."""
    count = draw(st.integers(min_value=0, max_value=max_sessions))
    sessions = []
    for number in range(count):
        ticket_type = draw(st.sampled_from(TICKET_TYPES))
        latest = CLOSE - TICKET_MINUTES[ticket_type]
        start = draw(st.sampled_from([s for s in START_TIMES if s <= latest]))
        pinned_zone = draw(st.one_of(st.none(), st.none(), st.sampled_from(ZONES)))
        sessions.append(
            Session(
                id=f"s{number}",
                date=TODAY,
                start=start,
                ticket_type=ticket_type,
                adults=draw(st.integers(min_value=1, max_value=25)),
                zone_id=pinned_zone,
                pinned=pinned_zone is not None,
            )
        )
    turnaround = draw(st.sampled_from([0, 0, 15, 30]))
    return sessions, Settings(turnaround_minutes=turnaround)


def overlaps_in_a_zone(sessions: list[Session], zone_of: dict[str, str], cfg: Settings) -> bool:
    for zone in ZONES:
        blocks = sorted(session_block(s, cfg) for s in sessions if zone_of[s.id] == zone)
        if any(a.end > b.start for a, b in itertools.pairwise(blocks)):
            return True
    return False


def brute_force(sessions: list[Session], cfg: Settings) -> int | None:
    """Fewest dead minutes over every possible zone combination, or None if none is valid."""
    floating = [s for s in sessions if not s.is_fixed]
    fixed = {s.id: s.zone_id for s in sessions if s.is_fixed}
    best = None
    for zones in itertools.product(ZONES, repeat=len(floating)):
        zone_of = {**fixed, **{s.id: z for s, z in zip(floating, zones, strict=True)}}
        if overlaps_in_a_zone(sessions, zone_of, cfg):  # type: ignore[arg-type]
            continue
        dead = sum(g.minutes for g in dead_gaps(sessions, zone_of, cfg))  # type: ignore[arg-type]
        best = dead if best is None else min(best, dead)
    return best


@settings(max_examples=300, deadline=None)
@given(random_day())
def test_assign_never_overlaps_and_never_moves_fixed_sessions(
    day: tuple[list[Session], Settings],
) -> None:
    sessions, cfg = day
    plan = assign(sessions, cfg)
    if not plan.ok:
        return
    zone_of = dict(plan.zone_of)
    assert set(zone_of) == {s.id for s in sessions}
    assert not overlaps_in_a_zone(sessions, zone_of, cfg)
    for s in sessions:
        if s.is_fixed:
            assert zone_of[s.id] == s.zone_id
    # The search's own count must match counting the gaps of the plan it returns.
    assert plan.dead_minutes == sum(g.minutes for g in dead_gaps(sessions, zone_of, cfg))


@settings(max_examples=300, deadline=None)
@given(random_day(max_sessions=6))
def test_assign_matches_brute_force(day: tuple[list[Session], Settings]) -> None:
    """Finds a plan whenever one exists, and its dead minutes are the true minimum."""
    sessions, cfg = day
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
    st.integers(min_value=1, max_value=60),
)
def test_check_never_accepts_what_assign_cannot_place(
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
    else:
        assert isinstance(result.placement, SplitPlacement)
        sizes = result.placement.new_sessions
    # Every accepted booking gets zones of its own, the group fits in them, and they can be placed.
    assert sum(sizes) == people
    assert len(sizes) == -(-people // 25)
    new = new_floating_sessions(request, sizes)
    assert assign([*sessions, *new], cfg, optimise=False).ok
