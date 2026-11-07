"""Fixed business rules (SPEC §2). These never change while the app runs.

Times are minutes since midnight, e.g. 9 * 60 = 540 means 09:00.
"""

from collections.abc import Mapping
from typing import Final, Literal

# Ticket types are identified by short, stable codes. Codes are what gets stored and compared;
# the names below are only for display, so products can be renamed without breaking data.
TicketType = Literal["CLASSIC", "CLASSIC_CT", "PREMIUM", "PREMIUM_CT"]

# Shortest first. This order is also the final tie-break between otherwise equal alternatives.
TICKET_TYPES: Final[tuple[TicketType, ...]] = ("CLASSIC", "CLASSIC_CT", "PREMIUM", "PREMIUM_CT")

TICKET_MINUTES: Final[Mapping[TicketType, int]] = {
    "CLASSIC": 90,
    "CLASSIC_CT": 90,
    "PREMIUM": 120,
    "PREMIUM_CT": 180,
}

# Full product names, for screens and Outlook.
TICKET_NAMES: Final[Mapping[TicketType, str]] = {
    "CLASSIC": "Classic Hawker Experience",
    "CLASSIC_CT": "Classic Hawker Experience + Chef's Table",
    "PREMIUM": "Premium Hawker Experience",
    "PREMIUM_CT": "Premium Hawker Experience + Chef's Table",
}

# Short names, for one-line messages to sales.
TICKET_SHORT_NAMES: Final[Mapping[TicketType, str]] = {
    "CLASSIC": "Classic",
    "CLASSIC_CT": "Classic + Chef's Table",
    "PREMIUM": "Premium",
    "PREMIUM_CT": "Premium + Chef's Table",
}

SHORTEST_TICKET_MINUTES: Final = min(TICKET_MINUTES.values())

# What each ticket includes, used to avoid suggesting downgrades (SPEC §4.4).
TICKET_LEVEL: Final[Mapping[TicketType, int]] = {
    "CLASSIC": 1,
    "CLASSIC_CT": 1,
    "PREMIUM": 2,
    "PREMIUM_CT": 2,
}
TICKET_HAS_CHEFS_TABLE: Final[Mapping[TicketType, bool]] = {
    "CLASSIC": False,
    "CLASSIC_CT": True,
    "PREMIUM": False,
    "PREMIUM_CT": True,
}


def is_downgrade(asked: TicketType, offered: TicketType) -> bool:
    """True if `offered` gives the customer less than `asked`: a lower level or no Chef's Table.

    Classic + Chef's Table and Premium are each a downgrade of the other (one loses the Chef's
    Table, the other loses the Premium level).
    """
    lower_level = TICKET_LEVEL[offered] < TICKET_LEVEL[asked]
    loses_chefs_table = TICKET_HAS_CHEFS_TABLE[asked] and not TICKET_HAS_CHEFS_TABLE[offered]
    return lower_level or loses_chefs_table


ZONES: Final[tuple[str, ...]] = ("A", "B", "C", "D")
ZONE_CAPACITY: Final = 25

OPEN: Final = 9 * 60  # 09:00
CLOSE: Final = 21 * 60  # 21:00
GRID: Final = 30  # start times are every 30 minutes
LAST_START: Final = 19 * 60 + 30  # 19:30
START_TIMES: Final[tuple[int, ...]] = tuple(range(OPEN, LAST_START + 1, GRID))

BOOKING_WINDOW_DAYS: Final = 183  # today up to 6 months ahead
