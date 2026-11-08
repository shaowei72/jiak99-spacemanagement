"""The JSON shapes of the API. Field names are camelCase in JSON, snake_case in Python.

Times are "HH:MM" strings in JSON (the engine uses minutes since midnight).
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from engine import Alternative, EnquiryResult, NewPlacement
from engine.rules import TicketType
from engine.text import format_time
from jiak_api import models

DateText = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$", examples=["2026-12-01"])]
TimeText = Annotated[str, Field(pattern=r"^\d{2}:\d{2}$", examples=["10:30"])]


def parse_time(text: str) -> int:
    """'10:30' -> 630."""
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# --- requests --------------------------------------------------------------------------------


class EnquiryIn(ApiModel):
    date: DateText
    start: TimeText
    ticket_type: TicketType = Field(alias="type")
    adults: int = Field(ge=0)
    children: int = Field(default=0, ge=0)


class CustomerIn(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str | None = None
    email: str | None = None
    company: str | None = None


class BookingIn(EnquiryIn):
    customer: CustomerIn
    notes: str = ""


class BookingPatchIn(ApiModel):
    """PATCH /bookings/:id body. Send only what changes; missing fields stay as they are.

    `customer`, if sent, replaces all the customer details.
    """

    date: DateText | None = None
    start: TimeText | None = None
    ticket_type: TicketType | None = Field(default=None, alias="type")
    adults: int | None = Field(default=None, ge=0)
    children: int | None = Field(default=None, ge=0)
    notes: str | None = None
    customer: CustomerIn | None = None


# --- responses -------------------------------------------------------------------------------


class PlacementOut(ApiModel):
    kind: Literal["new", "split"]
    people: int | None = None  # kind "new"
    new_sessions: list[int] | None = None  # kind "split": people per zone


class AlternativeOut(ApiModel):
    date: str
    start: str
    ticket_type: TicketType = Field(alias="type")
    verdict: str
    change_summary: str
    gain_summary: str

    @classmethod
    def from_engine(cls, alt: Alternative) -> "AlternativeOut":
        return cls(
            date=alt.date,
            start=format_time(alt.start),
            ticket_type=alt.ticket_type,
            verdict=alt.verdict,
            change_summary=alt.change_summary,
            gain_summary=alt.gain_summary,
        )


class CheckOut(ApiModel):
    verdict: str
    placement: PlacementOut | None
    dead_minutes_delta: int
    zone_minutes_added: int
    reason: str
    alternatives: list[AlternativeOut]

    @classmethod
    def from_engine(cls, result: EnquiryResult) -> "CheckOut":
        placement: PlacementOut | None = None
        if isinstance(result.placement, NewPlacement):
            placement = PlacementOut(kind="new", people=result.placement.people)
        elif result.placement is not None:
            placement = PlacementOut(kind="split", new_sessions=list(result.placement.new_sessions))
        return cls(
            verdict=result.verdict,
            placement=placement,
            dead_minutes_delta=result.dead_minutes_delta,
            zone_minutes_added=result.zone_minutes_added,
            reason=result.reason,
            alternatives=[AlternativeOut.from_engine(a) for a in result.alternatives],
        )


class ConflictOut(ApiModel):
    """409 body: the booking no longer fits; here is a fresh check with alternatives."""

    detail: str
    check: CheckOut


class CustomerOut(ApiModel):
    id: str
    name: str
    phone: str | None
    email: str | None
    company: str | None


class SessionOut(ApiModel):
    id: str
    start: str
    ticket_type: str = Field(alias="type")
    adults: int
    children: int
    zone_id: str | None  # None = floating, no zone yet
    pinned: bool
    status: str

    @classmethod
    def from_row(cls, row: models.Session) -> "SessionOut":
        return cls(
            id=row.id,
            start=format_time(row.start),
            ticket_type=row.ticket_type,
            adults=row.adults,
            children=row.children,
            zone_id=row.zone_id,
            pinned=row.pinned,
            status=row.status,
        )


class BookingOut(ApiModel):
    id: str
    date: str
    start: str
    ticket_type: str = Field(alias="type")
    adults: int
    children: int
    status: str
    notes: str
    created_by: str
    created_at: datetime
    customer: CustomerOut
    sessions: list[SessionOut]

    @classmethod
    def from_row(cls, row: models.Booking) -> "BookingOut":
        customer = row.customer
        return cls(
            id=row.id,
            date=row.date,
            start=format_time(row.start),
            ticket_type=row.ticket_type,
            adults=row.adults,
            children=row.children,
            status=row.status,
            notes=row.notes,
            created_by=row.created_by,
            created_at=row.created_at,
            customer=CustomerOut(
                id=customer.id,
                name=customer.name,
                phone=customer.phone,
                email=customer.email,
                company=customer.company,
            ),
            sessions=[SessionOut.from_row(s) for s in row.sessions],
        )


class BlackoutOut(ApiModel):
    start: str
    end: str


class MetricsOut(ApiModel):
    seat_hour_use: float
    zone_hour_use: float
    average_fill: float | None
    dead_gap_hours: float | None
    revenue_per_zone_hour_cents: float | None


class DayOut(ApiModel):
    date: str
    closed: bool
    locked: bool
    blackouts: list[BlackoutOut]
    bookings: list[BookingOut]
    # The engine's best zone plan right now: session id -> zone. Empty if nothing fits.
    assignment: dict[str, str]
    dead_minutes: int | None
    metrics: MetricsOut
