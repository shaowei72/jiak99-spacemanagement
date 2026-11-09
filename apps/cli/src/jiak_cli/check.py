"""Try an enquiry against a day from a JSON file, without any screens.

    uv run check 2026-12-01 11:30 PREMIUM 10                 # busy-day.json + operations board
    uv run check 2026-12-01 11:30 PREMIUM 10 --board sales   # what can still be sold
    uv run check 2026-12-01 10:00 PREMIUM 10 --empty-day     # start from an empty day
    uv run check 2026-12-01 10:30 CLASSIC 4 --day apps/cli/examples/week.json --no-board
    uv run check 2026-12-01 10:30 CLASSIC 4 --children 2 --turnaround 15
    uv run check 2026-12-01 10:30 CHE+CT 4                   # short labels work too

By default the day comes from apps/cli/examples/busy-day.json (it only has sessions on 2026-12-01;
other dates are empty) and the operations board is printed:
  --board operations  zones A-D across, time down; each cell shows which group is in that zone
  --board sales       start times down, ticket types across; + = can still be sold (SPEC §4.5)

Day file format (any number of dates). A date maps either to a list of sessions, or to an object
when the day is closed or has blackouts. Keys starting with "_" are comments and are ignored:

    {"2026-12-01": [{"start": "09:00", "type": "PREMIUM", "adults": 25},
                    {"start": "09:00", "type": "PREMIUM_CT", "adults": 10, "route": "ABDC", "pinned": true}],
     "2026-12-02": {"blackouts": [{"start": "14:00", "end": "17:00"}], "sessions": [...]},
     "2026-12-03": {"closed": true}}
"""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from engine import (
    Blackout,
    DayState,
    Enquiry,
    EnquiryResult,
    Session,
    Settings,
    assign,
    availability,
    check_enquiry,
    dead_gaps,
)
from engine.evaluate import group_sizes, new_floating_sessions
from engine.rules import CLOSE, GRID, OPEN, TICKET_SHORT_NAMES, TICKET_TYPES, ZONES, TicketType
from engine.text import format_time, plural, slot_label, ticket_label
from engine.timeline import reset_minutes, route_visits

SINGAPORE = timezone(timedelta(hours=8))  # no daylight saving

ZONE_COLUMN_WIDTH = 12  # characters per zone on the operations board

# apps/cli/examples/busy-day.json, found from this file so it works from any folder.
# parents[0] = jiak_cli, [1] = src, [2] = apps/cli
DEFAULT_DAY_FILE = Path(__file__).resolve().parents[2] / "examples" / "busy-day.json"


def parse_time(text: str) -> int:
    """'10:30' -> 630."""
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


def parse_ticket_type(text: str) -> TicketType:
    """A ticket type as typed by a person -> its code.

    Accepts the code ('PREMIUM', 'classic-ct', 'Classic CT') or the short label ('PHE', 'che+ct').
    """
    cleaned = text.strip().upper()
    as_code = cleaned.replace("-", "_").replace(" ", "_")
    for code in TICKET_TYPES:
        if cleaned.replace(" ", "") == TICKET_SHORT_NAMES[code].upper() or as_code == code:
            return code
    choices = ", ".join(TICKET_SHORT_NAMES[code] for code in TICKET_TYPES)
    raise argparse.ArgumentTypeError(f"ticket type must be one of {choices}")


def load_days(path: Path) -> dict[str, DayState]:
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return {date: parse_day(date, value) for date, value in raw.items() if not date.startswith("_")}


def parse_day(date: str, value: list[dict[str, Any]] | dict[str, Any]) -> DayState:
    """One date from a day file: a list of sessions, or {"sessions", "closed", "blackouts"}."""
    details: dict[str, Any] = {"sessions": value} if isinstance(value, list) else value
    sessions = tuple(
        Session(
            id=f"{date}#{number}",
            date=date,
            start=parse_time(entry["start"]),
            ticket_type=parse_ticket_type(entry["type"]),
            adults=int(entry.get("adults", 0)),
            children=int(entry.get("children", 0)),
            route=entry.get("route"),
            pinned=bool(entry.get("pinned", False)),
        )
        for number, entry in enumerate(details.get("sessions", []), start=1)
    )
    blackouts = tuple(
        Blackout(parse_time(b["start"]), parse_time(b["end"])) for b in details.get("blackouts", [])
    )
    return DayState(
        date=date,
        sessions=sessions,
        closed=bool(details.get("closed", False)),
        blackouts=blackouts,
    )


def format_result(enquiry: Enquiry, result: EnquiryResult) -> str:
    lines = [
        f"Request: {enquiry.date} {slot_label(enquiry.start, enquiry.ticket_type)}, "
        f"{enquiry.adults} adults, {enquiry.children} children",
        f"Verdict: {result.verdict}",
        f"Reason:  {result.reason}",
    ]
    if result.alternatives:
        lines.append("Better options:")
        for number, alt in enumerate(result.alternatives, start=1):
            lines.append(
                f"  {number}. {alt.verdict:<13} {alt.date} {slot_label(alt.start, alt.ticket_type)}"
                f"  {alt.change_summary} - {alt.gain_summary}"
            )
    else:
        lines.append("Better options: none")
    return "\n".join(lines)


