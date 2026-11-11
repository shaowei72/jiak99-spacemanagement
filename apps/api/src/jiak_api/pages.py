"""Web pages (SPEC §5.1, Milestone 3): server-rendered HTML with Jinja2 templates + HTMX.

The pages use the same building blocks as the JSON API in `routes.py`; they just answer with
HTML. With HTMX, a form sends the request in the background and only the result part of the
page is replaced. Without JavaScript the same form still works: the whole page is reloaded.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from engine import is_valid_date
from engine.dates import weekday
from engine.rules import TICKET_NAMES, TICKET_SHORT_NAMES, TICKET_TYPES, TicketType
from engine.text import duration_label, format_time
from jiak_api import bookings, models, overview
from jiak_api.auth import User
from jiak_api.clock import Now
from jiak_api.outlook_sync import OutlookDep
from jiak_api.routes import AnyUser, DatabaseDep, DbDep, NowDep, SalesUser, to_enquiry
from jiak_api.schemas import BookingOut, CheckOut, EnquiryIn

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")

router = APIRouter(include_in_schema=False)  # pages don't belong in the /docs API list

# What each verdict looks like on the page: (headline, CSS class for the colour).
VERDICT_LOOKS = {
    "ACCEPT_GOOD": ("Good fit", "good"),
    "ACCEPT_COSTLY": ("Fits, at a cost", "costly"),
    "NOT_POSSIBLE": ("Not possible", "no"),
}

# Plain-language messages for form mistakes, by field name.
FIELD_ERRORS = {
    "date": "Pick a date.",
    "start": "Pick a start time, e.g. 10:30.",
    "type": "Pick a ticket type.",
    "adults": "Adults must be a whole number, 0 or more.",
    "children": "Children must be a whole number, 0 or more.",
}

EMPTY_CUSTOMER = {"name": "", "phone": "", "email": "", "company": "", "notes": ""}


def ticket_choices() -> list[dict[str, str]]:
    """The ticket types for the form's drop-down, e.g. 'PHE – Premium Hawker Experience (2 h)'."""
    choices = []
    for code in TICKET_TYPES:
        label = f"{TICKET_SHORT_NAMES[code]} – {TICKET_NAMES[code]} ({duration_label(code)})"
        choices.append({"code": code, "label": label})
    return choices


CUSTOMER_LABELS = {"name": "name", "phone": "phone", "email": "email", "company": "company"}


def customer_errors(fields: dict[str, str]) -> list[str]:
    """What is wrong with the customer fields of a form: no name, or text longer than the
    database keeps (models.CUSTOMER_LIMITS)."""
    errors: list[str] = []
    if not fields["name"]:
        errors.append("Enter the customer's name.")
    for field, longest in models.CUSTOMER_LIMITS.items():
        if len(fields[field]) > longest:
            errors.append(
                f"The customer's {CUSTOMER_LABELS[field]} can be at most {longest} characters."
            )
    return errors


def form_errors(form: dict[str, str]) -> tuple[EnquiryIn | None, list[str]]:
    """Check the form. Returns the enquiry if it's valid, otherwise the error messages."""
    try:
        enquiry = EnquiryIn.model_validate(form)
    except ValidationError as error:
        messages = []
        for problem in error.errors():
            field = str(problem["loc"][0])
            message = FIELD_ERRORS.get(field, f"{field}: {problem['msg']}")
            if message not in messages:
                messages.append(message)
        return None, messages
    if not is_valid_date(enquiry.date):
        return None, [FIELD_ERRORS["date"]]
    if enquiry.adults + enquiry.children == 0:
        return None, ["Enter at least one person."]
    return enquiry, []


def enquiry_form(
    today: str,
    date: str | None,
    start: str | None,
    type: str | None,
    adults: str | None,
    children: str | None,
) -> dict[str, str]:
    """The enquiry fields as the form shows them, with defaults for anything missing."""
    return {
        "date": date or today,
        "start": start or "",
        "type": type or "PREMIUM",
        "adults": adults or "",
        "children": children or "0",
    }


def enquiry_query(date: str, start: str, ticket_type: str, adults: str, children: str) -> str:
    """The enquiry as a link's query, e.g. 'date=2026-12-01&start=10:30&type=PREMIUM&...'."""
    query = {
        "date": date,
        "start": start,
        "type": ticket_type,
        "adults": adults,
        "children": children,
    }
    return urlencode(query, safe=":")  # keep '10:30' readable in the address bar


def book_url(date: str, start: str, ticket_type: str, adults: str, children: str) -> str:
    """The Book button's link for one option: the customer form."""
    return "/enquiry/book?" + enquiry_query(date, start, ticket_type, adults, children)


