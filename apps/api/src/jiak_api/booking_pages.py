"""Bookings pages: the list of bookings, and edit / cancel / no-show for one booking.

The flow is Check and book -> Bookings -> Day plan: sales finds a booking here when the customer
calls back, and changes or cancels it. A change of date, time, ticket or headcount is checked
again like a new booking (same date lock, same rules, in `bookings.py`).

Plain HTML forms: after a successful change the browser goes to the booking's page
(Post/Redirect/Get); if the change is refused, the form is shown again with the reason.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from engine import is_valid_date
from engine.dates import weekday
from jiak_api import bookings, models
from jiak_api.auth import User
from jiak_api.clock import Now
from jiak_api.ops_pages import page
from jiak_api.pages import booking_view, form_errors, people, ticket_choices
from jiak_api.routes import (
    AnyUser,
    DatabaseDep,
    DbDep,
    NowDep,
    OperationsUser,
    SalesUser,
    to_enquiry,
)
from jiak_api.schemas import BookingOut, CheckOut

router = APIRouter(include_in_schema=False)

MAX_LIST_ROWS = 500  # more than a busy month; the filters narrow it down

STATUS_CHOICES = {
    "confirmed": "Confirmed",
    "all": "All (incl. cancelled and no-shows)",
}


# --- the list --------------------------------------------------------------------------------


def find_bookings(
    db: Session, first: str, last: str | None, name: str, status: str
) -> list[models.Booking]:
    """Bookings from `first` to `last` (inclusive; None = no end), oldest first.

    `name` matches part of the customer's name or company, in any case.
    `status` is 'confirmed' or 'all'.
    """
    query = (
        select(models.Booking)
        .join(models.Booking.customer)
        .where(models.Booking.date >= first)
        .options(selectinload(models.Booking.customer), selectinload(models.Booking.sessions))
        .order_by(models.Booking.date, models.Booking.start, models.Booking.created_at)
        .limit(MAX_LIST_ROWS)
    )
    if last is not None:
        query = query.where(models.Booking.date <= last)
    if status == "confirmed":
        query = query.where(models.Booking.status == "confirmed")
    if name:
        pattern = f"%{name.lower()}%"
        query = query.where(
            or_(
                func.lower(models.Customer.name).like(pattern),
                func.lower(models.Customer.company).like(pattern),
            )
        )
    return list(db.scalars(query))


@router.get("/bookings", response_class=HTMLResponse)
def bookings_list_page(
    request: Request,
    db: DbDep,
    now: NowDep,
    _user: AnyUser,
    first: Annotated[str | None, Query(alias="from")] = None,
    last: Annotated[str | None, Query(alias="to")] = None,
    name: Annotated[str, Query()] = "",
    status: Annotated[str, Query()] = "confirmed",
) -> Response:
    """The bookings from today on (or the dates chosen), grouped by date."""
    errors = []
    if first is None or first == "":
        first = now.today
    elif not is_valid_date(first):
        errors.append("'From' must be a real date.")
        first = now.today
    if last == "":
        last = None
    elif last is not None and not is_valid_date(last):
        errors.append("'To' must be a real date.")
        last = None
    if status not in STATUS_CHOICES:
        status = "confirmed"
    name = name.strip()

    rows = find_bookings(db, first, last, name, status)
    days: list[dict[str, Any]] = []  # [{"date", "weekday", "bookings": [...]}], in date order
    for row in rows:
        if not days or days[-1]["date"] != row.date:
            days.append({"date": row.date, "weekday": weekday(row.date), "bookings": []})
        days[-1]["bookings"].append(BookingOut.from_row(row))
    context = {
        "filters": {"from": first, "to": last or "", "name": name, "status": status},
        "status_choices": STATUS_CHOICES,
        "days": days,
        "count": len(rows),
        "too_many": len(rows) == MAX_LIST_ROWS,
        "errors": errors,
    }
    return page(request, "bookings.html", context)


# --- edit ------------------------------------------------------------------------------------


def edit_form_of(booking: BookingOut) -> dict[str, str]:
    """The edit form filled in with the booking as it is now."""
    return {
        "date": booking.date,
        "start": booking.start,
        "type": booking.ticket_type,
        "adults": str(booking.adults),
        "children": str(booking.children),
        "name": booking.customer.name,
        "phone": booking.customer.phone or "",
        "email": booking.customer.email or "",
        "company": booking.customer.company or "",
        "notes": booking.notes,
    }


def edit_page(
    request: Request,
    booking: BookingOut,
    form: dict[str, str],
    *,
    errors: list[str] | None = None,
    check: CheckOut | None = None,
    status_code: int = 200,
) -> Response:
    """The edit form for `booking`, with `form` values, error messages or a refused check."""
    context = {
        "booking": booking,
        "form": form,
        "errors": errors or [],
        "check": check,
        "ticket_choices": ticket_choices(),
        "people": people,
    }
    return page(request, "booking_edit.html", context, status_code=status_code)


def not_found(request: Request) -> Response:
    return page(request, "booking_missing.html", {}, status_code=404)


@router.get("/bookings/{booking_id}/edit", response_class=HTMLResponse)
def edit_booking_page(
    request: Request,
    booking_id: str,
    db: DbDep,
    _user: SalesUser,
    date: Annotated[str | None, Query()] = None,
    start: Annotated[str | None, Query()] = None,
    type: Annotated[str | None, Query()] = None,
    adults: Annotated[str | None, Query()] = None,
    children: Annotated[str | None, Query()] = None,
) -> Response:
    """The edit form. The slot and headcount may come from an alternative's "Use this"."""
    row = db.get(models.Booking, booking_id)
    if row is None:
        return not_found(request)
    booking = BookingOut.from_row(row)
    form = edit_form_of(booking)
    if date:
        form["date"] = date
    if start:
        form["start"] = start
    if type:
        form["type"] = type
    if adults:
        form["adults"] = adults
    if children:
        form["children"] = children
    return edit_page(request, booking, form)


