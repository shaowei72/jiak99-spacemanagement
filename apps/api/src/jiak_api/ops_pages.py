"""More web pages (Milestone 3): availability board, 6-month calendar, ops day timeline, settings.

These pages are plain HTML forms: a GET form to choose what to look at, and POST forms for
the operations actions (allocate, lock, pin, save settings). After a successful action the
browser is sent back to the page (Post/Redirect/Get); if the action is refused, the page is
shown again with the reason.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, cast

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from engine import Blackout, Price, dead_gaps, is_valid_date
from engine import Settings as EngineSettings
from engine.dates import add_days, weekday
from engine.model import day_of
from engine.rules import (
    CLOSE,
    OPEN,
    TICKET_MINUTES,
    TICKET_NAMES,
    TICKET_SHORT_NAMES,
    TICKET_TYPES,
    ZONE_CAPACITY,
    ZONES,
    TicketType,
)
from engine.text import format_time
from jiak_api import business_settings, models, overview, plan
from jiak_api.clock import Now
from jiak_api.pages import templates
from jiak_api.routes import AnyUser, DatabaseDep, DbDep, NowDep, OperationsUser, day_view
from jiak_api.schedule import load_schedule, load_settings
from jiak_api.schemas import AvailabilityOut, DayOut, SettingsBody

router = APIRouter(include_in_schema=False)

CALENDAR_MONTHS = 6
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip


def page(
    request: Request, template: str, context: dict[str, Any], status_code: int = 200
) -> Response:
    """Render a whole page. Never cached: every page shows what is booked right now."""
    full_context = {
        **context,
        "short_names": TICKET_SHORT_NAMES,
        "ticket_names": TICKET_NAMES,
        "ticket_types": TICKET_TYPES,
    }
    response = templates.TemplateResponse(request, template, full_context, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    return response


def date_or_today(date: str | None, today: str) -> str:
    """The date asked for, or today if it is missing or not a real date."""
    if date is not None and is_valid_date(date):
        return date
    return today


def percent(fraction: float) -> str:
    """0.256 -> '26%'."""
    return f"{round(fraction * 100)}%"


templates.env.globals["percent"] = percent


# --- availability board ----------------------------------------------------------------------


@router.get("/board", response_class=HTMLResponse)
def board_page(
    request: Request,
    db: DbDep,
    now: NowDep,
    _user: AnyUser,
    date: Annotated[str | None, Query()] = None,
    n: Annotated[str | None, Query()] = None,
) -> Response:
    """Which start time x ticket type can be sold to a group of `n` on `date`."""
    date = date_or_today(date, now.today)
    errors = []
    people = 1
    if n:
        if n.isdigit() and 1 <= int(n) <= 100:
            people = int(n)
        else:
            errors.append("Group size must be a whole number from 1 to 100.")
    board = AvailabilityOut.from_board(overview.board_for(db, date, people, now))
    context = {
        "date": date,
        "weekday": weekday(date),
        "people": people,
        "board": board,
        "errors": errors,
        "previous_day": add_days(date, -1),
        "next_day": add_days(date, 1),
    }
    return page(request, "board.html", context)


# --- 6-month calendar ------------------------------------------------------------------------


@dataclass(frozen=True)
class CalendarCell:
    date: str
    day_number: int
    day: overview.CalendarDay
    band: str  # CSS class for the colour: 'use0' (empty) .. 'use4' (busiest)
    is_past: bool
    is_today: bool


@dataclass(frozen=True)
class CalendarMonth:
    title: str  # 'December 2026'
    weeks: list[list[CalendarCell | None]]  # Monday first; None = a day of another month


def next_month(month: str) -> str:
    """'2026-12' -> '2027-01'."""
    year, number = int(month[:4]), int(month[5:7])
    if number == 12:
        return f"{year + 1}-01"
    return f"{year}-{number + 1:02d}"


def previous_month(month: str) -> str:
    """'2027-01' -> '2026-12'."""
    year, number = int(month[:4]), int(month[5:7])
    if number == 1:
        return f"{year - 1}-12"
    return f"{year}-{number - 1:02d}"


def use_band(seat_hour_use: float) -> str:
    """Colour band for a day's seat-hour use: none, under 25%, 50%, 75%, and 75% or more."""
    if seat_hour_use == 0:
        return "use0"
    if seat_hour_use < 0.25:
        return "use1"
    if seat_hour_use < 0.5:
        return "use2"
    if seat_hour_use < 0.75:
        return "use3"
    return "use4"


