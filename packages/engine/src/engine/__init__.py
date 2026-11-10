"""Zone capacity engine: pure functions, no I/O (SPEC §4).

Main entry points:
    check_enquiry  - verdict + alternatives for a sales enquiry
    assign         - give every session of a day a route (an order of zones)
    route_visits   - the zone visits of one route
    availability   - the availability board for a group size
    day_metrics    - utilisation figures for one day
"""

from engine.assign import Assignment, assign
from engine.availability import Slot, availability
from engine.budget import SearchBudget
from engine.dates import add_days, days_between, is_valid_date
from engine.enquiry import check_enquiry
from engine.evaluate import (
    Alternative,
    EnquiryResult,
    NewPlacement,
    Placement,
    SplitPlacement,
    Verdict,
)
from engine.metrics import DayMetrics, day_metrics, seat_hour_use, zone_hour_use
from engine.model import Blackout, DayState, Enquiry, Price, Schedule, Session, Settings
from engine.timeline import Gap, Visit, dead_gaps, route_visits

__all__ = [
    "Alternative",
    "Assignment",
    "Blackout",
    "DayMetrics",
    "DayState",
    "Enquiry",
    "EnquiryResult",
    "Gap",
    "NewPlacement",
    "Placement",
    "Price",
    "Schedule",
    "SearchBudget",
    "Session",
    "Settings",
    "Slot",
    "SplitPlacement",
    "Verdict",
    "Visit",
    "add_days",
    "assign",
    "availability",
    "check_enquiry",
    "day_metrics",
    "days_between",
    "dead_gaps",
    "is_valid_date",
    "route_visits",
    "seat_hour_use",
    "zone_hour_use",
]
