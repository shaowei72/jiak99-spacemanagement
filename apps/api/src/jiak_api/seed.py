"""Load an example day file into the database, so the API and web pages have data to show.

    uv run seed                  # apps/cli/examples/busy-day.json
    uv run seed week             # apps/cli/examples/week.json (the scenario week)
    uv run seed my-days.json     # any day file in the `uv run check` format
    uv run seed week --replace   # wipe those dates first

Each session in the file becomes one booking (customer "Sample group N"), with its route and pin
kept. Closed days and blackouts become day settings. Uses DATABASE_URL like the API does.

Seeding never touches dates that already have bookings unless you pass --replace, and refuses
any database other than SQLite unless you also pass --yes (so production is never seeded by
accident).
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from engine import DayState
from engine import Session as EngineSession
from engine.text import plural
from jiak_api import audit, models
from jiak_api.bookings import split_group
from jiak_api.config import load_config
from jiak_api.db import Database
from jiak_api.migrate import upgrade_to_latest
from jiak_cli.check import DEFAULT_DAY_FILE, load_days

EXAMPLES = DEFAULT_DAY_FILE.parent  # apps/cli/examples
SEED_ACTOR = "seed"


class SeedError(Exception):
    """Seeding was refused; the message says why and what to do."""


def resolve_day_file(name: str) -> Path:
    """'week' -> apps/cli/examples/week.json; anything else is a path to a day file."""
    example = EXAMPLES / f"{name}.json"
    if example.exists():
        return example
    path = Path(name)
    if not path.exists():
        names = ", ".join(sorted(p.stem for p in EXAMPLES.glob("*.json") if p.stem != "scenarios"))
        raise SeedError(f"No day file {name!r}. Use a path or one of: {names}.")
    return path


def seed_days(db: Session, days: dict[str, DayState], *, replace: bool) -> int:
    """Write `days` into the database and commit. Returns the number of bookings created."""
    dates = sorted(days)
    taken = dates_in_use(db, dates)
    if taken and not replace:
        raise SeedError(
            f"These dates already have bookings or day settings: {', '.join(taken)}. "
            "Run again with --replace to wipe them first."
        )

    created = 0
    for date in dates:
        removed = wipe_date(db, date)
        day = days[date]
        if day.closed or day.blackouts:
            db.add(
                models.DaySettings(
                    date=date,
                    closed=day.closed,
                    blackouts=[{"start": b.start, "end": b.end} for b in day.blackouts],
                )
            )
        for number, session in enumerate(day.sessions, start=1):
            db.add(sample_booking(session, number))
            created += 1
        audit.record(
            db,
            actor=SEED_ACTOR,
            action="day.seed",
            entity="day",
            entity_id=date,
            before={"bookingsRemoved": removed},
            after={"bookings": len(day.sessions), "closed": day.closed},
        )
    db.commit()
    return created


def sample_booking(session: EngineSession, number: int) -> models.Booking:
    """One booking for one session of the day file, keeping its route and pin."""
    parts = split_group(session.adults, session.children)
    if session.route is not None and len(parts) > 1:
        raise SeedError(
            f"{session.date} session {number}: a group over 25 can't all take one route "
            f"({session.route}). Split it into separate sessions in the day file."
        )
    booking = models.Booking(
        customer=models.Customer(name=f"Sample group {number}"),
        date=session.date,
        start=session.start,
        ticket_type=session.ticket_type,
        adults=session.adults,
        children=session.children,
        status="confirmed",
        notes="Sample data from uv run seed",
        created_by=SEED_ACTOR,
    )
    for adults, children in parts:
        booking.sessions.append(
            models.Session(
                date=session.date,
                start=session.start,
                ticket_type=session.ticket_type,
                adults=adults,
                children=children,
                route=session.route,
                pinned=session.pinned,
                status=session.status,
            )
        )
    return booking


def dates_in_use(db: Session, dates: list[str]) -> list[str]:
    """The dates (of `dates`) that already have bookings or day settings."""
    booked = db.scalars(select(models.Booking.date).where(models.Booking.date.in_(dates)))
    with_settings = db.scalars(
        select(models.DaySettings.date).where(models.DaySettings.date.in_(dates))
    )
    return sorted(set(booked) | set(with_settings))


def wipe_date(db: Session, date: str) -> int:
    """Delete every booking (any status), session, customer and day setting on `date`.

    Returns how many bookings were removed. Each booking has its own customer row, so the
    customers can go too.
    """
    booking_ids = list(db.scalars(select(models.Booking.id).where(models.Booking.date == date)))
    customer_ids = list(
        db.scalars(select(models.Booking.customer_id).where(models.Booking.date == date))
    )
    db.execute(delete(models.Session).where(models.Session.date == date))
    db.execute(delete(models.Booking).where(models.Booking.date == date))
    db.execute(delete(models.Customer).where(models.Customer.id.in_(customer_ids)))
    db.execute(delete(models.DaySettings).where(models.DaySettings.date == date))
    return len(booking_ids)


def count_bookings(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(models.Booking)) or 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seed", description="Load sample days into the database.")
    parser.add_argument(
        "day_file",
        nargs="?",
        default="busy-day",
        help="busy-day (default), week, or a path to a day file",
    )
    parser.add_argument("--replace", action="store_true", help="wipe those dates first")
    parser.add_argument("--database-url", help="default: DATABASE_URL, else the dev SQLite file")
    parser.add_argument("--yes", action="store_true", help="needed for a non-SQLite database")
    args = parser.parse_args(argv)

    url = args.database_url or load_config().database_url
    shown = make_url(url).render_as_string(hide_password=True)
    try:
        if not url.startswith("sqlite") and not args.yes:
            raise SeedError(
                f"{shown} is not a local SQLite file. Seeding adds sample bookings; "
                "add --yes if this really is a dev or test database."
            )
        path = resolve_day_file(args.day_file)
        days = load_days(path)
        upgrade_to_latest(url)  # make sure the tables exist
        database = Database(url)
        try:
            with database.make_session() as db:
                created = seed_days(db, days, replace=args.replace)
                total = count_bookings(db)
        finally:
            database.engine.dispose()
    except SeedError as error:
        print(f"seed: {error}", file=sys.stderr)
        return 1

    print(
        f"Seeded {plural(created, 'booking')} on {plural(len(days), 'date')} "
        f"from {path.name} into {shown}."
    )
    print(f"The database now has {total} bookings in total.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