def build_months(
    first_month: str, days: list[overview.CalendarDay], today: str
) -> list[CalendarMonth]:
    """Lay the days out as month grids, one row per week, Monday first."""
    by_date = {day.date: day for day in days}
    months = []
    month = first_month
    for _ in range(CALENDAR_MONTHS):
        weeks: list[list[CalendarCell | None]] = []
        week: list[CalendarCell | None] = [None] * WEEKDAYS.index(weekday(f"{month}-01"))
        date = f"{month}-01"
        while date.startswith(month):
            day = by_date[date]
            cell = CalendarCell(
                date=date,
                day_number=int(date[8:10]),
                day=day,
                band=use_band(day.seat_hour_use),
                is_past=date < today,
                is_today=date == today,
            )
            week.append(cell)
            if len(week) == 7:
                weeks.append(week)
                week = []
            date = add_days(date, 1)
        if week:
            weeks.append(week + [None] * (7 - len(week)))
        title = f"{MONTH_NAMES[int(month[5:7]) - 1]} {month[:4]}"
        months.append(CalendarMonth(title=title, weeks=weeks))
        month = next_month(month)
    return months


@router.get("/calendar", response_class=HTMLResponse)
def calendar_page(
    request: Request,
    db: DbDep,
    now: NowDep,
    _user: AnyUser,
    start: Annotated[str | None, Query()] = None,
) -> Response:
    """Six months, one cell per day, coloured by seat-hour use. `start` is a month, '2026-12'."""
    first_month = now.today[:7]
    if start is not None and is_valid_date(f"{start}-01"):
        first_month = start
    last_month = first_month
    for _ in range(CALENDAR_MONTHS):
        last_month = next_month(last_month)
    first = f"{first_month}-01"
    last = add_days(f"{last_month}-01", -1)  # the day before the 7th month starts
    days = overview.calendar_days(db, first, last)
    context = {
        "months": build_months(first_month, days, now.today),
        "weekdays": WEEKDAYS,
        "earlier": previous_month(first_month),
        "later": next_month(first_month),
    }
    return page(request, "calendar.html", context)


# --- operations day timeline -----------------------------------------------------------------


@dataclass(frozen=True)
class Bar:
    """Something drawn on a zone's timeline row."""

    kind: str  # 'session', 'blackout' or 'dead'
    start: int
    end: int
    left: str  # CSS position, e.g. '25.0%'
    width: str
    label: str = ""
    title: str = ""  # tooltip
    ticket_type: str = ""
    pinned: bool = False
    floating: bool = False  # no zone stored yet: the zone shown is only the best plan


@dataclass(frozen=True)
class ZoneRow:
    zone: str
    bars: list[Bar]


@dataclass(frozen=True)
class SessionLine:
    """One row of the session list under the timeline."""

    id: str
    booking_id: str
    start: str
    end: str
    ticket_type: str
    name: str
    people: int
    zone: str  # where the plan puts it
    stored_zone: str | None
    pinned: bool
    status: str


def position(start: int, end: int) -> tuple[str, str]:
    """CSS left and width (in % of the opening hours) for a stretch of time."""
    day_length = CLOSE - OPEN
    left = (start - OPEN) / day_length * 100
    width = (end - start) / day_length * 100
    return f"{left:.3f}%", f"{width:.3f}%"


