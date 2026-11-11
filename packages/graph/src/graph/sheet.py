"""Reading the corrected review sheet back in (SPEC §5.4 one-off migration, step 4).

The owner opens the review sheet (see `graph.takeover`) in Excel, fills in missing headcounts and
ticket types, and saves it. `read_sheet` turns it back into rows, and `decide` says what
`import-outlook --review FILE --commit` does with each row:

- 'history'    past event (before the take-over date): stored as a PastBooking
- 'book'       future event with everything needed: booked through the normal enquiry check
- 'skip'       cancelled, or 'skip' written in the action column: nothing is stored
- 'over 100'   future group over 100 people: not booked; ops blocks the venue on the Day plan
- 'not ready'  something is missing or can't be read: nothing is stored, the reason is listed

The proposed action written by the dry run is ignored (except 'skip'): it is worked out again from
the corrected cells and the take-over date, which may be later than the dry run's.

Excel re-saves dates and times in the computer's own format, so both the sheet's own format
(2026-11-12, 08:30) and Excel's (12/11/2026, 8:30, 8:30:00) are read. Dates with '/' are
day/month/year, as on a Singapore computer.
"""

import csv
import re
from dataclasses import dataclass, replace

from engine.dates import is_valid_date
from engine.rules import GRID, TICKET_SHORT_NAMES, TICKET_TYPES, TicketType
from graph.ics import MINUTES_PER_DAY
from graph.takeover import MAX_PEOPLE

DECISION_HISTORY = "history"
DECISION_BOOK = "book"
DECISION_SKIP = "skip"
DECISION_OVER_100 = "over 100"
DECISION_NOT_READY = "not ready"

# Columns the commit needs. The others ('notes', 'people_found', ...) are only for reading.
NEEDED_COLUMNS = (
    "event_id",
    "date",
    "start",
    "end",
    "people",
    "adults",
    "children",
    "ticket",
    "client",
    "soft_block",
    "cancelled",
    "action",
    "ticket_found",
    "subject",
)

_ISO_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_EXCEL_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})(?::00)?(?:\s*\+(\d+)d)?$")


class SheetError(Exception):
    """The sheet as a whole can't be used (e.g. a column is missing)."""


@dataclass(frozen=True)
class SheetRow:
    """One row of the corrected sheet. `line` is its line number in Excel (the header is line 1).

    Cells that can't be read are None and explained in `problems`. `start` is None for an
    all-day event (or an empty cell); `end` may be over 1440 for an event ending the next day.
    """

    line: int
    event_id: str
    date: str
    start: int | None
    end: int | None
    people: int | None
    adults: int | None
    children: int | None
    ticket: TicketType | None
    client: str
    soft_block: bool
    cancelled: bool
    action: str
    ticket_found: str
    subject: str
    problems: tuple[str, ...] = ()


@dataclass(frozen=True)
class Decision:
    """What the commit does with one row. For 'book', `adults` + `children` is the headcount."""

    kind: str
    reason: str = ""
    adults: int = 0
    children: int = 0


# --- reading cells ---------------------------------------------------------------------------


def read_date(text: str) -> str | None:
    """'2026-11-12' or '12/11/2026' (day/month/year) -> '2026-11-12'; None if not a real date."""
    text = text.strip()
    iso = _ISO_DATE.match(text)
    excel = _EXCEL_DATE.match(text)
    if iso:
        year, month, day = iso.group(1), iso.group(2), iso.group(3)
    elif excel:
        day, month, year = excel.group(1), excel.group(2), excel.group(3)
    else:
        return None
    date = f"{year}-{int(month):02d}-{int(day):02d}"
    if not is_valid_date(date):
        return None
    return date


def read_time(text: str) -> int | None:
    """'08:30', '8:30', '8:30:00' -> 510; '01:00+1d' -> 1500. None if it isn't a time."""
    match = _TIME.match(text.strip())
    if match is None:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        return None
    days = int(match.group(3)) if match.group(3) else 0
    return days * MINUTES_PER_DAY + hours * 60 + minutes


def read_count(text: str) -> int | None:
    """'18' -> 18. Raises ValueError for anything that isn't a whole number of 0 or more."""
    text = text.strip()
    if not text:
        return None
    if not text.isdigit():
        raise ValueError(text)
    return int(text)


def read_ticket(text: str) -> TicketType | None:
    """A ticket code ('PREMIUM_CT', any case) or short name ('PHE+CT') -> the code.
    Raises ValueError if it is neither; '' -> None."""
    cleaned = text.strip().upper()
    if not cleaned:
        return None
    for ticket in TICKET_TYPES:
        if cleaned == ticket or cleaned == TICKET_SHORT_NAMES[ticket].upper():
            return ticket
    raise ValueError(text)


def read_yes_no(text: str) -> bool:
    """'yes' / 'TRUE' / '1' -> True; 'no', 'FALSE', '0' or empty -> False. Raises ValueError."""
    cleaned = text.strip().lower()
    if cleaned in ("yes", "y", "true", "1"):
        return True
    if cleaned in ("no", "n", "false", "0", ""):
        return False
    raise ValueError(text)


# --- reading the sheet -----------------------------------------------------------------------