@router.post("/bookings/{booking_id}/edit", response_class=HTMLResponse)
def save_booking_edit(
    request: Request,
    booking_id: str,
    database: DatabaseDep,
    db: DbDep,
    now: NowDep,
    user: SalesUser,
    date: Annotated[str, Form()] = "",
    start: Annotated[str, Form()] = "",
    type: Annotated[str, Form()] = "",
    adults: Annotated[str, Form()] = "",
    children: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    phone: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    company: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
) -> Response:
    """Save the changes. A new slot or headcount is re-checked; if it doesn't fit, the
    booking stays as it was and the page shows why, with alternatives."""
    row = db.get(models.Booking, booking_id)
    if row is None:
        return not_found(request)
    booking = BookingOut.from_row(row)
    form = {
        "date": date.strip(),
        "start": start.strip(),
        "type": type.strip(),
        "adults": adults.strip(),
        "children": children.strip() or "0",
        "name": name.strip(),
        "phone": phone.strip(),
        "email": email.strip(),
        "company": company.strip(),
        "notes": notes.strip(),
    }
    enquiry, errors = form_errors(form)
    if not form["name"]:
        errors.append("Enter the customer's name.")
    if enquiry is None or errors:
        return edit_page(request, booking, form, errors=errors, status_code=422)

    wanted = to_enquiry(enquiry)
    changes = bookings.BookingChanges(
        date=wanted.date,
        start=wanted.start,
        ticket_type=wanted.ticket_type,
        adults=wanted.adults,
        children=wanted.children,
        notes=form["notes"],
        customer=bookings.CustomerDetails(
            name=form["name"],
            phone=form["phone"] or None,  # empty field -> not given
            email=form["email"] or None,
            company=form["company"] or None,
        ),
    )
    try:
        bookings.edit_booking(database, db, booking_id, changes, actor=user.name, now=now)
    except bookings.NoLongerFits as error:
        db.rollback()
        check = CheckOut.from_engine(error.result)
        return edit_page(request, booking, form, check=check, status_code=409)
    except bookings.BookingNotChangeable as error:
        db.rollback()
        return edit_page(request, booking, form, errors=[str(error)], status_code=409)
    return RedirectResponse(f"/bookings/{booking_id}?done=changed", status_code=303)


# --- cancel and no-show ----------------------------------------------------------------------


@router.get("/bookings/{booking_id}/cancel", response_class=HTMLResponse)
def cancel_booking_page(request: Request, booking_id: str, db: DbDep, _user: SalesUser) -> Response:
    """'Are you sure?' before cancelling."""
    row = db.get(models.Booking, booking_id)
    if row is None:
        return not_found(request)
    context = {"booking": BookingOut.from_row(row), "people": people, "errors": []}
    return page(request, "booking_cancel.html", context)


@router.post("/bookings/{booking_id}/cancel", response_class=HTMLResponse)
def cancel_booking(
    request: Request,
    booking_id: str,
    database: DatabaseDep,
    db: DbDep,
    now: NowDep,
    user: SalesUser,
) -> Response:
    """Cancel the booking; its zones become free again."""
    try:
        bookings.cancel_booking(database, db, booking_id, actor=user.name)
    except bookings.BookingNotFound:
        return not_found(request)
    except bookings.BookingNotChangeable as error:
        db.rollback()
        return refused(request, db, now, user, booking_id, str(error))
    return RedirectResponse(f"/bookings/{booking_id}?done=cancelled", status_code=303)


@router.post("/bookings/{booking_id}/no-show", response_class=HTMLResponse)
def no_show(
    request: Request,
    booking_id: str,
    database: DatabaseDep,
    db: DbDep,
    now: NowDep,
    user: OperationsUser,
) -> Response:
    """The group didn't come (only after the start time). Its zones become free again."""
    try:
        bookings.mark_no_show(database, db, booking_id, actor=user.name, now=now)
    except bookings.BookingNotFound:
        return not_found(request)
    except bookings.BookingNotChangeable as error:
        db.rollback()
        return refused(request, db, now, user, booking_id, str(error))
    return RedirectResponse(f"/bookings/{booking_id}?done=no_show", status_code=303)


def refused(
    request: Request, db: Session, now: Now, user: User, booking_id: str, message: str
) -> Response:
    """The cancel / no-show was refused: show the booking's page with the reason."""
    row = db.get(models.Booking, booking_id, populate_existing=True)
    if row is None:
        return not_found(request)
    return booking_view(request, row, now, user, errors=[message], status_code=409)