def build_timeline(db: Session, view: DayOut) -> tuple[list[ZoneRow], list[SessionLine]]:
    """The zone rows (sessions, blackouts and dead gaps) and the session list for one day."""
    settings = load_settings(db)
    day = day_of(load_schedule(db, view.date, view.date), view.date)
    bars: dict[str, list[Bar]] = {zone: [] for zone in ZONES}
    lines: list[SessionLine] = []

    for blackout in view.blackouts:
        start, end = parse_minutes(blackout.start), parse_minutes(blackout.end)
        left, width = position(start, end)
        for zone in ZONES:
            bars[zone].append(Bar("blackout", start, end, left, width, title="Blocked"))

    for booking in view.bookings:
        for session in booking.sessions:
            zone = view.assignment.get(session.id, session.zone_id or "?")
            start = parse_minutes(session.start)
            ticket_type = cast(TicketType, session.ticket_type)
            end = start + TICKET_MINUTES[ticket_type]
            people = session.adults + session.children
            label = f"{TICKET_SHORT_NAMES[ticket_type]} {people}/{ZONE_CAPACITY}"
            title = (
                f"{session.start}–{format_time(end)} · {booking.customer.name} · {people} people"
            )
            left, width = position(start, end)
            if zone in bars:
                bars[zone].append(
                    Bar(
                        "session",
                        start,
                        end,
                        left,
                        width,
                        label=label,
                        title=title,
                        ticket_type=session.ticket_type,
                        pinned=session.pinned,
                        floating=session.zone_id is None,
                    )
                )
            lines.append(
                SessionLine(
                    id=session.id,
                    booking_id=booking.id,
                    start=session.start,
                    end=format_time(end),
                    ticket_type=session.ticket_type,
                    name=booking.customer.name,
                    people=people,
                    zone=zone,
                    stored_zone=session.zone_id,
                    pinned=session.pinned,
                    status=session.status,
                )
            )

    if view.assignment:
        for gap in dead_gaps(day.sessions, view.assignment, settings, blackouts=day.blackouts):
            left, width = position(gap.start, gap.end)
            title = f"Dead gap {format_time(gap.start)}–{format_time(gap.end)}: nothing fits"
            bars[gap.zone_id].append(Bar("dead", gap.start, gap.end, left, width, title=title))

    lines.sort(key=lambda line: (line.start, line.zone))
    rows = [ZoneRow(zone, sorted(bars[zone], key=lambda bar: bar.start)) for zone in ZONES]
    return rows, lines


def parse_minutes(text: str) -> int:
    """'10:30' -> 630."""
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


def blackouts_text(view: DayOut) -> str:
    """The day's blackouts as the form shows them: one '14:00-17:00' per line."""
    return "\n".join(f"{b.start}-{b.end}" for b in view.blackouts)


def ops_page(
    request: Request,
    db: Session,
    date: str,
    *,
    errors: list[str] | None = None,
    blackouts_input: str | None = None,
    status_code: int = 200,
) -> Response:
    """The operations page for `date`, optionally with error messages."""
    view = day_view(db, date)
    rows, lines = build_timeline(db, view)
    hours = [format_time(minute) for minute in range(OPEN, CLOSE + 1, 60)]
    context = {
        "date": date,
        "weekday": weekday(date),
        "view": view,
        "rows": rows,
        "lines": lines,
        "hours": hours,
        "zones": ZONES,
        "errors": errors or [],
        "blackouts_input": blackouts_text(view) if blackouts_input is None else blackouts_input,
        "previous_day": add_days(date, -1),
        "next_day": add_days(date, 1),
    }
    return page(request, "ops.html", context, status_code=status_code)


def back_to_ops(date: str) -> RedirectResponse:
    """After a successful action: show the day again (reloading can't repeat the action)."""
    return RedirectResponse(f"/ops?date={date}", status_code=303)


@router.get("/ops", response_class=HTMLResponse)
def ops_day_page(
    request: Request,
    db: DbDep,
    now: NowDep,
    _user: AnyUser,
    date: Annotated[str | None, Query()] = None,
) -> Response:
    """The Day plan: one day for operations, zones x time, dead gaps shaded, allocate / lock / pin."""
    return ops_page(request, db, date_or_today(date, now.today))


def run_plan_action(
    request: Request, db: Session, now: Now, date: str, action: Callable[[], None]
) -> Response:
    """Run allocate / lock / unlock; on a conflict show the page again with the reason."""
    if not is_valid_date(date):
        return ops_page(request, db, now.today, errors=["Pick a real date."], status_code=422)
    try:
        action()
    except plan.PlanConflict as error:
        db.rollback()
        return ops_page(request, db, date, errors=[str(error)], status_code=409)
    return back_to_ops(date)


