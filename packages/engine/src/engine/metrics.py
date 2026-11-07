"""Day metrics for ops and the 6-month calendar (SPEC §4.6)."""

from dataclasses import dataclass

from engine.assign import assign
from engine.budget import SearchBudget
from engine.model import DayState, Session, Settings
from engine.rules import CLOSE, OPEN, ZONE_CAPACITY, ZONES

OPEN_HOURS = (CLOSE - OPEN) / 60  # 12


@dataclass(frozen=True)
class DayMetrics:
    seat_hour_use: float  # share of all seat-hours sold, 0..1
    zone_hour_use: float  # share of all zone-hours used by sessions, 0..1
    average_fill: float | None  # mean headcount / 25; None if no sessions
    dead_gap_hours: float | None  # None if the day has no valid zone plan
    revenue_per_zone_hour_cents: float | None  # None without prices or sessions


def _revenue_cents(session: Session, settings: Settings) -> int:
    if settings.prices is None or session.ticket_type not in settings.prices:
        return 0
    price = settings.prices[session.ticket_type]
    return session.adults * price.adult_cents + session.children * price.child_cents


def day_metrics(
    day: DayState, settings: Settings, *, budget: SearchBudget | None = None
) -> DayMetrics:
    sessions = day.sessions
    seat_hours = sum(s.headcount * s.minutes / 60 for s in sessions)
    zone_hours = sum(s.minutes / 60 for s in sessions)

    average_fill = None
    if sessions:
        average_fill = sum(s.headcount / ZONE_CAPACITY for s in sessions) / len(sessions)

    plan = assign(sessions, settings, blackouts=day.blackouts, budget=budget)
    dead_gap_hours = plan.dead_minutes / 60 if plan.ok else None

    revenue_per_zone_hour = None
    if settings.prices is not None and zone_hours > 0:
        revenue = sum(_revenue_cents(s, settings) for s in sessions)
        revenue_per_zone_hour = revenue / zone_hours

    return DayMetrics(
        seat_hour_use=seat_hours / (len(ZONES) * ZONE_CAPACITY * OPEN_HOURS),
        zone_hour_use=zone_hours / (len(ZONES) * OPEN_HOURS),
        average_fill=average_fill,
        dead_gap_hours=dead_gap_hours,
        revenue_per_zone_hour_cents=revenue_per_zone_hour,
    )
