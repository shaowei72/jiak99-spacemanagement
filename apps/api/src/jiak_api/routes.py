"""JSON routes under /api (SPEC §5.3). Each route is short: parse, call bookings.py, reply."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from engine import Blackout, Enquiry, Price, assign, day_metrics, days_between, is_valid_date
from engine import Settings as EngineSettings
from engine.model import day_of
from engine.text import format_time
from jiak_api import bookings, business_settings, models, overview, plan
from jiak_api.auth import User, current_user, require
from jiak_api.clock import Now
from jiak_api.db import Database
from jiak_api.outlook_sync import OutlookDep
from jiak_api.schedule import load_schedule, load_settings
from jiak_api.schemas import (
    AvailabilityOut,
    BlackoutOut,
    BookingIn,
    BookingOut,
    BookingPatchIn,
    CalendarDayOut,
    CalendarOut,
    CheckOut,
    ConflictOut,
    DateText,
    DayOut,
    DaySettingsIn,
    EnquiryIn,
    MetricsOut,
    PinIn,
    SettingsBody,
    parse_time,
)

router = APIRouter(prefix="/api")


# --- dependencies ----------------------------------------------------------------------------


def get_database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def get_db(database: Annotated[Database, Depends(get_database)]) -> Iterator[Session]:
    """One database session per request, closed (and rolled back if uncommitted) at the end."""
    with database.make_session() as db:
        yield db


def get_now(request: Request) -> Now:
    now: Now = request.app.state.clock()
    return now


DatabaseDep = Annotated[Database, Depends(get_database)]
DbDep = Annotated[Session, Depends(get_db)]
NowDep = Annotated[Now, Depends(get_now)]
SalesUser = Annotated[User, Depends(require("Sales"))]
OperationsUser = Annotated[User, Depends(require("Operations"))]
AnyUser = Annotated[User, Depends(current_user)]


def to_enquiry(body: EnquiryIn) -> Enquiry:
    return Enquiry(
        date=body.date,
        start=parse_time(body.start),
        ticket_type=body.ticket_type,
        adults=body.adults,
        children=body.children,
    )


# --- enquiries and bookings ------------------------------------------------------------------


@router.get("/enquiries/check")
def check_enquiry(
    query: Annotated[EnquiryIn, Query()],
    response: Response,
    db: DbDep,
    now: NowDep,
    _user: SalesUser,
) -> CheckOut:
    """The verdict on an enquiry plus up to 3 better alternatives. Stores nothing.

    Example: /api/enquiries/check?date=2026-12-01&start=10:00&type=PREMIUM&adults=10
    """
    # The answer changes as soon as someone else books, so browsers must never reuse it.
    response.headers["Cache-Control"] = "no-store"
    result = bookings.check(db, to_enquiry(query), now)
    return CheckOut.from_engine(result)


@router.post(
    "/bookings",
    status_code=201,
    responses={409: {"model": ConflictOut, "description": "No longer fits"}},
)
def create_booking(
    body: BookingIn,
    database: DatabaseDep,
    db: DbDep,
    now: NowDep,
    user: SalesUser,
    outlook: OutlookDep,
) -> BookingOut:
    """Book the request (or an alternative). Re-checked under the date lock."""
    customer = bookings.CustomerDetails(
        name=body.customer.name,
        phone=body.customer.phone,
        email=body.customer.email,
        company=body.customer.company,
    )
    # If it no longer fits, NoLongerFits becomes a 409 reply (see no_longer_fits_handler).
    booking = bookings.create_booking(
        database, db, to_enquiry(body), customer, notes=body.notes, actor=user.name, now=now
    )
    outlook.dates_changed(booking.date)
    return BookingOut.from_row(booking)


def no_longer_fits_handler(_request: Request, error: Exception) -> JSONResponse:
    """409 with a fresh check (verdict, reason, alternatives) so sales can offer something else."""
    assert isinstance(error, bookings.NoLongerFits)
    conflict = ConflictOut(detail=error.result.reason, check=CheckOut.from_engine(error.result))
    return JSONResponse(status_code=409, content=conflict.model_dump(by_alias=True))


@router.get("/bookings/{booking_id}")
def get_booking(booking_id: str, db: DbDep, _user: AnyUser) -> BookingOut:
    booking = db.get(models.Booking, booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found.")
    return BookingOut.from_row(booking)


@router.patch(
    "/bookings/{booking_id}",
    responses={409: {"model": ConflictOut, "description": "New slot or headcount doesn't fit"}},
)
def edit_booking(
    booking_id: str,
    body: BookingPatchIn,
    database: DatabaseDep,
    db: DbDep,
    now: NowDep,
    user: SalesUser,
    outlook: OutlookDep,
) -> BookingOut:
    """Change a booking. A new date, time, type or headcount is re-checked like a new booking.

    Example body (move to 14:00 and add two children): {"start": "14:00", "children": 2}
    """
    customer = None
    if body.customer is not None:
        customer = bookings.CustomerDetails(
            name=body.customer.name,
            phone=body.customer.phone,
            email=body.customer.email,
            company=body.customer.company,
        )
    changes = bookings.BookingChanges(
        date=body.date,
        start=parse_time(body.start) if body.start is not None else None,
        ticket_type=body.ticket_type,
        adults=body.adults,
        children=body.children,
        notes=body.notes,
        customer=customer,
    )
    # If it doesn't fit, NoLongerFits becomes a 409 reply (see no_longer_fits_handler).
    try:
        booking = bookings.edit_booking(database, db, booking_id, changes, actor=user.name, now=now)
    except bookings.BookingNotFound:
        raise HTTPException(status_code=404, detail="Booking not found.") from None
    except bookings.BookingNotChangeable as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    outlook.dates_changed(booking.date)
    return BookingOut.from_row(booking)


@router.post("/bookings/{booking_id}/cancel")
def cancel_booking(
    booking_id: str, database: DatabaseDep, db: DbDep, user: SalesUser, outlook: OutlookDep
) -> BookingOut:
    """Cancel a booking; its zone slots become free again."""
    try:
        booking = bookings.cancel_booking(database, db, booking_id, actor=user.name)
    except bookings.BookingNotFound:
        raise HTTPException(status_code=404, detail="Booking not found.") from None
    except bookings.BookingNotChangeable as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    outlook.dates_changed(booking.date)
    return BookingOut.from_row(booking)


@router.post("/bookings/{booking_id}/no-show")
def mark_no_show(
    booking_id: str,
    database: DatabaseDep,
    db: DbDep,
    now: NowDep,
    user: OperationsUser,
    outlook: OutlookDep,
) -> BookingOut:
    """The group didn't come (only after the start time). Its zone slots become free again."""
    try:
        booking = bookings.mark_no_show(database, db, booking_id, actor=user.name, now=now)
    except bookings.BookingNotFound:
        raise HTTPException(status_code=404, detail="Booking not found.") from None
    except bookings.BookingNotChangeable as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    outlook.dates_changed(booking.date)
    return BookingOut.from_row(booking)


