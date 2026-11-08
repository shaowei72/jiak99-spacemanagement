"""Read-only overviews: the availability board (one day) and the calendar (many days).

Both only read; they never change bookings, so they need no date lock.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from engine import Slot, add_days, availability, days_between, seat_hour_use, zone_hour_use
from engine.dates import weekday
from engine.model import day_of
from jiak_api import models
from jiak_api.clock import Now
from jiak_api.schedule import load_schedule, load_settings

MAX_CALENDAR_DAYS = 366  # one request covers at most a year


@dataclass(frozen=True)
class Board:
    date: str
    people: int
    closed: bool
    slots: tuple[Slot, ...]  # start-time then ticket-type order


@dataclass(frozen=True)
class CalendarDay:
    date: str
    weekday: str  # 'Mon'..'Sun'
    closed: bool
    locked: bool
    bookings: int  # confirmed bookings (a group over 25 counts once)
    people: int
    seat_hour_use: float  # 0..1
    zone_hour_use: float  # 0..1


def board_for(db: Session, date: str, people: int, now: Now) -> Board:
    """Which start time x ticket type can still be sold to a group of `people` on `date`."""
    settings = load_settings(db)
    schedule = load_schedule(db, date, date)
    slots = availability(schedule, settings, date, people, today=now.today, now_minute=now.minute)
    return Board(date=date, people=people, closed=day_of(schedule, date).closed, slots=slots)


def calendar_days(db: Session, first: str, last: str) -> list[CalendarDay]:
    """One entry per date from `first` to `last` (inclusive), for the 6-month calendar.

    Four queries in total, whatever the range: sessions, day settings, booking counts and
    locked days.
    """
    schedule = load_schedule(db, first, last)

    counts = db.execute(
        select(
            models.Booking.date,
            func.count(),
            func.sum(models.Booking.adults + models.Booking.children),
        )
        .where(
            models.Booking.date >= first,
            models.Booking.date <= last,
            models.Booking.status == "confirmed",
        )
        .group_by(models.Booking.date)
    )
    bookings_on: dict[str, tuple[int, int]] = {}
    for date, bookings, people in counts:
        bookings_on[date] = (int(bookings), int(people or 0))

    locked_dates = set(
        db.scalars(
            select(models.DaySettings.date).where(
                models.DaySettings.date >= first,
                models.DaySettings.date <= last,
                models.DaySettings.locked_at.is_not(None),
            )
        )
    )

    days: list[CalendarDay] = []
    for offset in range(days_between(first, last) + 1):
        date = add_days(first, offset)
        day = day_of(schedule, date)
        bookings, people = bookings_on.get(date, (0, 0))
        days.append(
            CalendarDay(
                date=date,
                weekday=weekday(date),
                closed=day.closed,
                locked=date in locked_dates,
                bookings=bookings,
                people=people,
                seat_hour_use=seat_hour_use(day.sessions),
                zone_hour_use=zone_hour_use(day.sessions),
            )
        )
    return days
