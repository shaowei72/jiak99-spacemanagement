"""Zone capacity engine: pure functions, no I/O (SPEC §4).

Main entry points:
    check_enquiry  - verdict + alternatives for a sales enquiry
    assign         - give every session of a day a zone
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
from engine.metrics import DayMetrics, day_metrics
from engine.model import Blackout, DayState, Enquiry, Price, Schedule, Session, Settings
from engine.timeline import Gap, dead_gaps

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
    "add_days",
    "assign",
    "availability",
    "check_enquiry",
    "day_metrics",
    "days_between",
    "dead_gaps",
    "is_valid_date",
]
