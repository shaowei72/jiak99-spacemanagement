"""Small helpers for the plain-English messages shown to sales staff."""

from engine.rules import TICKET_MINUTES, TICKET_SHORT_NAMES, TicketType


def format_time(minutes: int) -> str:
    """540 -> '09:00'."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def ticket_label(ticket_type: TicketType) -> str:
    """'PREMIUM' -> 'PHE', 'CLASSIC_CT' -> 'CHE+CT'."""
    return TICKET_SHORT_NAMES[ticket_type]


def duration_label(ticket_type: TicketType) -> str:
    """'CLASSIC' -> '1.5 h', 'PREMIUM' -> '2 h'."""
    return f"{TICKET_MINUTES[ticket_type] / 60:g} h"


def slot_label(start: int, ticket_type: TicketType) -> str:
    """(660, 'PREMIUM') -> '11:00 · PHE'."""
    return f"{format_time(start)} · {ticket_label(ticket_type)}"


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """plural(1, 'zone') -> '1 zone'; plural(2, 'zone') -> '2 zones'."""
    word = singular if count == 1 else (plural_form or singular + "s")
    return f"{count} {word}"


def join_words(items: list[str]) -> str:
    """['a', 'b', 'c'] -> 'a, b and c'."""
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]
