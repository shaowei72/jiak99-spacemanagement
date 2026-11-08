"""The day's zone plan, for operations: allocate, lock / unlock, pin (SPEC §5.3).

Which sessions may move? (The engine decides this from the session itself, see `is_fixed`.)
- Allocated only (zone stored, not pinned, day not locked): may still move when the plan changes.
- Pinned (ops chose the zone) or locked (the day is locked): never moved automatically.

Every change happens under the date's lock, like bookings, so a booking and an allocation of the
same day can't interleave.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine import assign
from engine.model import day_of
from jiak_api import audit, models
from jiak_api.db import Database
from jiak_api.models import utc_now
from jiak_api.schedule import load_schedule, load_settings


class SessionNotFound(Exception):
    pass


class PlanConflict(Exception):
    """The change can't be made (e.g. pin into a clash, lock a locked day). Message says why."""


def allocate_day(database: Database, db: Session, date: str, *, actor: str) -> None:
    """Store the best zone plan: every movable session gets a zone. Fixed ones stay put."""
    with database.date_lock(db, date):
        changes = store_plan(db, date)
        if changes:
            audit.record(
                db,
                actor=actor,
                action="day.allocate",
                entity="day",
                entity_id=date,
                before={sid: change["before"] for sid, change in changes.items()},
                after={sid: change["after"] for sid, change in changes.items()},
            )
        db.commit()


def lock_day(database: Database, db: Session, date: str, *, actor: str) -> None:
    """Allocate, then freeze every session's zone and mark the day locked.

    (Writing the zone calendars in Outlook on lock is added in Milestone 4.)
    """
    with database.date_lock(db, date):
        day_row = db.get(models.DaySettings, date)
        if day_row is not None and day_row.locked_at is not None:
            raise PlanConflict(f"{date} is already locked.")
        if day_row is None:
            day_row = models.DaySettings(date=date)
            db.add(day_row)
        day_row.locked_at = utc_now()
        changes = store_plan(db, date, locked=True)
        audit.record(
            db,
            actor=actor,
            action="day.lock",
            entity="day",
            entity_id=date,
            before={sid: change["before"] for sid, change in changes.items()},
            after={sid: change["after"] for sid, change in changes.items()},
        )
        db.commit()


def unlock_day(database: Database, db: Session, date: str, *, actor: str) -> None:
    """Unfreeze the day. Sessions keep their zones (and pins), but unpinned ones may move again."""
    with database.date_lock(db, date):
        day_row = db.get(models.DaySettings, date)
        if day_row is None or day_row.locked_at is None:
            raise PlanConflict(f"{date} is not locked.")
        day_row.locked_at = None
        unlocked = []
        for row in sessions_on(db, date):
            if row.status == "locked":
                row.status = "open"
                unlocked.append(row.id)
        audit.record(
            db,
            actor=actor,
            action="day.unlock",
            entity="day",
            entity_id=date,
            before={"locked": True},
            after={"locked": False, "sessionsUnlocked": unlocked},
        )
        db.commit()


def pin_session(
    database: Database,
    db: Session,
    session_id: str,
    *,
    pinned: bool,
    zone: str | None,
    actor: str,
) -> str:
    """Pin a session to `zone` (or to its current zone), or unpin it. Returns the session's date.

    Pinning re-plans the other movable sessions around it; if no valid plan is left, PlanConflict
    and nothing changes. Unpinning keeps the zone, it only lets the session move again.
    """
    row = db.get(models.Session, session_id)
    if row is None:
        raise SessionNotFound(session_id)
    date = row.date
    with database.date_lock(db, date):
        # Re-read now that we hold the lock: an edit may have replaced the session meanwhile.
        row = db.get(models.Session, session_id, populate_existing=True)
        if row is None:
            raise SessionNotFound(session_id)
        before = {"zoneId": row.zone_id, "pinned": row.pinned}

        if not pinned:
            row.pinned = False
            action = "session.unpin"
        else:
            target = zone if zone is not None else row.zone_id
            if target is None:
                raise PlanConflict(
                    "This group has no zone yet. Choose a zone, or allocate the day first."
                )
            row.zone_id = target
            row.pinned = True
            action = "session.pin"
            try:
                store_plan(db, date)
            except PlanConflict:
                db.rollback()
                raise PlanConflict(
                    f"Zone {target} is taken at that time by a pinned or locked group."
                ) from None

        after = {"zoneId": row.zone_id, "pinned": row.pinned}
        if after != before:
            audit.record(
                db,
                actor=actor,
                action=action,
                entity="session",
                entity_id=session_id,
                before=before,
                after=after,
            )
        db.commit()
    return date


# --- helpers ---------------------------------------------------------------------------------


def sessions_on(db: Session, date: str) -> list[models.Session]:
    return list(
        db.scalars(
            select(models.Session)
            .where(models.Session.date == date)
            .order_by(models.Session.start, models.Session.id)
        )
    )


def store_plan(db: Session, date: str, *, locked: bool | None = None) -> dict[str, dict[str, Any]]:
    """Run the engine's `assign` for `date` and store its zones on the session rows.

    On a locked day (or with locked=True) every open session is locked as well, so sessions
    booked after the lock are frozen like the rest. Returns what changed, per session id.
    Raises PlanConflict if the sessions can't all fit (e.g. clashing pins).
    """
    if locked is None:
        day_row = db.get(models.DaySettings, date)
        locked = day_row is not None and day_row.locked_at is not None
    rows = sessions_on(db, date)  # also saves pending changes (e.g. a new pin) first
    day = day_of(load_schedule(db, date, date), date)
    plan = assign(day.sessions, load_settings(db), blackouts=day.blackouts)
    if not plan.ok:
        raise PlanConflict(f"The sessions on {date} don't fit in 4 zones; check the pins.")

    changes: dict[str, dict[str, Any]] = {}
    for row in rows:
        before = {"zoneId": row.zone_id, "status": row.status}
        row.zone_id = plan.zone_of[row.id]
        if locked and row.status == "open":
            row.status = "locked"
        after = {"zoneId": row.zone_id, "status": row.status}
        if after != before:
            changes[row.id] = {"before": before, "after": after}
    return changes
