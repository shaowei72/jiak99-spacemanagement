"""SPEC §7/§8: an enquiry check on busy days, including alternatives, stays under 300 ms."""

import random
import time

import pytest

from engine import DayState, Enquiry, Session, Settings, add_days, check_enquiry
from engine.rules import CLOSE, OPEN, START_TIMES, TICKET_MINUTES, TICKET_TYPES, ZONES
from engine_helpers import TODAY

LIMIT_SECONDS = 0.3


FULL = [0]  # sessions back to back
HALF_FULL = [0, 30, 90, 150]  # gaps of all sizes: the most zone choices for the search


def busy_day(date: str, rng: random.Random, gaps: list[int]) -> DayState:
    """Fill each zone with sessions separated by random `gaps`; about a quarter pinned."""
    sessions: list[Session] = []
    for zone in ZONES:
        start = OPEN + rng.choice(gaps)
        while True:
            ticket_type = rng.choice(TICKET_TYPES)
            if start + TICKET_MINUTES[ticket_type] > CLOSE:
                break
            pinned = rng.random() < 0.25
            sessions.append(
                Session(
                    id=f"{date}-{len(sessions)}",
                    date=date,
                    start=start,
                    ticket_type=ticket_type,
                    adults=rng.randint(1, 25),
                    zone_id=zone if pinned else None,
                    pinned=pinned,
                )
            )
            start += TICKET_MINUTES[ticket_type] + rng.choice(gaps)
    return DayState(date, tuple(sessions))


@pytest.mark.parametrize(("gaps", "min_sessions"), [(FULL, 20), (HALF_FULL, 12)])
def test_enquiry_check_is_fast_on_busy_days(gaps: list[int], min_sessions: int) -> None:
    rng = random.Random(42)
    schedule = {}
    for offset in range(0, 15):
        date = add_days(TODAY, offset)
        schedule[date] = busy_day(date, rng, gaps)
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
