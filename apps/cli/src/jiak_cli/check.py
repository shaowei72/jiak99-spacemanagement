"""Try an enquiry against a day from a JSON file, without any screens.

    uv run check 2026-12-01 11:30 PREMIUM 10              # busy-day.json + board (the defaults)
    uv run check 2026-12-01 10:00 PREMIUM 10 --empty-day  # start from an empty day
    uv run check 2026-12-01 10:30 CLASSIC 4 --day apps/cli/examples/week.json --no-board
    uv run check 2026-12-01 10:30 CLASSIC 4 --children 2 --turnaround 15

By default the day comes from apps/cli/examples/busy-day.json (it only has sessions on 2026-12-01;
other dates are empty) and the availability board is printed.

Day file format (any number of dates). A date maps either to a list of sessions, or to an object
when the day is closed or has blackouts. Keys starting with "_" are comments and are ignored:

    {"2026-12-01": [{"start": "09:00", "type": "PREMIUM", "adults": 25},
                    {"start": "09:00", "type": "PREMIUM_CT", "adults": 10, "zone": "A", "pinned": true}],
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
    availability,
    check_enquiry,
)
from engine.rules import TICKET_TYPES, TicketType
from engine.text import format_time, slot_label

SINGAPORE = timezone(timedelta(hours=8))  # no daylight saving

# apps/cli/examples/busy-day.json, found from this file so it works from any folder.
# parents[0] = jiak_cli, [1] = src, [2] = apps/cli
DEFAULT_DAY_FILE = Path(__file__).resolve().parents[2] / "examples" / "busy-day.json"


def parse_time(text: str) -> int:
    """'10:30' -> 630."""
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


def parse_ticket_type(text: str) -> TicketType:
    """'premium', 'classic-ct' or 'Classic CT' -> the ticket code."""
    code = text.strip().upper().replace("-", "_").replace(" ", "_")
    if code not in TICKET_TYPES:
        raise argparse.ArgumentTypeError(f"ticket type must be one of {', '.join(TICKET_TYPES)}")
    return code


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
            zone_id=entry.get("zone"),
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


def format_board(
    days: dict[str, DayState], settings: Settings, enquiry: Enquiry, today: str
) -> str:
    board = availability(days, settings, enquiry.date, enquiry.headcount, today=today)
    marks = {"available": "+", "full": "x", "not_offered": " "}
    lines = [f"Board for {enquiry.headcount} people (+ = available, x = full)"]
    width = max(len(code) for code in TICKET_TYPES)
    lines.append("        " + " ".join(f"{code:^{width}}" for code in TICKET_TYPES))
    for start in sorted({slot.start for slot in board}):
        row = [marks[s.status] for s in board if s.start == start]
        line = f"  {format_time(start)} " + " ".join(f"{mark:^{width}}" for mark in row)
        lines.append(line.rstrip())
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check", description="Check a sales enquiry.")
    parser.add_argument("date", help="YYYY-MM-DD")
    parser.add_argument("start", type=parse_time, help="HH:MM, e.g. 10:30")
    parser.add_argument("type", type=parse_ticket_type, help=", ".join(TICKET_TYPES))
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
        action=argparse.BooleanOptionalAction,
        default=True,
        help="print the availability board (default; --no-board to hide it)",
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
    if args.board:
        print()
        print(format_board(days, settings, enquiry, today))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