def format_operations_board(
    days: dict[str, DayState], settings: Settings, enquiry: Enquiry, fits: bool
) -> str:
    """Zones A-D across, time down: which group is in each zone in the engine's best plan.

    Groups are numbered as in the day file; each cell shows the group's number and ticket.
    If the request fits, it is added to the plan and shown as *. A list under the board gives
    each group's size and route. Routes not pinned may still change until ops allocates the day.
    """
    day = days.get(enquiry.date) or DayState(date=enquiry.date)
    title = f"Operations board for {enquiry.date}: routes as planned now"
    if day.closed:
        return f"{title}\n  Closed all day."

    new: tuple[Session, ...] = ()
    if fits:
        new = new_floating_sessions(enquiry, group_sizes(enquiry.headcount))
    sessions = day.sessions + new
    plan = assign(sessions, settings, blackouts=day.blackouts)
    if not plan.ok:
        return f"{title}\n  The groups in the day file don't all fit."
    marks = {s.id: str(number) for number, s in enumerate(day.sessions, start=1)}
    marks.update({s.id: "*" for s in new})

    # What each zone slot holds: (zone, slot start) -> text. Later entries win.
    cells: dict[tuple[str, int], str] = {}
    for blackout in day.blackouts:
        for zone in ZONES:
            for minute in range(OPEN, CLOSE, GRID):
                if blackout.start < minute + GRID and minute < blackout.end:
                    cells[(zone, minute)] = "blackout"
    for gap in dead_gaps(sessions, plan.route_of, settings, day.blackouts):
        for minute in range(gap.start, gap.end, GRID):
            cells[(gap.zone_id, minute)] = "-dead-"
    reset = reset_minutes(settings)
    for session in sessions:
        label = f"{marks[session.id]} {ticket_label(session.ticket_type)}"
        for visit in route_visits(session.start, session.ticket_type, plan.route_of[session.id]):
            for minute in range(visit.start, visit.end, GRID):
                cells[(visit.zone_id, minute)] = label
            for minute in range(visit.end, min(CLOSE, visit.end + reset), GRID):
                cells.setdefault((visit.zone_id, minute), "(reset)")

    width = ZONE_COLUMN_WIDTH
    header = "        " + "".join(f"{zone:<{width}}" for zone in ZONES)
    lines = [title, header.rstrip()]
    for minute in range(OPEN, CLOSE, GRID):
        row = "".join(f"{cells.get((zone, minute), '.'):<{width}}" for zone in ZONES)
        lines.append(f"  {format_time(minute)} " + row.rstrip())
    lines.append("  . = free   -dead- = no ticket but Tingkat can use it   (reset) = turnaround")
    lines.append("Groups:")
    for session in sessions:
        route = plan.route_of[session.id]
        pinned = ", pinned" if session.is_fixed else ""
        lines.append(
            f"  {marks[session.id]:>2} = {slot_label(session.start, session.ticket_type)}, "
            f"{plural(session.headcount, 'person', 'people')}, route {route}{pinned}"
        )
    lines.append("  Routes that aren't pinned may still change until ops allocates the day.")
    return "\n".join(lines)


def format_sales_board(
    days: dict[str, DayState], settings: Settings, enquiry: Enquiry, today: str
) -> str:
    """Start times down, ticket types across: can a group this size still be sold?"""
    board = availability(days, settings, enquiry.date, enquiry.headcount, today=today)
    marks = {"available": "+", "full": "x", "not_offered": " "}
    lines = [f"Sales board for {enquiry.headcount} people (+ = available, x = full)"]
    labels = [ticket_label(code) for code in TICKET_TYPES]
    width = max(len(label) for label in labels) + 2
    lines.append(("        " + " ".join(f"{label:^{width}}" for label in labels)).rstrip())
    for start in sorted({slot.start for slot in board}):
        row = [marks[s.status] for s in board if s.start == start]
        line = f"  {format_time(start)} " + " ".join(f"{mark:^{width}}" for mark in row)
        lines.append(line.rstrip())
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check", description="Check a sales enquiry.")
    parser.add_argument("date", help="YYYY-MM-DD")
    parser.add_argument("start", type=parse_time, help="HH:MM, e.g. 10:30")
    parser.add_argument(
        "type",
        type=parse_ticket_type,
        help=", ".join(f"{TICKET_SHORT_NAMES[code]} ({code})" for code in TICKET_TYPES),
    )
    parser.add_argument("adults", type=int)
    parser.add_argument("--children", type=int, default=0)
    # Use either a day file or an empty day, not both (argparse refuses the combination).
    day_options = parser.add_mutually_exclusive_group()
    day_options.add_argument(
        "--day",
        type=Path,
        default=DEFAULT_DAY_FILE,
        help="JSON file with existing sessions (default: examples/busy-day.json)",
    )
    day_options.add_argument(
        "--empty-day", action="store_true", help="start from an empty day instead of a day file"
    )
    parser.add_argument("--turnaround", type=int, default=0, help="zone reset minutes")
    parser.add_argument("--warn-below", type=int, default=0, help="small-group warning size")
    parser.add_argument("--today", help="pretend today is YYYY-MM-DD (default: real date)")
    parser.add_argument(
        "--board",
        choices=["operations", "sales"],
        default="operations",
        help="operations: zones A-D over the day (default); "
        "sales: which start times and ticket types can still be sold",
    )
    parser.add_argument(
        "--no-board", dest="board", action="store_const", const=None, help="print no board"
    )
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # the messages use "·" and "–"
    days = {} if args.empty_day else load_days(args.day)
    settings = Settings(turnaround_minutes=args.turnaround, small_group_warn_below=args.warn_below)
    today = args.today or datetime.now(SINGAPORE).date().isoformat()
    enquiry = Enquiry(args.date, args.start, args.type, args.adults, args.children)

    result = check_enquiry(days, settings, enquiry, today=today)
    print(format_result(enquiry, result))
    if args.board == "operations":
        print()
        fits = result.verdict != "NOT_POSSIBLE"
        print(format_operations_board(days, settings, enquiry, fits))
    elif args.board == "sales":
        print()
        print(format_sales_board(days, settings, enquiry, today))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
