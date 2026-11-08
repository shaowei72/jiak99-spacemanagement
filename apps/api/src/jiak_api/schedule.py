"""Turn database rows into the engine's types (DayState, Settings)."""

from collections.abc import Collection
from dataclasses import replace
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine import Blackout, DayState, Price, Schedule, Settings
from engine import Session as EngineSession
from engine.dates import add_days
from engine.model import SessionStatus
from engine.rules import TicketType
from jiak_api import models


def load_settings(db: Session) -> Settings:
    """The business settings, or the SPEC defaults if none have been saved yet."""
    row = db.get(models.AppSettings, 1)
    if row is None:
        return Settings()
    return Settings(
        turnaround_minutes=row.turnaround_minutes,
        small_group_warn_below=row.small_group_warn_below,
        alternative_day_search=row.alternative_day_search,
        availability_calendar_days=row.availability_calendar_days,
        prices=parse_prices(row.prices),
    )


def parse_prices(raw: dict[str, Any] | None) -> dict[TicketType, Price] | None:
    """{"PREMIUM": {"adultCents": 9800, "childCents": 4900}} -> {"PREMIUM": Price(9800, 4900)}."""
    if not raw:
        return None
    return {
        cast(TicketType, code): Price(int(price["adultCents"]), int(price["childCents"]))
        for code, price in raw.items()
    }


def load_schedule(db: Session, first: str, last: str) -> Schedule:
    """Every date from `first` to `last` (inclusive) that has sessions or day settings."""
    days: dict[str, DayState] = {}

    sessions_by_date: dict[str, list[EngineSession]] = {}
    session_rows = db.scalars(
        select(models.Session)
        .where(models.Session.date >= first, models.Session.date <= last)
        .order_by(models.Session.date, models.Session.start, models.Session.id)
    )
    for row in session_rows:
        sessions_by_date.setdefault(row.date, []).append(to_engine_session(row))

    day_rows = db.scalars(
        select(models.DaySettings).where(
            models.DaySettings.date >= first, models.DaySettings.date <= last
        )
    )
    settings_by_date = {row.date: row for row in day_rows}

    for date in sorted(set(sessions_by_date) | set(settings_by_date)):
        day_row = settings_by_date.get(date)
        days[date] = DayState(
            date=date,
            sessions=tuple(sessions_by_date.get(date, [])),
            closed=day_row.closed if day_row else False,
            blackouts=tuple(
                Blackout(b["start"], b["end"]) for b in (day_row.blackouts if day_row else [])
            ),
        )
    return days


def load_around(db: Session, date: str, settings: Settings) -> Schedule:
    """The date plus the days either side that "another day" alternatives may look at."""
    reach = settings.alternative_day_search
    return load_schedule(db, add_days(date, -reach), add_days(date, reach))


def without_sessions(schedule: Schedule, session_ids: Collection[str]) -> Schedule:
    """The same schedule with some sessions left out (e.g. the booking being edited)."""
    result: dict[str, DayState] = {}
    for date, day in schedule.items():
        kept = tuple(s for s in day.sessions if s.id not in session_ids)
        result[date] = replace(day, sessions=kept)
    return result


def to_engine_session(row: models.Session) -> EngineSession:
    return EngineSession(
        id=row.id,
        date=row.date,
        start=row.start,
        ticket_type=cast(TicketType, row.ticket_type),
        adults=row.adults,
        children=row.children,
        zone_id=row.zone_id,
        pinned=row.pinned,
        status=cast(SessionStatus, row.status),
    )
