"""Database tables (SPEC §3), using SQLAlchemy 2.0.

Times are stored like the engine uses them: `start` is minutes since midnight, `date` is a
'YYYY-MM-DD' string in Singapore time. Only audit timestamps are real datetimes: always
timezone-aware, stored and returned in UTC (see UTCDateTime).
"""

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, DateTime, Dialect, ForeignKey, Index, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def new_id() -> str:
    """A random id for a new row, e.g. '3f2b...'."""
    return uuid.uuid4().hex


def utc_now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """A timestamp that always has a timezone: stored in UTC, read back in UTC.

    PostgreSQL keeps the timezone itself ("timestamp with time zone"). SQLite has no timezone
    support, so there we store the UTC time and add the UTC label back when reading.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Python -> database. A time without a timezone is a bug, so refuse it."""
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"Timestamp {value} has no timezone; use datetime.now(UTC)")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Database -> Python. Always hand back a UTC time."""
        if value is None:
            return None
        if value.tzinfo is None:  # SQLite: stored as UTC, without the label
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    # Every `Mapped[datetime]` column uses UTCDateTime, so all timestamps carry a timezone.
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: UTCDateTime}


# The longest text each customer field may hold. The forms and the API check these, so nothing is
# stored that PostgreSQL would refuse (SQLite doesn't check lengths itself; SPEC §5.5).
CUSTOMER_LIMITS = {"name": 200, "phone": 50, "email": 200, "company": 200}


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(CUSTOMER_LIMITS["name"]))
    phone: Mapped[str | None] = mapped_column(String(CUSTOMER_LIMITS["phone"]))
    email: Mapped[str | None] = mapped_column(String(CUSTOMER_LIMITS["email"]))
    company: Mapped[str | None] = mapped_column(String(CUSTOMER_LIMITS["company"]))


class Booking(Base):
    """One group's booking. It owns one session per sub-group (more than one if over 25)."""

    __tablename__ = "bookings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    date: Mapped[str] = mapped_column(String(10), index=True)
    start: Mapped[int]
    ticket_type: Mapped[str] = mapped_column(String(20))
    adults: Mapped[int]
    children: Mapped[int]
    status: Mapped[str] = mapped_column(
        String(20), default="confirmed"
    )  # confirmed/cancelled/no_show
    notes: Mapped[str] = mapped_column(default="")
    created_by: Mapped[str] = mapped_column(String(200))
    outlook_event_id: Mapped[str | None] = mapped_column(String(200))
    # The iCalendar UID of the Outlook event this booking was taken over from (one-off take-over,
    # SPEC §5.4), so running the take-over again skips it. None for bookings made in the app.
    source_event_id: Mapped[str | None] = mapped_column(String(300), unique=True)
    # What the Outlook event showed (date, times, subject) when the app last wrote or checked it.
    # If the event no longer matches, someone changed it in Outlook (SPEC §5.4). None = not yet
    # written to this calendar.
    outlook_seen: Mapped[str | None] = mapped_column(String(600))
    # A message from the app shown on top of the Outlook event (e.g. why an Outlook change wasn't
    # made), with the "Conflict" category. Cleared when the booking is changed again.
    outlook_note: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)

    customer: Mapped[Customer] = relationship()
    sessions: Mapped[list["Session"]] = relationship(
        back_populates="booking", cascade="all, delete-orphan", order_by="Session.id"
    )


class Session(Base):
    """One group (or sub-group) moving through its zones. Deleted when its booking is cancelled."""

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_date", "date"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    booking_id: Mapped[str] = mapped_column(ForeignKey("bookings.id"))
    date: Mapped[str] = mapped_column(String(10))
    start: Mapped[int]
    ticket_type: Mapped[str] = mapped_column(String(20))
    adults: Mapped[int]
    children: Mapped[int]
    # Zone letters in visiting order, e.g. "ABDC". None = floating (no route fixed yet).
    route: Mapped[str | None] = mapped_column(String(4))
    pinned: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open/locked/in_progress/done

    booking: Mapped[Booking] = relationship(back_populates="sessions")


class DaySettings(Base):
    """Per-date overrides: closed days, blackouts, and whether ops has locked the plan."""

    __tablename__ = "day_settings"

    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    closed: Mapped[bool] = mapped_column(default=False)
    # [{"start": 840, "end": 1020}, ...] in minutes since midnight
    blackouts: Mapped[list[dict[str, int]]] = mapped_column(JSON, default=list)
    locked_at: Mapped[datetime | None]


class AppSettings(Base):
    """The configurable settings of SPEC §2. There is only ever one row (id 1)."""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    turnaround_minutes: Mapped[int] = mapped_column(default=0)
    small_group_warn_below: Mapped[int] = mapped_column(default=0)
    alternative_day_search: Mapped[int] = mapped_column(default=7)
    availability_calendar_days: Mapped[int] = mapped_column(default=60)
    # {"PREMIUM": {"adultCents": 9800, "childCents": 4900}, ...} or None
    prices: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class PastBooking(Base):
    """A booking from before the app, kept as history only (SPEC §3, §5.4 one-off take-over).

    Read-only: never planned, never written to Outlook, no rule is checked against it. Holds no
    body text, phone numbers or email addresses. `ticket_type` is None when it couldn't be told;
    `type_source` says how it was found ('subject', 'body', 'client', 'review' or None).
    """

    __tablename__ = "past_bookings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    date: Mapped[str] = mapped_column(String(10), index=True)
    start: Mapped[int]
    end: Mapped[int]
    people: Mapped[int | None]
    ticket_type: Mapped[str | None] = mapped_column(String(20))
    type_source: Mapped[str | None] = mapped_column(String(20))
    client: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20))  # confirmed/cancelled
    was_soft_block: Mapped[bool]
    source_event_id: Mapped[str] = mapped_column(String(300), unique=True)
    original_subject: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class OutlookState(Base):
    """How the Outlook link is doing (SPEC §5.4 "Outlook sync warning"). Only ever one row (id 1)."""

    __tablename__ = "outlook_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Which calendar the stored event ids belong to (GraphGateway.calendar_id).
    calendar_id: Mapped[str | None] = mapped_column(String(300))
    last_write_ok: Mapped[datetime | None]
    last_read_ok: Mapped[datetime | None]
    last_error: Mapped[str] = mapped_column(default="")
    last_error_at: Mapped[datetime | None]


class OutlookPending(Base):
    """A date whose bookings still have to be written to Outlook. Removed once that worked."""

    __tablename__ = "outlook_pending"

    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    since: Mapped[datetime] = mapped_column(default=utc_now)


class OutlookFlag(Base):
    """A hand-typed Outlook entry the app couldn't book and marked ("Conflict" or "Needs format").
    It is looked at again only when its date, times or subject change."""

    __tablename__ = "outlook_flags"

    event_id: Mapped[str] = mapped_column(String(300), primary_key=True)
    seen: Mapped[str] = mapped_column(String(600))  # graph.format.visible_state when marked
    outcome: Mapped[str] = mapped_column(String(20))  # "Conflict" or "Needs format"
    at: Mapped[datetime] = mapped_column(default=utc_now)


class AuditLog(Base):
    """Every change to bookings, sessions and settings, with who made it."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(50))  # e.g. "booking.create"
    entity: Mapped[str] = mapped_column(String(50))  # e.g. "booking"
    entity_id: Mapped[str] = mapped_column(String(32))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(default=utc_now)
