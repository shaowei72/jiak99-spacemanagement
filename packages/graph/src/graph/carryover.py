"""Carrying corrections over from an earlier review sheet (SPEC §6 Milestone 5).

The owner may start correcting a review sheet weeks before go-live. Close to go-live a fresh
export of the calendar gives a fresh sheet: events may have moved, new bookings may have been
typed, some may be gone. `carry_over` builds the fresh sheet's rows and copies the owner's
corrections across, matching rows by event id.

What comes from where:
- date, start, end, status, subject: the fresh export (Outlook is right about when an event is).
  Only an all-day event with a start filled in on the earlier sheet keeps that start and end.
- people, adults, children, ticket, client, soft_block, cancelled: the earlier sheet when its
  cell is filled in and differs from the fresh export; otherwise the fresh export.
- action: worked out again from the result, except a 'skip' the owner wrote, which is kept.

Every row where the earlier sheet and the fresh export disagree gets a note saying what Outlook
says now, and a future one gets the action 'check by hand', so the dry run lists it.
"""

import csv
from dataclasses import dataclass, replace

from graph.sheet import read_count, read_ticket, read_time, read_yes_no
from graph.takeover import (
    ACTION_CHECK,
    ACTION_SKIP,
    ReviewRow,
    action_for,
    time_text,
)

ACTION_OWNER_SKIP = "skip"

COUNT_COLUMNS = ("people", "adults", "children")


class CarryOverError(Exception):
    """The earlier sheet as a whole can't be used (e.g. it has no event_id column)."""


@dataclass(frozen=True)
class CarryOver:
    """The fresh rows with corrections copied in, and what happened to the earlier rows.

    `carried`: event ids whose rows took at least one cell from the earlier sheet.
    `disagree`: event ids where the earlier sheet and the fresh export disagree.
    `new`: event ids not on the earlier sheet (typed or turned booking-like since).
    `gone`: earlier rows (as a short label) whose event is no longer in the fresh export.
    """

    rows: tuple[ReviewRow, ...]
    carried: tuple[str, ...]
    disagree: tuple[str, ...]
    new: tuple[str, ...]
    gone: tuple[str, ...]


def read_earlier_sheet(text: str) -> dict[str, dict[str, str]]:
    """The earlier sheet's cells (stripped), by event id. Rows without an event id are left out;
    if an event id appears twice the first row counts."""
    reader = csv.DictReader(text.splitlines())
    columns = [(name or "").strip() for name in reader.fieldnames or []]
    if "event_id" not in columns:
        raise CarryOverError(
            "the earlier sheet has no event_id column. Use a sheet written by the dry run "
            "(keep its header row)."
        )
    rows: dict[str, dict[str, str]] = {}
    for raw in reader:
        cells = {(key or "").strip(): (value or "").strip() for key, value in raw.items()}
        event_id = cells.get("event_id", "")
        if event_id and event_id not in rows:
            rows[event_id] = cells
    return rows


def _fresh_cell(row: ReviewRow, column: str) -> str:
    """A fresh row's cell as the sheet writes it."""
    value = getattr(row, column)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _label(cells: dict[str, str]) -> str:
    """'2026-11-21 14:00 Kingfisher Travel' for a row of the earlier sheet."""
    when = f"{cells.get('date', '')} {cells.get('start', '') or 'all day'}"
    return f"{when} {cells.get('client', '') or cells.get('subject', '')}".strip()


def _earlier_count(text: str, column: str, notes: list[str]) -> int | None:
    """An earlier count cell as a number; None (with a note) if it isn't one."""
    try:
        return read_count(text)
    except ValueError:
        notes.append(f"earlier sheet's {column} {text!r} is not a number: not carried over")
        return None


def _earlier_flag(text: str, column: str, notes: list[str]) -> bool | None:
    """An earlier yes/no cell; None if it is empty or (with a note) not yes or no."""
    if not text:
        return None
    try:
        return read_yes_no(text)
    except ValueError:
        notes.append(f"earlier sheet's {column} {text!r} is not yes or no: not carried over")
        return None