# --- availability board and calendar ---------------------------------------------------------


def real_date(text: str, field: str) -> str:
    """Reject dates that look right but don't exist, e.g. 2026-02-30."""
    if not is_valid_date(text):
        raise HTTPException(status_code=422, detail=f"{field}: {text} is not a real date.")
    return text


@router.get("/availability")
def get_availability(
    date: DateText,
    n: Annotated[int, Query(ge=1, le=100, description="Group size (people)")],
    response: Response,
    db: DbDep,
    now: NowDep,
    _user: AnyUser,
) -> AvailabilityOut:
    """The availability board: for a group of `n`, which start time x ticket type can be sold.

    Example: /api/availability?date=2026-12-01&n=10
    """
    response.headers["Cache-Control"] = "no-store"  # changes with every booking
    board = overview.board_for(db, real_date(date, "date"), n, now)
    return AvailabilityOut.from_board(board)


@router.get("/calendar")
def get_calendar(
    first: Annotated[DateText, Query(alias="from")],
    last: Annotated[DateText, Query(alias="to")],
    response: Response,
    db: DbDep,
    _user: AnyUser,
) -> CalendarOut:
    """Per-day use for the 6-month calendar (at most a year per request).

    Example: /api/calendar?from=2026-12-01&to=2027-05-31
    """
    response.headers["Cache-Control"] = "no-store"
    real_date(first, "from")
    real_date(last, "to")
    span = days_between(first, last) + 1
    if span < 1:
        raise HTTPException(status_code=422, detail="'to' must not be before 'from'.")
    if span > overview.MAX_CALENDAR_DAYS:
        raise HTTPException(
            status_code=422, detail=f"At most {overview.MAX_CALENDAR_DAYS} days per request."
        )
    days = overview.calendar_days(db, first, last)
    return CalendarOut(
        first_date=first,
        last_date=last,
        days=[
            CalendarDayOut(
                date=d.date,
                weekday=d.weekday,
                closed=d.closed,
                locked=d.locked,
                bookings=d.bookings,
                people=d.people,
                seat_hour_use=d.seat_hour_use,
                zone_hour_use=d.zone_hour_use,
            )
            for d in days
        ],
    )


# --- settings --------------------------------------------------------------------------------


@router.get("/settings")
def get_settings(db: DbDep, _user: AnyUser) -> SettingsBody:
    """The business settings (SPEC §2): the defaults until ops saves others."""
    return SettingsBody.model_validate(business_settings.settings_snapshot(load_settings(db)))


@router.put("/settings")
def put_settings(body: SettingsBody, db: DbDep, now: NowDep, user: OperationsUser) -> SettingsBody:
    """Replace the business settings. Refused (409) if bookings from today on would no longer fit."""
    prices = None
    if body.prices is not None:
        prices = {code: Price(p.adult_cents, p.child_cents) for code, p in body.prices.items()}
    new = EngineSettings(
        turnaround_minutes=body.turnaround_minutes,
        small_group_warn_below=body.small_group_warn_below,
        alternative_day_search=body.alternative_day_search,
        availability_calendar_days=body.availability_calendar_days,
        prices=prices,
    )
    try:
        business_settings.save_settings(db, new, actor=user.name, now=now)
    except business_settings.SettingsConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    return SettingsBody.model_validate(business_settings.settings_snapshot(load_settings(db)))


