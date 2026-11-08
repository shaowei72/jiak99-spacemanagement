"""Web pages (SPEC §5.1, Milestone 3): server-rendered HTML with Jinja2 templates + HTMX.

The pages use the same building blocks as the JSON API in `routes.py`; they just answer with
HTML. With HTMX, a form sends the request in the background and only the result part of the
page is replaced. Without JavaScript the same form still works: the whole page is reloaded.
"""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from engine import is_valid_date
from engine.rules import TICKET_NAMES, TICKET_SHORT_NAMES, TICKET_TYPES
from engine.text import duration_label
from jiak_api import bookings
from jiak_api.routes import DbDep, NowDep, SalesUser, to_enquiry
from jiak_api.schemas import CheckOut, EnquiryIn

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


def ticket_choices() -> list[dict[str, str]]:
    """The ticket types for the form's drop-down, e.g. 'PHE – Premium Hawker Experience (2 h)'."""
    choices = []
    for code in TICKET_TYPES:
        label = f"{TICKET_SHORT_NAMES[code]} – {TICKET_NAMES[code]} ({duration_label(code)})"
        choices.append({"code": code, "label": label})
    return choices


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


@router.get("/")
def home() -> RedirectResponse:
    """The start page is the Enquiry check."""
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
    """The Enquiry check page. With the form filled in, it also shows the verdict.

    An HTMX request (header HX-Request) gets only the result part, not the whole page.
    """
    submitted = date is not None
    form = {
        "date": date or now.today,
        "start": start or "",
        "type": type or "PREMIUM",
        "adults": adults or "",
        "children": children or "0",
    }
    errors: list[str] = []
    check: CheckOut | None = None
    if submitted:
        enquiry, errors = form_errors(form)
        if enquiry is not None:
            check = CheckOut.from_engine(bookings.check(db, to_enquiry(enquiry), now))

    context = {
        "form": form,
        "errors": errors,
        "check": check,
        "verdict_looks": VERDICT_LOOKS,
        "short_names": TICKET_SHORT_NAMES,
        "ticket_choices": ticket_choices(),
    }
    is_htmx = request.headers.get("HX-Request") == "true"
    template = "_check_result.html" if is_htmx else "enquiry.html"
    response = templates.TemplateResponse(request, template, context)
    # Like the JSON check: the answer changes as soon as someone books, so never reuse it.
    response.headers["Cache-Control"] = "no-store"
    return response
