"""Check the Outlook connection (Milestone 5): `uv run outlook-check [--write-test]`.

    uv run outlook-check                # sign in and read the next 7 days of BOOKINGS_MAILBOX
    uv run outlook-check --write-test   # also write, read back, change and delete one test event

Uses GRAPH_MODE and the admin's settings like the app does. Run `--write-test` on the test
mailbox first (BOOKINGS_MAILBOX=jiak99-test@...): it adds an event "Jiak99 connection test" for
tomorrow 07:00-07:30 and deletes it again at the end. Prints OK or PROBLEM for each step.
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import replace

from engine.dates import add_days
from graph.gateway import EventNotFound, GraphGateway
from graph.model import CalendarEvent
from graph.real import GraphError
from jiak_api.clock import singapore_now
from jiak_api.config import load_config
from jiak_api.outlook_sync import make_gateway

TEST_SUBJECT = "Jiak99 connection test (delete me)"
TEST_BOOKING_ID = "connection-test"


class Steps:
    """Prints each step's outcome and remembers whether any went wrong."""

    def __init__(self) -> None:
        self.problems = 0

    def check(self, step: str, ok: bool, detail: str = "") -> bool:
        """Print 'OK  step' or 'PROBLEM  step: detail'; returns `ok`."""
        if ok:
            print(f"OK       {step}")
        else:
            self.problems += 1
            print(f"PROBLEM  {step}: {detail}")
        return ok


def same_event(found: CalendarEvent, written: CalendarEvent) -> str:
    """'' if `found` shows what was written, else what differs."""
    differences = []
    for name in ("date", "start", "end", "subject", "body", "booking_id"):
        if getattr(found, name) != getattr(written, name):
            differences.append(
                f"{name} {getattr(found, name)!r} (wrote {getattr(written, name)!r})"
            )
    return "; ".join(differences)


def write_test(calendar: GraphGateway, date: str, steps: Steps) -> None:
    """Write one test event and check every calendar operation on it; delete it at the end."""
    event = CalendarEvent(
        id="",
        date=date,
        start=7 * 60,
        end=7 * 60 + 30,
        subject=TEST_SUBJECT,
        body="Written by uv run outlook-check --write-test. Safe to delete.",
        booking_id=TEST_BOOKING_ID,
    )
    created = calendar.create_event(event)
    try:
        steps.check("create a test event", bool(created.id), "Outlook gave no event id")

        found = calendar.get_event(created.id)
        steps.check("read it back", not same_event(found, event), same_event(found, event))

        listed = [e for e in calendar.list_events(date, date) if e.id == created.id]
        steps.check(
            "find it in the day's list, with its booking id",
            len(listed) == 1 and listed[0].booking_id == TEST_BOOKING_ID,
            f"listed {len(listed)} time(s), booking id {listed[0].booking_id if listed else None!r}",
        )

        by_uid = calendar.find_event_by_ical_uid(created.ical_uid)
        steps.check(
            "find it by its iCalendar UID (used by the take-over)",
            by_uid is not None and by_uid.id == created.id,
            f"UID {created.ical_uid!r} found {by_uid.id if by_uid else 'nothing'}",
        )

        changed = replace(
            created, subject=TEST_SUBJECT + " - changed", start=7 * 60 + 30, end=8 * 60
        )
        calendar.update_event(changed)
        found = calendar.get_event(created.id)
        steps.check("change it", not same_event(found, changed), same_event(found, changed))

        unlinked = replace(changed, booking_id=None)
        calendar.update_event(unlinked)
        found = calendar.get_event(created.id)
        steps.check(
            "remove its booking id", found.booking_id is None, f"still {found.booking_id!r}"
        )
    finally:
        calendar.delete_event(created.id)
    try:
        calendar.get_event(created.id)
        steps.check("delete it", False, "it is still there")
    except EventNotFound:
        steps.check("delete it", True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="outlook-check", description="Check the app's connection to the Outlook calendar."
    )
    parser.add_argument(
        "--write-test",
        action="store_true",
        help="also write, change and delete one test event (use the test mailbox first)",
    )
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    config = load_config()
    if config.graph_mode != "real":
        print(
            "outlook-check: GRAPH_MODE is not real, so there is no Outlook to check.",
            file=sys.stderr,
        )
        return 2
    try:
        calendar = make_gateway(config)
    except ValueError as error:
        print(f"outlook-check: {error}", file=sys.stderr)
        return 2

    print(f"Mailbox: {config.bookings_mailbox}")
    steps = Steps()
    today = singapore_now().today
    try:
        events = calendar.list_events(today, add_days(today, 6))
        steps.check(f"sign in and read the next 7 days ({len(events)} events)", True)
        if args.write_test:
            write_test(calendar, add_days(today, 1), steps)
    except (GraphError, EventNotFound) as error:
        steps.check("talk to Outlook", False, str(error))

    if steps.problems:
        print(f"\n{steps.problems} problem(s). Pass the lines above on.")
        return 1
    print("\nAll good.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
