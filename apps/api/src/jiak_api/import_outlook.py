"""Take over the Bookings calendar once (SPEC §5.4 one-off migration).

    uv run import-outlook --from-ics FILE            # dry run: write the review sheet, store nothing
    uv run import-outlook --from-ics FILE --out review.csv --today 2026-11-12
    uv run import-outlook --from-ics FRESH.ics --carry-over EARLIER-review.csv
    uv run import-outlook --review FILE              # rehearse the corrected sheet: store nothing
    uv run import-outlook --review FILE --commit     # apply it: bookings, history, Outlook events
    uv run import-outlook --from-graph --out review.csv   # read the calendar itself (GRAPH_MODE=real)

The review sheet is written next to the .ics file (`<name>-review.csv`) unless `--out` says
otherwise. It holds customer names, so keep it out of the repo. An existing sheet is never
overwritten (it may hold your corrections) unless you add `--overwrite`.

`--carry-over` copies the corrections from an earlier sheet into the fresh one, matched by event
id (see `graph.carryover`): start correcting early, export again close to go-live.

`--from-graph` reads BOOKINGS_MAILBOX's calendar through Microsoft Graph instead of an .ics file
(GRAPH_MODE=real and the admin's settings needed), from `--since` (default: a year before the
take-over date) to 200 days after the take-over date. It writes the same sheet; `--out` is needed.

`--review` uses DATABASE_URL like the API does, and the calendar GRAPH_MODE says. With the fake
calendar (the default), an in-memory copy of the sheet's events stands in for operations@, so you
can see what would be rewritten; your real calendar is not touched.
"""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.engine import make_url

from engine.dates import add_days, is_valid_date
from engine.text import plural
from graph.carryover import CarryOverError, carry_over, read_earlier_sheet
from graph.fake import FakeGateway
from graph.gateway import GraphGateway
from graph.ics import IcsError, IcsEvent, parse_ics
from graph.model import CalendarEvent
from graph.oldstyle import ClientMapError, ClientRule, parse_client_map
from graph.real import GraphError
from graph.sheet import SheetError, SheetRow, read_sheet
from graph.takeover import (
    ACTION_BOOK,
    ACTION_HISTORY,
    ACTION_SKIP,
    build_review,
    count_actions,
    review_csv,
)
from jiak_api.clock import Now, singapore_now
from jiak_api.config import AppConfig, load_config
from jiak_api.db import Database
from jiak_api.migrate import upgrade_to_latest
from jiak_api.outlook_sync import make_gateway
from jiak_api.take_over import (
    OUTCOME_ALREADY,
    OUTCOME_BOOKED,
    OUTCOME_HISTORY,
    OUTCOME_SKIPPED,
    TO_SORT_OUT,
    TakeOverResult,
    apply_sheet,
    row_label,
)

DEFAULT_CLIENT_MAP = Path(__file__).resolve().parents[2] / "client-tickets.toml"  # apps/api


class TakeOverError(Exception):
    """The import can't go on; the message says why and what to do."""


