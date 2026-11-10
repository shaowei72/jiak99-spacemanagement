"""SPEC §7/§8: an enquiry check on busy days, including alternatives, stays under 300 ms."""

import dataclasses
import random
import time

import pytest

from engine import DayState, Enquiry, Session, Settings, add_days, assign, check_enquiry
from engine.rules import CLOSE, START_TIMES, TICKET_MINUTES, TICKET_TYPES
from engine_helpers import TODAY

LIMIT_SECONDS = 0.3


def busy_day(date: str, rng: random.Random, tries_per_start: int) -> DayState:
    """Add random groups start by start, keeping each one only if the day still has a plan.

    More tries per start time give a fuller day. About a quarter of the groups are then pinned
    to the route the plan gave them.
    """
    sessions: list[Session] = []
    for start in START_TIMES:
        for _ in range(tries_per_start):
            ticket_type = rng.choice(TICKET_TYPES)
            if start + TICKET_MINUTES[ticket_type] > CLOSE:
                continue
            candidate = Session(
                id=f"{date}-{len(sessions)}",
                date=date,
                start=start,
                ticket_type=ticket_type,
                adults=rng.randint(1, 25),
            )
            if assign([*sessions, candidate], Settings(), optimise=False).ok:
                sessions.append(candidate)
    plan = assign(sessions, Settings(), optimise=False)
    pinned = [
        dataclasses.replace(s, route=plan.route_of[s.id], pinned=True) if rng.random() < 0.25 else s
        for s in sessions
    ]
    return DayState(date, tuple(pinned))


@pytest.mark.parametrize(("tries_per_start", "min_sessions"), [(4, 20), (1, 12)])
def test_enquiry_check_is_fast_on_busy_days(tries_per_start: int, min_sessions: int) -> None:
    rng = random.Random(42)
    schedule = {}
    for offset in range(0, 15):
        date = add_days(TODAY, offset)
        schedule[date] = busy_day(date, rng, tries_per_start)
    request_date = add_days(TODAY, 7)
    assert len(schedule[request_date].sessions) >= min_sessions

    slowest = 0.0
    for _ in range(40):
        request = Enquiry(
            request_date,
            rng.choice(START_TIMES),
            rng.choice(TICKET_TYPES),
            adults=rng.randint(1, 40),
        )
        began = time.perf_counter()
        check_enquiry(schedule, Settings(), request, today=TODAY)
        slowest = max(slowest, time.perf_counter() - began)

    assert slowest < LIMIT_SECONDS, f"slowest check took {slowest * 1000:.0f} ms"