def check_url(date: str, start: str, ticket_type: str, adults: str, children: str) -> str:
    """The link back to Check and book for one option."""
    return "/enquiry?" + enquiry_query(date, start, ticket_type, adults, children)


def people(adults: int, children: int) -> str:
    """'10 adults, 2 children', '1 adult', '1 adult, 1 child'."""
    text = f"{adults} adult" if adults == 1 else f"{adults} adults"
    if children == 1:
        text += ", 1 child"
    elif children > 1:
        text += f", {children} children"
    return text


# Helpers the templates can call.
templates.env.globals["book_url"] = book_url
templates.env.globals["check_url"] = check_url
templates.env.globals["people"] = people


@dataclass(frozen=True)
class TicketTimes:
    """The start times that still fit the group, for one ticket type."""

    ticket_type: TicketType
    starts: list[str]  # e.g. ['16:00', '16:30']; empty = nothing fits on this day
    asked: bool  # the ticket the customer asked for


@dataclass(frozen=True)
class TimesPanel:
    """Every start time that still fits the group on the asked date, per ticket type.

    The asked ticket comes first. This is the availability board, cut down to what can be sold.
    """

    date: str
    weekday: str
    people: int
    closed: bool
    tickets: list[TicketTimes]


def times_panel(db: Session, enquiry: EnquiryIn, now: Now) -> TimesPanel:
    """The times panel for an enquiry's date and group size."""
    people = enquiry.adults + enquiry.children
    board = overview.board_for(db, enquiry.date, people, now)
    starts: dict[str, list[str]] = {code: [] for code in TICKET_TYPES}
    for slot in board.slots:
        if slot.status == "available":
            starts[slot.ticket_type].append(format_time(slot.start))
    order = [enquiry.ticket_type] + [code for code in TICKET_TYPES if code != enquiry.ticket_type]
    tickets = [
        TicketTimes(ticket_type=code, starts=starts[code], asked=code == enquiry.ticket_type)
        for code in order
    ]
    return TimesPanel(
        date=enquiry.date,
        weekday=weekday(enquiry.date),
        people=people,
        closed=board.closed,
        tickets=tickets,
    )


def is_htmx(request: Request) -> bool:
    """True if HTMX sent the request: it wants only a part of the page back."""
    return request.headers.get("HX-Request") == "true"