@router.post("/ops/{date}/allocate", response_class=HTMLResponse)
def ops_allocate(
    request: Request,
    date: str,
    database: DatabaseDep,
    db: DbDep,
    now: NowDep,
    user: OperationsUser,
) -> Response:
    """Give every movable group its zone from the best plan."""
    return run_plan_action(
        request, db, now, date, lambda: plan.allocate_day(database, db, date, actor=user.name)
    )


@router.post("/ops/{date}/lock", response_class=HTMLResponse)
def ops_lock(
    request: Request,
    date: str,
    database: DatabaseDep,
    db: DbDep,
    now: NowDep,
    user: OperationsUser,
) -> Response:
    """Allocate, then freeze every zone of the day."""
    return run_plan_action(
        request, db, now, date, lambda: plan.lock_day(database, db, date, actor=user.name)
    )


@router.post("/ops/{date}/unlock", response_class=HTMLResponse)
def ops_unlock(
    request: Request,
    date: str,
    database: DatabaseDep,
    db: DbDep,
    now: NowDep,
    user: OperationsUser,
) -> Response:
    """Unfreeze the day; unpinned groups may move again."""
    return run_plan_action(
        request, db, now, date, lambda: plan.unlock_day(database, db, date, actor=user.name)
    )


@router.post("/ops/sessions/{session_id}/pin", response_class=HTMLResponse)
def ops_pin(
    request: Request,
    session_id: str,
    database: DatabaseDep,
    db: DbDep,
    now: NowDep,
    user: OperationsUser,
    zone: Annotated[str, Form()] = "",
    pinned: Annotated[str, Form()] = "true",
) -> Response:
    """Pin a group to a zone (the zone chosen in the form), or unpin it."""
    row = db.get(models.Session, session_id)
    if row is None:
        return ops_page(
            request, db, now.today, errors=["That group no longer exists."], status_code=404
        )
    date = row.date
    if zone not in ("", *ZONES):
        return ops_page(request, db, date, errors=["Choose zone A, B, C or D."], status_code=422)
    try:
        plan.pin_session(
            database,
            db,
            session_id,
            pinned=pinned == "true",
            zone=zone or None,
            actor=user.name,
        )
    except plan.SessionNotFound:
        return ops_page(request, db, date, errors=["That group no longer exists."], status_code=404)
    except plan.PlanConflict as error:
        db.rollback()
        return ops_page(request, db, date, errors=[str(error)], status_code=409)
    return back_to_ops(date)


def parse_blackouts(text: str) -> tuple[list[Blackout], list[str]]:
    """'14:00-17:00' lines -> blackouts, or the problems found."""
    blackouts: list[Blackout] = []
    errors: list[str] = []
    for line in text.splitlines():
        line = line.strip().replace("–", "-")
        if not line:
            continue
        parts = [part.strip() for part in line.split("-")]
        if len(parts) != 2 or not all(is_time(part) for part in parts):
            errors.append(f"'{line}' is not like 14:00-17:00.")
            continue
        start, end = parse_minutes(parts[0]), parse_minutes(parts[1])
        if not OPEN <= start < end <= CLOSE:
            errors.append(f"'{line}': the end must be after the start, within 09:00-21:00.")
            continue
        blackouts.append(Blackout(start, end))
    return blackouts, errors


def is_time(text: str) -> bool:
    """True for 'HH:MM' with a real hour and minute."""
    if len(text) != 5 or text[2] != ":" or not (text[:2] + text[3:]).isdigit():
        return False
    return int(text[:2]) < 24 and int(text[3:]) < 60


@router.post("/ops/{date}/settings", response_class=HTMLResponse)
def ops_day_settings(
    request: Request,
    date: str,
    database: DatabaseDep,
    db: DbDep,
    now: NowDep,
    user: OperationsUser,
    closed: Annotated[str, Form()] = "",
    blackouts: Annotated[str, Form()] = "",
) -> Response:
    """Close / reopen the day and set its blackout times."""
    if not is_valid_date(date):
        return ops_page(request, db, now.today, errors=["Pick a real date."], status_code=422)
    parsed, errors = parse_blackouts(blackouts)
    if errors:
        return ops_page(
            request, db, date, errors=errors, blackouts_input=blackouts, status_code=422
        )
    try:
        business_settings.save_day_settings(
            database, db, date, closed=closed == "on", blackouts=parsed, actor=user.name
        )
    except business_settings.SettingsConflict as error:
        db.rollback()
        return ops_page(
            request, db, date, errors=[str(error)], blackouts_input=blackouts, status_code=409
        )
    return back_to_ops(date)


