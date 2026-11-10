"""Take over the Bookings calendar once (SPEC §5.4 one-off migration).

    uv run import-outlook --from-ics FILE            # dry run: write the review sheet, store nothing
    uv run import-outlook --from-ics FILE --out review.csv --today 2026-11-12
    uv run import-outlook --review FILE --commit     # apply the corrected sheet (not built yet)
    uv run import-outlook --from-graph               # read operations@ itself (not built yet)

The review sheet is written next to the .ics file (`<name>-review.csv`) unless `--out` says
otherwise. It holds customer names, so keep it out of the repo. An existing sheet is never
overwritten (it may hold your corrections) unless you add `--overwrite`.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from engine.dates import is_valid_date
from graph.ics import IcsError, parse_ics
from graph.oldstyle import ClientMapError, ClientRule, parse_client_map
from graph.takeover import (
    ACTION_BOOK,
    ACTION_HISTORY,
    ACTION_SKIP,
    build_review,
    count_actions,
    review_csv,
)
from jiak_api.clock import singapore_now

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


def dry_run(
    ics_path: Path, out_path: Path, today: str, rules: list[ClientRule], overwrite: bool
) -> str:
    """Read the .ics file, write the review sheet and return a summary to print."""
    if out_path.exists() and not overwrite:
        raise TakeOverError(
            f"{out_path} already exists (it may hold your corrections). "
            "Use --out for another file, or --overwrite to replace it."
        )
    try:
        text = ics_path.read_text(encoding="utf-8")
    except OSError as error:
        raise TakeOverError(f"Can't read {ics_path}: {error.strerror}") from error
    try:
        events = parse_ics(text)
    except IcsError as error:
        raise TakeOverError(f"{ics_path}: {error}") from error

    review = build_review(events, today, rules)
    # utf-8-sig: Excel then shows '·', '–' and accents correctly.
    out_path.write_text(review_csv(review.rows), encoding="utf-8-sig", newline="")

    lines = [
        f"Read {len(events)} events from {ics_path.name}: {len(review.rows)} look like bookings, "
        f"{len(review.others)} others (meetings, site visits, ...) are left alone.",
        f"Take-over date {today}: events before it are history.",
        "",
        "Proposed actions:",
    ]
    for action, number in count_actions(review.rows).items():
        lines.append(f"  {action:<18} {number}")

    to_look_at = [
        row for row in review.rows if row.action not in (ACTION_BOOK, ACTION_HISTORY, ACTION_SKIP)
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
        "Nothing was stored. Correct the sheet, then run --review FILE --commit (coming next)."
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="import-outlook",
        description="Take over the Bookings calendar once: write a review sheet (dry run), then apply it.",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--from-ics", metavar="FILE", help="an .ics file saved from the calendar")
    source.add_argument(
        "--from-graph", action="store_true", help="read the calendar itself (later)"
    )
    source.add_argument("--review", metavar="CSV", help="a corrected review sheet to apply (later)")
    parser.add_argument("--out", metavar="CSV", help="where to write the review sheet")
    parser.add_argument("--today", metavar="YYYY-MM-DD", help="take-over date (default: today)")
    parser.add_argument(
        "--clients",
        metavar="FILE",
        default=str(DEFAULT_CLIENT_MAP),
        help="client map (default: apps/api/client-tickets.toml)",
    )
    parser.add_argument("--overwrite", action="store_true", help="replace an existing review sheet")
    parser.add_argument("--dry-run", action="store_true", help="store nothing (the default)")
    parser.add_argument("--commit", action="store_true", help="apply --review FILE (later)")
    args = parser.parse_args(argv)

    try:
        if args.commit or args.review:
            raise TakeOverError("--review / --commit are not built yet (Milestone 4 part 4).")
        if args.from_graph:
            raise TakeOverError(
                "--from-graph is not built yet (needs the real Graph gateway). Use --from-ics."
            )
        if not args.from_ics:
            raise TakeOverError("Say where to read from: --from-ics FILE.")
        today = args.today or singapore_now().today
        if not is_valid_date(today):
            raise TakeOverError(f"--today must be a date like 2026-11-12, not {today!r}.")
        rules = load_client_map(Path(args.clients))
        ics_path = Path(args.from_ics)
        summary = dry_run(ics_path, review_path(ics_path, args.out), today, rules, args.overwrite)
    except TakeOverError as error:
        print(f"import-outlook: {error}", file=sys.stderr)
        return 2
    print(summary)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