def load_client_map(path: Path) -> list[ClientRule]:
    """Read the client map file (see apps/api/client-tickets.toml)."""
    try:
        return parse_client_map(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise TakeOverError(f"Can't read the client map {path}: {error.strerror}") from error
    except ClientMapError as error:
        raise TakeOverError(f"Client map {path}: {error}") from error


def review_path(ics_path: Path, out: str | None) -> Path:
    """Where the review sheet goes: `--out`, else '<ics name>-review.csv' next to the .ics file."""
    if out:
        return Path(out)
    return ics_path.with_name(f"{ics_path.stem}-review.csv")


def read_csv_text(path: Path) -> str:
    """A sheet saved by Excel, as 'CSV UTF-8' or, as plain 'CSV', in the Windows code page."""
    try:
        data = path.read_bytes()
    except OSError as error:
        raise TakeOverError(f"Can't read {path}: {error.strerror}") from error
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def check_out_path(out_path: Path, overwrite: bool, earlier_path: Path | None) -> None:
    """Refuse to write the sheet over an earlier one (it may hold corrections)."""
    if earlier_path is not None and earlier_path.resolve() == out_path.resolve():
        raise TakeOverError(
            f"--carry-over {earlier_path} is where the fresh sheet would go. "
            "Use --out to write the fresh sheet somewhere else."
        )
    if out_path.exists() and not overwrite:
        raise TakeOverError(
            f"{out_path} already exists (it may hold your corrections). "
            "Use --out for another file, or --overwrite to replace it."
        )


def events_from_ics(ics_path: Path) -> list[IcsEvent]:
    """The events in an .ics file saved from the calendar."""
    try:
        text = ics_path.read_text(encoding="utf-8")
    except OSError as error:
        raise TakeOverError(f"Can't read {ics_path}: {error.strerror}") from error
    try:
        return parse_ics(text)
    except IcsError as error:
        raise TakeOverError(f"{ics_path}: {error}") from error


def events_from_graph(gateway: GraphGateway, first_date: str, last_date: str) -> list[IcsEvent]:
    """The calendar's events from `first_date` to `last_date`, read through Microsoft Graph, in
    the shape the review sheet is built from. The event id on the sheet is the iCalendar UID, as
    for an .ics file, so sheets from either source match up."""
    try:
        events = gateway.list_events(first_date, last_date)
    except GraphError as error:
        raise TakeOverError(str(error)) from error
    return [
        IcsEvent(
            uid=event.ical_uid or event.id,
            date=event.date,
            start=event.start,
            end=event.end,
            subject=event.subject,
            body=event.body,
            all_day=event.all_day,
        )
        for event in events
    ]


def dry_run(
    events: list[IcsEvent],
    source_name: str,
    out_path: Path,
    today: str,
    rules: list[ClientRule],
    earlier_path: Path | None = None,
) -> str:
    """Write the review sheet for `events` and return a summary to print. With `earlier_path`,
    corrections on that earlier sheet are carried over."""
    review = build_review(events, today, rules)
    rows = review.rows
    carried_lines: list[str] = []
    if earlier_path is not None:
        try:
            earlier = read_earlier_sheet(read_csv_text(earlier_path))
        except CarryOverError as error:
            raise TakeOverError(f"{earlier_path}: {error}") from error
        result = carry_over(rows, earlier)
        rows = result.rows
        carried_lines = [
            "",
            f"From the earlier sheet {earlier_path.name}:",
            f"  corrections carried over   {len(result.carried)}",
            f"  disagree with Outlook now  {len(result.disagree)} (marked 'check by hand')",
            f"  new since then             {len(result.new)}",
            f"  no longer in the calendar  {len(result.gone)}",
        ]
        for label in result.gone:
            carried_lines.append(f"    {label}")
    # utf-8-sig: Excel then shows '·', '–' and accents correctly.
    out_path.write_text(review_csv(rows), encoding="utf-8-sig", newline="")

    lines = [
        f"Read {len(events)} events from {source_name}: {len(rows)} look like bookings, "
        f"{len(review.others)} others (meetings, site visits, ...) are left alone.",
        f"Take-over date {today}: events before it are history.",
        "",
        "Proposed actions:",
    ]
    for action, number in count_actions(rows).items():
        lines.append(f"  {action:<18} {number}")
    lines.extend(carried_lines)

    to_look_at = [
        row for row in rows if row.action not in (ACTION_BOOK, ACTION_HISTORY, ACTION_SKIP)
    ]
    if to_look_at:
        lines.append("")
        lines.append("Future events to look at:")
        for row in to_look_at:
            lines.append(
                f"  {row.date} {row.start or 'all day':>7}  {row.action:<14} {row.client or row.subject}"
            )
            if row.notes:
                lines.append(f"  {'':18}  {row.notes}")
    lines.append("")
    lines.append(f"Review sheet written: {out_path}")
    lines.append(
        "Nothing was stored. Correct the sheet, then run --review FILE to rehearse "
        "and --review FILE --commit to apply it."
    )
    return "\n".join(lines)


# --- applying the corrected sheet --------------------------------------------------------------


def load_sheet(path: Path) -> list[SheetRow]:
    """Read the corrected sheet (see `read_csv_text` for the file formats)."""
    text = read_csv_text(path)
    try:
        return read_sheet(text)
    except SheetError as error:
        raise TakeOverError(f"{path}: {error}") from error


def fake_calendar(rows: list[SheetRow]) -> FakeGateway:
    """An in-memory calendar holding each row's event as Outlook has it now (old subject)."""
    events: list[CalendarEvent] = []
    for row in rows:
        if not row.event_id or not row.date:
            continue
        start = row.start if row.start is not None else 0
        end = row.end if row.end is not None else start
        events.append(
            CalendarEvent(
                id="",
                date=row.date,
                start=start,
                end=end,
                subject=row.subject,
                ical_uid=row.event_id,
            )
        )
    return FakeGateway(events)


def calendar_for(config: AppConfig, rows: list[SheetRow]) -> tuple[GraphGateway, str]:
    """The calendar to take over, and a line saying which one it is."""
    if config.graph_mode != "real":
        note = (
            "Calendar: GRAPH_MODE is fake, so an in-memory copy of the sheet's events stands in "
            "for operations@. Your real calendar is not touched."
        )
        return fake_calendar(rows), note
    gateway = real_calendar(config)
    return gateway, f"Calendar: {config.bookings_mailbox} in Outlook (GRAPH_MODE=real)."


def real_calendar(config: AppConfig) -> GraphGateway:
    """The real calendar, or a TakeOverError saying which setting is missing."""
    if config.graph_mode != "real":
        raise TakeOverError(
            "--from-graph reads Outlook itself: set GRAPH_MODE=real and the M365 admin's values "
            "(README.md, step 7). Or save the calendar as an .ics file and use --from-ics."
        )
    try:
        return make_gateway(config)
    except ValueError as error:
        raise TakeOverError(str(error)) from error


def take_over_report(result: TakeOverResult, sheet: Path, today: str, notes: list[str]) -> str:
    """What the take-over did (or would do), to print."""
    lines = [
        f"Read {plural(len(result.outcomes), 'row')} from {sheet.name}. "
        f"Take-over date {today}: rows before it are history.",
        *notes,
        "",
    ]
    order = (OUTCOME_BOOKED, OUTCOME_HISTORY, *TO_SORT_OUT, OUTCOME_SKIPPED, OUTCOME_ALREADY)
    for outcome in order:
        number = result.count(outcome)
        if number:
            lines.append(f"  {outcome:<16} {number}")

    booked = [item for item in result.outcomes if item.outcome == OUTCOME_BOOKED]
    if booked:
        lines.append("")
        lines.append(
            "Booked. The Outlook event gets this subject (route added once the day is planned):"
        )
        for item in booked:
            lines.append(f"  {row_label(item.row)}")
            lines.append(f"  {'':19}-> {item.message}")

    to_sort = [item for item in result.outcomes if item.outcome in TO_SORT_OUT]
    if to_sort:
        lines.append("")
        lines.append("To sort out by hand (nothing stored, the Outlook event is left as it is):")
        for item in to_sort:
            lines.append(f"  {row_label(item.row)}")
            lines.append(f"  {'':19}{item.outcome}: {item.message}")

    lines.append("")
    if result.committed:
        dates = plural(len(result.booked_dates), "date")
        lines.append(f"Stored. Outlook events rewritten in place on {dates}.")
    else:
        lines.append("Rehearsal: nothing was stored. Add --commit to apply the sheet.")
    return "\n".join(lines)


def apply_review(sheet: Path, today: str, now: Now, config: AppConfig, *, commit: bool) -> str:
    """Rehearse (or with `commit`, apply) the corrected sheet and return the report."""
    rows = load_sheet(sheet)
    gateway, calendar_note = calendar_for(config, rows)
    upgrade_to_latest(config.database_url)  # make sure the tables exist
    database = Database(config.database_url)
    try:
        with database.make_session() as db:
            result = apply_sheet(database, db, gateway, rows, today=today, now=now, commit=commit)
    finally:
        database.engine.dispose()
    shown = make_url(config.database_url).render_as_string(hide_password=True)
    return take_over_report(result, sheet, today, [calendar_note, f"Database: {shown}"])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="import-outlook",
        description="Take over the Bookings calendar once: write a review sheet (dry run), then apply it.",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--from-ics", metavar="FILE", help="an .ics file saved from the calendar")
    source.add_argument(
        "--from-graph",
        action="store_true",
        help="read the calendar itself through Microsoft Graph (GRAPH_MODE=real)",
    )
    source.add_argument("--review", metavar="CSV", help="the corrected review sheet to apply")
    parser.add_argument("--out", metavar="CSV", help="where to write the review sheet")
    parser.add_argument(
        "--carry-over",
        metavar="CSV",
        help="with --from-ics: copy the corrections from this earlier review sheet",
    )
    parser.add_argument("--today", metavar="YYYY-MM-DD", help="take-over date (default: today)")
    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="with --from-graph: first date to read (default: a year before the take-over date)",
    )
    parser.add_argument(
        "--clients",
        metavar="FILE",
        default=str(DEFAULT_CLIENT_MAP),
        help="client map (default: apps/api/client-tickets.toml)",
    )
    parser.add_argument("--overwrite", action="store_true", help="replace an existing review sheet")
    parser.add_argument("--dry-run", action="store_true", help="store nothing (the default)")
    parser.add_argument("--commit", action="store_true", help="with --review: store it")
    args = parser.parse_args(argv)

    try:
        if args.commit and not args.review:
            raise TakeOverError("--commit goes with --review CSV (the corrected sheet).")
        if args.carry_over and not (args.from_ics or args.from_graph):
            raise TakeOverError("--carry-over goes with --from-ics FILE or --from-graph.")
        if args.from_graph and not args.out:
            raise TakeOverError("--from-graph needs --out CSV (where to write the review sheet).")
        if args.since and not args.from_graph:
            raise TakeOverError("--since goes with --from-graph.")
        if not args.from_ics and not args.review and not args.from_graph:
            raise TakeOverError(
                "Say where to read from: --from-ics FILE, --from-graph (or --review CSV)."
            )
        today = args.today or singapore_now().today
        if not is_valid_date(today):
            raise TakeOverError(f"--today must be a date like 2026-11-12, not {today!r}.")
        # Bookings are checked as if at the start of the take-over date, so groups coming later
        # that same day are still taken over even if their start time has already passed.
        now = Now(today=today, minute=0)
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")  # the report uses "·" and "–"
        if args.review:
            # Outlook errors during the rewrite are logged by the sync; show them here.
            logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
            summary = apply_review(Path(args.review), today, now, load_config(), commit=args.commit)
        else:
            rules = load_client_map(Path(args.clients))
            earlier = Path(args.carry_over) if args.carry_over else None
            if args.from_graph:
                since = args.since or add_days(today, -365)
                if not is_valid_date(since):
                    raise TakeOverError(f"--since must be a date like 2026-10-01, not {since!r}.")
                out_path = Path(args.out)
                check_out_path(out_path, args.overwrite, earlier)
                config = load_config()
                events = events_from_graph(real_calendar(config), since, add_days(today, 200))
                source_name = f"{config.bookings_mailbox} ({since} to {add_days(today, 200)})"
            else:
                ics_path = Path(args.from_ics)
                out_path = review_path(ics_path, args.out)
                check_out_path(out_path, args.overwrite, earlier)
                events = events_from_ics(ics_path)
                source_name = ics_path.name
            summary = dry_run(events, source_name, out_path, today, rules, earlier)
    except TakeOverError as error:
        print(f"import-outlook: {error}", file=sys.stderr)
        return 2
    print(summary)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