# --- settings --------------------------------------------------------------------------------

SETTING_FIELDS = {
    "turnaroundMinutes": "Turnaround must be a whole number of minutes, 0 to 120.",
    "smallGroupWarnBelow": "Small-group warning must be a whole number, 0 to 25 (0 = off).",
    "alternativeDaySearch": "Days searched either side must be a whole number, 0 to 30.",
    "availabilityCalendarDays": "Outlook availability days must be a whole number, 1 to 183.",
}


def settings_form(settings: EngineSettings) -> dict[str, str]:
    """The settings as the form shows them. Prices in dollars, e.g. '98.00'; empty = no price."""
    form = {
        "turnaroundMinutes": str(settings.turnaround_minutes),
        "smallGroupWarnBelow": str(settings.small_group_warn_below),
        "alternativeDaySearch": str(settings.alternative_day_search),
        "availabilityCalendarDays": str(settings.availability_calendar_days),
    }
    for code in TICKET_TYPES:
        price = settings.prices.get(code) if settings.prices else None
        form[f"{code}_adult"] = f"{price.adult_cents / 100:.2f}" if price else ""
        form[f"{code}_child"] = f"{price.child_cents / 100:.2f}" if price else ""
    return form


def dollars_to_cents(text: str) -> int | None:
    """'98' or '98.50' -> 9800 / 9850. None if it isn't a price."""
    text = text.strip().lstrip("$")
    whole, _, fraction = text.partition(".")
    if not whole.isdigit() or len(fraction) > 2 or (fraction and not fraction.isdigit()):
        return None
    return int(whole) * 100 + int(fraction.ljust(2, "0") or "0")


def read_settings_form(form: dict[str, str]) -> tuple[SettingsBody | None, list[str]]:
    """Check the settings form. Returns the settings if valid, otherwise the error messages."""
    errors: list[str] = []
    prices: dict[str, dict[str, int]] = {}
    for code in TICKET_TYPES:
        adult, child = form[f"{code}_adult"].strip(), form[f"{code}_child"].strip()
        if not adult and not child:
            continue  # no price for this ticket
        adult_cents, child_cents = dollars_to_cents(adult), dollars_to_cents(child)
        if adult_cents is None or child_cents is None:
            errors.append(
                f"{TICKET_SHORT_NAMES[code]}: enter both prices in dollars, e.g. 98.00, or neither."
            )
            continue
        prices[code] = {"adultCents": adult_cents, "childCents": child_cents}
    body = {key: form[key].strip() for key in SETTING_FIELDS}
    try:
        settings = SettingsBody.model_validate({**body, "prices": prices or None})
    except ValidationError as error:
        for problem in error.errors():
            message = SETTING_FIELDS.get(str(problem["loc"][0]), str(problem["msg"]))
            if message not in errors:
                errors.append(message)
        return None, errors
    if errors:
        return None, errors
    return settings, []


def settings_page_response(
    request: Request,
    form: dict[str, str],
    *,
    errors: list[str] | None = None,
    saved: bool = False,
    status_code: int = 200,
) -> Response:
    context = {"form": form, "errors": errors or [], "saved": saved}
    return page(request, "settings.html", context, status_code=status_code)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request, db: DbDep, _user: AnyUser, saved: Annotated[str | None, Query()] = None
) -> Response:
    """The business settings form."""
    form = settings_form(load_settings(db))
    return settings_page_response(request, form, saved=saved is not None)


@router.post("/settings", response_class=HTMLResponse)
async def save_settings_page(
    request: Request, db: DbDep, now: NowDep, user: OperationsUser
) -> Response:
    """Save the business settings. Refused if bookings from today on would no longer fit."""
    sent = await request.form()
    form = {key: str(sent.get(key, "")) for key in settings_form(EngineSettings())}
    body, errors = read_settings_form(form)
    if body is None:
        return settings_page_response(request, form, errors=errors, status_code=422)
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
        db.rollback()
        return settings_page_response(request, form, errors=[str(error)], status_code=409)
    return RedirectResponse("/settings?saved=1", status_code=303)
