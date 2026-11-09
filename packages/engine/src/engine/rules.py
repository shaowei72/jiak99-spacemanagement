"""Fixed business rules (SPEC §2). These never change while the app runs.

Times are minutes since midnight, e.g. 9 * 60 = 540 means 09:00.
"""

from collections.abc import Mapping
from itertools import permutations
from typing import Final, Literal

# Ticket types are identified by short, stable codes. Codes are what gets stored and compared;
# the names below are only for display, so products can be renamed without breaking data.
TicketType = Literal["TINGKAT", "CLASSIC", "CLASSIC_CT", "PREMIUM", "PREMIUM_CT"]

# Shortest first. This order is also the final tie-break between otherwise equal alternatives.
TICKET_TYPES: Final[tuple[TicketType, ...]] = (
    "TINGKAT",
    "CLASSIC",
    "CLASSIC_CT",
    "PREMIUM",
    "PREMIUM_CT",
)

TICKET_MINUTES: Final[Mapping[TicketType, int]] = {
    "TINGKAT": 30,
    "CLASSIC": 60,
    "CLASSIC_CT": 90,
    "PREMIUM": 120,
    "PREMIUM_CT": 180,
}

# Full product names, for screens.
TICKET_NAMES: Final[Mapping[TicketType, str]] = {
    "TINGKAT": "Tingkat Meals",
    "CLASSIC": "Classic Hawker Experience",
    "CLASSIC_CT": "Classic Hawker Experience + Chef's Table",
    "PREMIUM": "Premium Hawker Experience",
    "PREMIUM_CT": "Premium Hawker Experience + Chef's Table",
}

# Short labels, for boards, Outlook subjects and one-line messages to sales (CT = Chef's Table).
TICKET_SHORT_NAMES: Final[Mapping[TicketType, str]] = {
    "TINGKAT": "Tingkat",
    "CLASSIC": "CHE",
    "CLASSIC_CT": "CHE+CT",
    "PREMIUM": "PHE",
    "PREMIUM_CT": "PHE+CT",
}

# What each ticket includes, used to avoid suggesting downgrades (SPEC §4.4).
# Tingkat is level 0, so it is never offered instead of another ticket.
TICKET_LEVEL: Final[Mapping[TicketType, int]] = {
    "TINGKAT": 0,
    "CLASSIC": 1,
    "CLASSIC_CT": 1,
    "PREMIUM": 2,
    "PREMIUM_CT": 2,
}
TICKET_HAS_CHEFS_TABLE: Final[Mapping[TicketType, bool]] = {
    "TINGKAT": False,
    "CLASSIC": False,
    "CLASSIC_CT": True,
    "PREMIUM": False,
    "PREMIUM_CT": True,
}


def is_downgrade(asked: TicketType, offered: TicketType) -> bool:
    """True if `offered` gives the customer less than `asked`: a lower level or no Chef's Table.

    Classic + Chef's Table and Premium are each a downgrade of the other (one loses the Chef's
    Table, the other loses the Premium level). Tingkat is a downgrade of every other ticket.
    """
    lower_level = TICKET_LEVEL[offered] < TICKET_LEVEL[asked]
    loses_chefs_table = TICKET_HAS_CHEFS_TABLE[asked] and not TICKET_HAS_CHEFS_TABLE[offered]
    return lower_level or loses_chefs_table


# --- zones and routes -------------------------------------------------------------------------

ZONES: Final[tuple[str, ...]] = ("A", "B", "C", "D")
ZONE_NAMES: Final[Mapping[str, str]] = {
    "A": "Tour/Exhibit",
    "B": "Dining",
    "C": "Chef's Table / Dining",
    "D": "Sandart",
}
ZONE_CAPACITY: Final = 25

# The zone sets each ticket may use (the app picks one). Each zone in the set is visited once.
ZONE_SETS: Final[Mapping[TicketType, tuple[str, ...]]] = {
    "TINGKAT": ("B", "C", "D"),
    "CLASSIC": ("AD", "AB", "AC"),
    "CLASSIC_CT": ("ACD", "ABD"),
    "PREMIUM": ("ABCD",),
    "PREMIUM_CT": ("ABCD",),
}

VISIT_MINUTES: Final = 30  # time in each zone, except the longer visits below
LONG_VISITS: Final[Mapping[TicketType, Mapping[str, int]]] = {
    "PREMIUM_CT": {"B": 60, "C": 60},
}


def visit_minutes(ticket_type: TicketType, zone: str) -> int:
    """How long a group with this ticket stays in `zone`."""
    return LONG_VISITS.get(ticket_type, {}).get(zone, VISIT_MINUTES)


def _all_routes(ticket_type: TicketType) -> tuple[str, ...]:
    """Every order of every allowed zone set, e.g. CLASSIC -> ('AB', 'AC', 'AD', 'BA', ...)."""
    routes = {
        "".join(order) for zone_set in ZONE_SETS[ticket_type] for order in permutations(zone_set)
    }
    return tuple(sorted(routes))


# A route is the zone letters in visiting order, e.g. 'ABDC'.
ROUTES: Final[Mapping[TicketType, tuple[str, ...]]] = {
    ticket_type: _all_routes(ticket_type) for ticket_type in TICKET_TYPES
}


def is_valid_route(ticket_type: TicketType, route: str) -> bool:
    """True if `route` visits exactly one of the ticket's zone sets, each zone once."""
    return route in ROUTES.get(ticket_type, ())


# --- the day ------------------------------------------------------------------------------------

OPEN: Final = 9 * 60  # 09:00
CLOSE: Final = 21 * 60  # 21:00
GRID: Final = 30  # start times and zone slots are every 30 minutes
SLOTS_PER_DAY: Final = (CLOSE - OPEN) // GRID  # 24
LAST_START: Final = CLOSE - min(TICKET_MINUTES.values())  # 20:30
START_TIMES: Final[tuple[int, ...]] = tuple(range(OPEN, LAST_START + 1, GRID))

BOOKING_WINDOW_DAYS: Final = 183  # today up to 6 months ahead