def _row(line: int, cells: dict[str, str]) -> SheetRow:
    """One sheet row, with every cell that can't be read listed in `problems`."""
    problems: list[str] = []

    def cell(column: str) -> str:
        return (cells.get(column) or "").strip()

    date = read_date(cell("date"))
    if date is None:
        problems.append(f"date {cell('date')!r} is not a date like 2026-11-12")

    times: dict[str, int | None] = {}
    for column in ("start", "end"):
        times[column] = read_time(cell(column)) if cell(column) else None
        if cell(column) and times[column] is None:
            problems.append(f"{column} {cell(column)!r} is not a time like 10:30")

    counts: dict[str, int | None] = {}
    for column in ("people", "adults", "children"):
        try:
            counts[column] = read_count(cell(column))
        except ValueError:
            counts[column] = None
            problems.append(f"{column} {cell(column)!r} is not a whole number")

    try:
        ticket = read_ticket(cell("ticket"))
    except ValueError:
        ticket = None
        codes = ", ".join(TICKET_TYPES)
        problems.append(f"ticket {cell('ticket')!r} is not one of {codes}")

    flags: dict[str, bool] = {}
    for column in ("soft_block", "cancelled"):
        try:
            flags[column] = read_yes_no(cell(column))
        except ValueError:
            flags[column] = False
            problems.append(f"{column} {cell(column)!r} should be yes or no")

    if not cell("event_id"):
        problems.append("event_id is empty (it links the row to its Outlook event)")

    return SheetRow(
        line=line,
        event_id=cell("event_id"),
        date=date or "",
        start=times["start"],
        end=times["end"],
        people=counts["people"],
        adults=counts["adults"],
        children=counts["children"],
        ticket=ticket,
        client=cell("client"),
        soft_block=flags["soft_block"],
        cancelled=flags["cancelled"],
        action=cell("action"),
        ticket_found=cell("ticket_found"),
        subject=cell("subject"),
        problems=tuple(problems),
    )


def read_sheet(text: str) -> list[SheetRow]:
    """The rows of a corrected review sheet (CSV text). Empty lines are left out.

    Raises SheetError if a needed column is missing. A row whose event id already appeared
    higher up gets a problem (each Outlook event can only be taken over once).
    """
    reader = csv.DictReader(text.splitlines())
    columns = [name.strip() for name in reader.fieldnames or []]
    missing = [name for name in NEEDED_COLUMNS if name not in columns]
    if missing:
        raise SheetError(
            f"the sheet has no {', '.join(missing)} column(s). "
            "Use the sheet written by the dry run (keep its header row)."
        )

    rows: list[SheetRow] = []
    seen: dict[str, int] = {}  # event id -> line where it first appeared
    for line, raw in enumerate(reader, start=2):
        cells = {(key or "").strip(): value for key, value in raw.items() if key is not None}
        if not any((value or "").strip() for value in cells.values()):
            continue  # an empty line, e.g. at the end of an Excel file
        row = _row(line, cells)
        if row.event_id in seen:
            duplicate = f"same event_id as line {seen[row.event_id]}"
            row = replace(row, problems=(*row.problems, duplicate))
        elif row.event_id:
            seen[row.event_id] = line
        rows.append(row)
    return rows


# --- deciding --------------------------------------------------------------------------------


def split_headcount(row: SheetRow) -> tuple[int, int] | str:
    """(adults, children) from the people / adults / children cells, or what is wrong.

    Only people: all adults. People and one of the two: the other is the rest. Adults and
    children without people: people is their sum.
    """
    people, adults, children = row.people, row.adults, row.children
    if people is None:
        if adults is None and children is None:
            return "no headcount: fill in people"
        return (adults or 0, children or 0)
    if adults is None and children is None:
        return (people, 0)
    if adults is None:
        adults = people - (children or 0)
    if children is None:
        children = people - adults
    if adults < 0 or children < 0 or adults + children != people:
        return (
            f"adults ({row.adults}) and children ({row.children}) don't add up to people ({people})"
        )
    return (adults, children)


def decide(row: SheetRow, today: str) -> Decision:
    """What the commit does with this row. `today` is the take-over date: rows before it are
    history. See the module docstring for the kinds of decision."""
    if row.action.lower().startswith("skip"):
        return Decision(DECISION_SKIP, "skip on the sheet")
    if row.problems:
        return Decision(DECISION_NOT_READY, "; ".join(row.problems))
    if row.date < today:
        return Decision(DECISION_HISTORY)
    if row.cancelled:
        return Decision(DECISION_SKIP, "cancelled")
    if row.start is None:
        return Decision(DECISION_NOT_READY, "no start time (all-day event): fill in start")
    if row.start % GRID:
        return Decision(DECISION_NOT_READY, "start is not on the half hour")
    headcount = split_headcount(row)
    if isinstance(headcount, str):
        return Decision(DECISION_NOT_READY, headcount)
    adults, children = headcount
    people = adults + children
    if people > MAX_PEOPLE:
        return Decision(
            DECISION_OVER_100,
            f"{people} people: not booked; block the venue with a blackout on the Day plan",
        )
    if people < 1:
        return Decision(DECISION_NOT_READY, "people must be at least 1")
    if row.ticket is None:
        return Decision(DECISION_NOT_READY, "no ticket type: fill in ticket")
    return Decision(DECISION_BOOK, adults=adults, children=children)


def ticket_source(row: SheetRow) -> str | None:
    """How a past row's ticket type was found, for PastBooking.type_source: 'subject', 'body',
    'client' (client map), 'review' (filled in on the sheet) or None (no ticket type)."""
    if row.ticket is None:
        return None
    if row.ticket_found.startswith("client map"):
        return "client"
    if row.ticket_found in ("subject", "body"):
        return row.ticket_found
    return "review"


def past_times(row: SheetRow) -> tuple[int, int]:
    """(start, end) for a PastBooking: an all-day event covers the whole day."""
    if row.start is None:
        return (0, MINUTES_PER_DAY)
    if row.end is None or row.end < row.start:
        return (row.start, row.start)
    return (row.start, row.end)