@router.put("/days/{date}/settings")
def put_day_settings(
    date: DateText, body: DaySettingsIn, database: DatabaseDep, db: DbDep, user: OperationsUser
) -> DayOut:
    """Close or reopen a date and set its blackout times. Refused (409) if it strands bookings.

    Example body: {"closed": false, "blackouts": [{"start": "14:00", "end": "17:00"}]}
    """
    date = real_date(date, "date")
    blackouts = [Blackout(parse_time(b.start), parse_time(b.end)) for b in body.blackouts]
    try:
        business_settings.save_day_settings(
            database, db, date, closed=body.closed, blackouts=blackouts, actor=user.name
        )
    except business_settings.SettingsConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    return day_view(db, date)


# --- days ------------------------------------------------------------------------------------


@router.get("/days/{date}")
def get_day(date: DateText, db: DbDep, _user: AnyUser) -> DayOut:
    """Everything about one date: bookings, sessions, the best plan (routes), and metrics."""
    return day_view(db, real_date(date, "date"))


@router.post("/days/{date}/allocate")
def allocate_day(
    date: DateText, database: DatabaseDep, db: DbDep, user: OperationsUser, outlook: OutlookDep
) -> DayOut:
    """Give every movable session its route from the best plan. Pinned and locked ones stay put.

    Unpinned routes are still a suggestion: they may change when later bookings need it.
    """
    date = real_date(date, "date")
    try:
        plan.allocate_day(database, db, date, actor=user.name)
    except plan.PlanConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    outlook.dates_changed(date)
    return day_view(db, date)


@router.post("/days/{date}/lock")
def lock_day(
    date: DateText, database: DatabaseDep, db: DbDep, user: OperationsUser, outlook: OutlookDep
) -> DayOut:
    """Allocate, then freeze every route of the day.

    Bookings made later start without a route; the next allocate gives them one and freezes it.
    """
    date = real_date(date, "date")
    try:
        plan.lock_day(database, db, date, actor=user.name)
    except plan.PlanConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    outlook.dates_changed(date)
    return day_view(db, date)


@router.post("/days/{date}/unlock")
def unlock_day(date: DateText, database: DatabaseDep, db: DbDep, user: OperationsUser) -> DayOut:
    """Unfreeze the day. Routes and pins are kept; unpinned routes may change again."""
    date = real_date(date, "date")
    try:
        plan.unlock_day(database, db, date, actor=user.name)
    except plan.PlanConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    return day_view(db, date)


@router.patch("/sessions/{session_id}/pin")
def pin_session(
    session_id: str,
    body: PinIn,
    database: DatabaseDep,
    db: DbDep,
    user: OperationsUser,
    outlook: OutlookDep,
) -> DayOut:
    """Pin a group to a route, or unpin it. Returns the whole day, as other groups may move.

    {"route": "ABDC"} pins to that route · {} pins to the current route · {"pinned": false} unpins.
    """
    try:
        date = plan.pin_session(
            database, db, session_id, pinned=body.pinned, route=body.route, actor=user.name
        )
    except plan.SessionNotFound:
        raise HTTPException(status_code=404, detail="Session not found.") from None
    except plan.PlanConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    outlook.dates_changed(date)
    return day_view(db, date)


def day_view(db: Session, date: str) -> DayOut:
    """The day view used by GET /days/:date and returned by every plan change."""
    settings = load_settings(db)
    day = day_of(load_schedule(db, date, date), date)
    plan = assign(day.sessions, settings, blackouts=day.blackouts)
    metrics = day_metrics(day, settings)
    day_row = db.get(models.DaySettings, date)
    booking_rows = db.scalars(
        select(models.Booking)
        .where(models.Booking.date == date, models.Booking.status != "cancelled")
        .options(selectinload(models.Booking.sessions), selectinload(models.Booking.customer))
        .order_by(models.Booking.start, models.Booking.created_at)
        .execution_options(populate_existing=True)  # show the routes just stored
    )
    return DayOut(
        date=date,
        closed=day.closed,
        locked=day_row is not None and day_row.locked_at is not None,
        blackouts=[
            BlackoutOut(start=format_time(b.start), end=format_time(b.end)) for b in day.blackouts
        ],
        bookings=[BookingOut.from_row(b) for b in booking_rows],
        assignment=dict(plan.route_of) if plan.ok else {},
        dead_minutes=plan.dead_minutes if plan.ok else None,
        metrics=MetricsOut(
            seat_hour_use=metrics.seat_hour_use,
            zone_hour_use=metrics.zone_hour_use,
            average_fill=metrics.average_fill,
            dead_gap_hours=metrics.dead_gap_hours,
            revenue_per_zone_hour_cents=metrics.revenue_per_zone_hour_cents,
        ),
    )
