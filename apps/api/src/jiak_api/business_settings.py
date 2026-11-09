"""Saving the business settings (SPEC §2) and per-date overrides (closed days, blackouts).

A change is refused if it would leave existing bookings without a valid plan, e.g. a longer
turnaround when groups follow each other closely through a zone, or closing a day that has bookings.
"""

from typing import Any

from sqlalchemy.orm import Session

from engine import Blackout, DayState, Settings, assign
from engine.model import day_of
from jiak_api import audit, models
from jiak_api.clock import Now
from jiak_api.db import Database
from jiak_api.schedule import load_schedule, load_settings

LAST_DATE = "9999-12-31"  # 'YYYY-MM-DD' strings sort like dates, so this is "no end"


class SettingsConflict(Exception):
    """The change would strand existing bookings. The message says which."""


def save_settings(db: Session, new: Settings, *, actor: str, now: Now) -> None:
    """Replace the business settings, if every booked day from today on still has a valid plan."""
    broken = []
    for date, day in sorted(load_schedule(db, now.today, LAST_DATE).items()):
        if day.sessions and not fits(day, new):
            broken.append(date)
    if broken:
        raise SettingsConflict(
            "These settings leave no valid plan for the bookings on "
            f"{', '.join(broken)}. Move or cancel bookings there first."
        )

    before = settings_snapshot(load_settings(db))
    row = db.get(models.AppSettings, 1)
    if row is None:
        row = models.AppSettings(id=1)
        db.add(row)
    row.turnaround_minutes = new.turnaround_minutes
    row.small_group_warn_below = new.small_group_warn_below
    row.alternative_day_search = new.alternative_day_search
    row.availability_calendar_days = new.availability_calendar_days
    row.prices = prices_json(new)
    after = settings_snapshot(new)
    if after != before:
        audit.record(
            db,
            actor=actor,
            action="settings.update",
            entity="settings",
            entity_id="1",
            before=before,
            after=after,
        )
    db.commit()


def save_day_settings(
    database: Database,
    db: Session,
    date: str,
    *,
    closed: bool,
    blackouts: list[Blackout],
    actor: str,
) -> None:
    """Close or reopen a date, and set its blackout times, unless that strands bookings."""
    with database.date_lock(db, date):
        current = day_of(load_schedule(db, date, date), date)
        if closed and current.sessions:
            raise SettingsConflict(
                f"{date} has bookings. Move or cancel them before closing the day."
            )
        wanted = DayState(
            date=date, sessions=current.sessions, closed=closed, blackouts=tuple(blackouts)
        )
        if not fits(wanted, load_settings(db)):
            raise SettingsConflict(
                f"Those blackout times overlap bookings on {date}. "
                "Move or cancel them first, or choose other times."
            )

        row = db.get(models.DaySettings, date)
        if row is None:
            row = models.DaySettings(date=date)
            db.add(row)
        before = {"closed": row.closed, "blackouts": list(row.blackouts or [])}
        row.closed = closed
        row.blackouts = [{"start": b.start, "end": b.end} for b in blackouts]
        after = {"closed": row.closed, "blackouts": row.blackouts}
        if after != before:
            audit.record(
                db,
                actor=actor,
                action="day.settings",
                entity="day",
                entity_id=date,
                before=before,
                after=after,
            )
        db.commit()


# --- helpers ---------------------------------------------------------------------------------


def fits(day: DayState, settings: Settings) -> bool:
    """True if the day's groups all get a route (fixed ones keep theirs)."""
    return assign(day.sessions, settings, blackouts=day.blackouts, optimise=False).ok


def prices_json(settings: Settings) -> dict[str, Any] | None:
    """Prices as stored: {"PREMIUM": {"adultCents": 9800, "childCents": 4900}} or None."""
    if settings.prices is None:
        return None
    return {
        code: {"adultCents": price.adult_cents, "childCents": price.child_cents}
        for code, price in settings.prices.items()
    }


def settings_snapshot(settings: Settings) -> dict[str, Any]:
    """The settings as JSON-friendly values, for the API, the audit log and comparing."""
    return {
        "turnaroundMinutes": settings.turnaround_minutes,
        "smallGroupWarnBelow": settings.small_group_warn_below,
        "alternativeDaySearch": settings.alternative_day_search,
        "availabilityCalendarDays": settings.availability_calendar_days,
        "prices": prices_json(settings),
    }