def render(
    request: Request, part: str, context: dict[str, Any], status_code: int = 200
) -> Response:
    """Answer with just the template `part` for HTMX, or the whole Check and book page with
    `part` shown under the form.

    Never cached: every answer depends on what is booked right now.
    """
    full_context = {
        **context,
        "part": part,
        "verdict_looks": VERDICT_LOOKS,
        "short_names": TICKET_SHORT_NAMES,
        "ticket_names": TICKET_NAMES,
        "ticket_choices": ticket_choices(),
    }
    template = part if is_htmx(request) else "enquiry.html"
    response = templates.TemplateResponse(request, template, full_context, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/")
def home() -> RedirectResponse:
    """The start page is Check and book."""
    return RedirectResponse("/enquiry")


@router.get("/enquiry", response_class=HTMLResponse)
def enquiry_page(
    request: Request,
    db: DbDep,
    now: NowDep,
    _user: SalesUser,
    date: Annotated[str | None, Query()] = None,
    start: Annotated[str | None, Query()] = None,
    type: Annotated[str | None, Query()] = None,
    adults: Annotated[str | None, Query()] = None,
    children: Annotated[str | None, Query()] = None,
) -> Response:
    """The Check and book page. With the form filled in, it shows the verdict on the asked
    time and every other time that fits the group that day. Without a start time it shows
    only the times ("what do you have on Saturday for 40?").
    """
    form = enquiry_form(now.today, date, start, type, adults, children)
    errors: list[str] = []
    check: CheckOut | None = None
    times: TimesPanel | None = None
    if date is not None:  # the form was sent
        any_time = not form["start"]
        if any_time:
            # Check the other fields with a stand-in time; the time itself isn't used.
            enquiry, errors = form_errors({**form, "start": "09:00"})
        else:
            enquiry, errors = form_errors(form)
        if enquiry is not None:
            times = times_panel(db, enquiry, now)
            if not any_time:
                check = CheckOut.from_engine(bookings.check(db, to_enquiry(enquiry), now))
    context: dict[str, Any] = {"form": form, "errors": errors, "check": check, "times": times}
    return render(request, "_check_result.html", context)


@router.get("/enquiry/book", response_class=HTMLResponse)
def book_form_page(
    request: Request,
    db: DbDep,
    now: NowDep,
    _user: SalesUser,
    date: Annotated[str | None, Query()] = None,
    start: Annotated[str | None, Query()] = None,
    type: Annotated[str | None, Query()] = None,
    adults: Annotated[str | None, Query()] = None,
    children: Annotated[str | None, Query()] = None,
) -> Response:
    """After a Book button: the option is checked again, then the customer form is shown.

    If the option doesn't fit any more (the result on screen was out of date), the page says
    "No longer fits" with fresh alternatives instead of showing the form, just like a Book
    press on an open form that loses the race.
    """
    form = enquiry_form(now.today, date, start, type, adults, children)
    enquiry, errors = form_errors(form)
    if enquiry is None:
        return render(request, "_check_result.html", {"form": form, "errors": errors})
    check = CheckOut.from_engine(bookings.check(db, to_enquiry(enquiry), now))
    context: dict[str, Any]
    if check.verdict == "NOT_POSSIBLE":
        context = {"form": form, "check": check, "no_longer_fits": True}
        return render(request, "_check_result.html", context)
    context = {
        "form": form,
        "check": check,
        "customer": EMPTY_CUSTOMER,
        "errors": [],
    }
    return render(request, "_book_form.html", context)


@router.post("/enquiry/book", response_class=HTMLResponse)
def book(
    request: Request,
    database: DatabaseDep,
    db: DbDep,
    now: NowDep,
    user: SalesUser,
    outlook: OutlookDep,
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
    """Make the booking. The answer is one of:

    - booked: the confirmation (HTMX), or a redirect to the booking's page (plain form);
    - form mistakes (422): the form again, with what to fix;
    - no longer fits (409): why, and fresh alternatives to book instead.
    """
    form = enquiry_form(now.today, date, start, type, adults, children)
    customer = {
        "name": name.strip(),
        "phone": phone.strip(),
        "email": email.strip(),
        "company": company.strip(),
        "notes": notes.strip(),
    }
    enquiry, errors = form_errors(form)
    errors.extend(customer_errors(customer))
    if enquiry is None or errors:
        context: dict[str, Any] = {
            "form": form,
            "check": None,
            "customer": customer,
            "errors": errors,
        }
        return render(request, "_book_form.html", context, status_code=422)

    details = bookings.CustomerDetails(
        name=customer["name"],
        phone=customer["phone"] or None,  # empty field -> not given
        email=customer["email"] or None,
        company=customer["company"] or None,
    )
    try:
        booking = bookings.create_booking(
            database,
            db,
            to_enquiry(enquiry),
            details,
            notes=customer["notes"],
            actor=user.name,
            now=now,
        )
    except bookings.NoLongerFits as error:
        check = CheckOut.from_engine(error.result)
        context = {"form": form, "check": check, "no_longer_fits": True}
        return render(request, "_check_result.html", context, status_code=409)
    outlook.dates_changed(booking.date)

    booking_url = f"/bookings/{booking.id}"
    if not is_htmx(request):
        # Post/Redirect/Get: reloading the next page can't book the same group twice.
        return RedirectResponse(booking_url, status_code=303)
    context = {
        "form": form,
        "booking": BookingOut.from_row(booking),
        "just_booked": True,
    }
    response = render(request, "_booking.html", context)
    response.headers["HX-Push-Url"] = booking_url  # the address bar shows the booking
    return response


# What the booking page says after a change, by the `done` query value.
DONE_MESSAGES = {
    "changed": "Booking changed.",
    "cancelled": "Booking cancelled. Its zone slots are free again.",
    "no_show": "Marked as no-show. Its zone slots are free again.",
}


@router.get("/bookings/{booking_id}", response_class=HTMLResponse)
def booking_page(
    request: Request,
    booking_id: str,
    db: DbDep,
    now: NowDep,
    user: AnyUser,
    done: Annotated[str | None, Query()] = None,
) -> Response:
    """One booking: who, when, which ticket, how many, and what can be done with it."""
    row = db.get(models.Booking, booking_id)
    if row is None:
        form = enquiry_form(now.today, None, None, None, None, None)
        return render(request, "_not_found.html", {"form": form}, status_code=404)
    return booking_view(request, row, now, user, done=DONE_MESSAGES.get(done or ""))


def booking_view(
    request: Request,
    row: models.Booking,
    now: Now,
    user: User,
    *,
    done: str | None = None,
    errors: list[str] | None = None,
    status_code: int = 200,
) -> Response:
    """The booking's page, optionally with a 'done' message or error messages."""
    booking = BookingOut.from_row(row)
    form = enquiry_form(
        now.today,
        booking.date,
        booking.start,
        booking.ticket_type,
        str(booking.adults),
        str(booking.children),
    )
    confirmed = booking.status == "confirmed"
    context = {
        "form": form,
        "booking": booking,
        "done": done,
        "errors": errors or [],
        "can_change": confirmed and user.can("Sales"),
        "can_no_show": confirmed and user.can("Operations") and bookings.has_started(row, now),
    }
    return render(request, "_booking.html", context, status_code=status_code)
