"""Reads the short subject sales type by hand in the Bookings calendar (SPEC §5.4).

    [PHE] 11:00 · 10A 2C · Tan family

Ticket in square brackets, start time, adults ('10A') and optionally children ('2C'), customer.
The separators may be '·', '|' or '-'. Upper or lower case and extra spaces are fine. The date is the
event's own date, so it isn't in the subject.

The ticket can be written as its code or its name: `[classic ct]`, `[Classic-CT]`, `[CLASSIC_CT]` all
mean CLASSIC_CT. The short names used in the app's own subjects also work: `[CHE+CT]`, `[PHE]`.
"""

import re
from dataclasses import dataclass

from engine.rules import TicketType

_TEMPLATE = re.compile(
    r"^\s*\[\s*(?P<ticket>[a-z +_\-]+?)\s*\]"  # [Premium CT]
    r"\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})"  # 11:00
    r"\s*[·|\-]\s*(?P<adults>\d+)\s*A"  # · 10A
    r"(?:\s*(?P<children>\d+)\s*C)?"  # 2C (optional)
    r"\s*[·|\-]\s*(?P<customer>\S.*?)\s*$",  # · Tan family
    re.IGNORECASE,
)

# Ticket words as written by people, after removing spaces, '+', '_' and '-' and lower-casing.
_TICKET_WORDS: dict[str, TicketType] = {
    "tingkat": "TINGKAT",
    "classic": "CLASSIC",
    "che": "CLASSIC",
    "classicct": "CLASSIC_CT",
    "chect": "CLASSIC_CT",
    "premium": "PREMIUM",
    "phe": "PREMIUM",
    "premiumct": "PREMIUM_CT",
    "phect": "PREMIUM_CT",
}


@dataclass(frozen=True)
class TemplateBooking:
    """What a hand-typed subject asks for. `start` is minutes since midnight."""

    ticket_type: TicketType
    start: int
    adults: int
    children: int
    customer: str


def ticket_from_words(text: str) -> TicketType | None:
    """'Classic CT' -> 'CLASSIC_CT', 'phe+ct' -> 'PREMIUM_CT'; None if it isn't a ticket type."""
    squeezed = text.lower()
    for character in " +_-":
        squeezed = squeezed.replace(character, "")
    return _TICKET_WORDS.get(squeezed)


def parse_template(subject: str) -> TemplateBooking | None:
    """Read a hand-typed subject. None if it doesn't follow the template (the app then marks the
    event 'Needs format')."""
    match = _TEMPLATE.match(subject)
    if match is None:
        return None
    ticket_type = ticket_from_words(match["ticket"])
    if ticket_type is None:
        return None
    hour = int(match["hour"])
    minute = int(match["minute"])
    if hour > 23 or minute > 59:
        return None
    children = int(match["children"]) if match["children"] else 0
    return TemplateBooking(
        ticket_type=ticket_type,
        start=hour * 60 + minute,
        adults=int(match["adults"]),
        children=children,
        customer=match["customer"],
    )