def carry_row(fresh: ReviewRow, earlier: dict[str, str]) -> tuple[ReviewRow, bool, bool]:
    """The fresh row with the earlier sheet's corrections in it.

    Returns (row, carried anything, disagrees with Outlook)."""
    notes: list[str] = []  # cells that couldn't be read, the event having moved
    disagree: list[str] = []  # 'people 20 (Outlook now says 18)'
    carried: list[str] = []  # columns taken from the earlier sheet

    def take(column: str) -> None:
        """Note that `column` comes from the earlier sheet, and what Outlook says if anything."""
        carried.append(column)
        now = _fresh_cell(fresh, column)
        if now:
            disagree.append(f"{column} {earlier.get(column, '')} (Outlook now says {now})")

    counts: dict[str, int | None] = {}
    for column in COUNT_COLUMNS:
        counts[column] = getattr(fresh, column)
        text = earlier.get(column, "")
        if not text or text == _fresh_cell(fresh, column):
            continue
        value = _earlier_count(text, column, notes)
        if value is not None:
            counts[column] = value
            take(column)

    ticket = fresh.ticket
    text = earlier.get("ticket", "")
    if text:
        try:
            earlier_ticket = read_ticket(text)
        except ValueError:
            notes.append(f"earlier sheet's ticket {text!r} is not a ticket type: not carried over")
        else:
            if earlier_ticket is not None and earlier_ticket != fresh.ticket:
                ticket = earlier_ticket
                take("ticket")

    client = fresh.client
    text = earlier.get("client", "")
    if text and text != fresh.client:
        client = text
        take("client")

    soft_block = fresh.soft_block
    flag = _earlier_flag(earlier.get("soft_block", ""), "soft_block", notes)
    if flag is not None and flag != fresh.soft_block:
        soft_block = flag
        take("soft_block")

    cancelled = fresh.cancelled
    flag = _earlier_flag(earlier.get("cancelled", ""), "cancelled", notes)
    if flag is not None and flag != fresh.cancelled:
        cancelled = flag
        take("cancelled")

    # An all-day event: the owner may have filled in its start (and end) on the earlier sheet.
    start, end = fresh.start, fresh.end
    earlier_start = earlier.get("start", "")
    if not fresh.start and earlier_start:
        start_minutes = read_time(earlier_start)
        end_minutes = read_time(earlier.get("end", ""))
        if start_minutes is None:
            notes.append(f"earlier sheet's start {earlier_start!r} is not a time: not carried over")
        else:
            start = time_text(start_minutes)
            end = time_text(end_minutes) if end_minutes is not None else ""
            carried.append("start")
    elif earlier.get("date") and (earlier.get("date"), earlier_start) != (fresh.date, fresh.start):
        was = f"{earlier.get('date')} {earlier_start or 'all day'}"
        notes.append(f"moved in Outlook since the earlier sheet (was {was})")

    # A 'skip' the owner wrote is kept; 'skip (cancelled)' is worked out again from 'cancelled'.
    earlier_action = earlier.get("action", "")
    owner_skip = (
        earlier_action.lower().startswith(ACTION_OWNER_SKIP) and earlier_action != ACTION_SKIP
    )
    if not carried and not notes and not owner_skip:
        return fresh, False, False

    row = replace(
        fresh,
        start=start,
        end=end,
        people=counts["people"],
        adults=counts["adults"],
        children=counts["children"],
        ticket=ticket,
        client=client,
        soft_block=soft_block,
        cancelled=cancelled,
    )
    if owner_skip:
        action = earlier_action
    elif disagree and row.when == "future":
        action = ACTION_CHECK  # so the dry run lists it
    else:
        action = action_for(row)

    if disagree:
        notes.insert(0, "kept from the earlier sheet: " + ", ".join(disagree))
    elif carried or owner_skip:
        notes.insert(0, "corrections carried over from the earlier sheet")
    all_notes = "; ".join(note for note in [*notes, fresh.notes] if note)
    took = bool(carried) or owner_skip
    return replace(row, action=action, notes=all_notes), took, bool(disagree)


def carry_over(fresh_rows: tuple[ReviewRow, ...], earlier: dict[str, dict[str, str]]) -> CarryOver:
    """The fresh sheet with the earlier sheet's corrections copied in (see the module docstring)."""
    rows: list[ReviewRow] = []
    carried: list[str] = []
    disagree: list[str] = []
    new: list[str] = []
    fresh_ids: set[str] = set()
    for fresh in fresh_rows:
        fresh_ids.add(fresh.event_id)
        cells = earlier.get(fresh.event_id)
        if cells is None:
            new.append(fresh.event_id)
            rows.append(fresh)
            continue
        row, took, differs = carry_row(fresh, cells)
        if took:
            carried.append(fresh.event_id)
        if differs:
            disagree.append(fresh.event_id)
        rows.append(row)
    gone = [_label(cells) for event_id, cells in earlier.items() if event_id not in fresh_ids]
    return CarryOver(
        rows=tuple(rows),
        carried=tuple(carried),
        disagree=tuple(disagree),
        new=tuple(new),
        gone=tuple(gone),
    )
