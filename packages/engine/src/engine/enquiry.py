"""The enquiry check sales use (SPEC §4.3 + §4.4)."""

from dataclasses import replace

from engine.alternatives import find_alternatives
from engine.budget import SearchBudget
from engine.evaluate import EnquiryResult, Evaluator
from engine.model import Enquiry, Schedule, Settings


def check_enquiry(
    schedule: Schedule,
    settings: Settings,
    enquiry: Enquiry,
    *,
    today: str,
    now_minute: int | None = None,
    budget: SearchBudget | None = None,
) -> EnquiryResult:
    """Verdict on the request as asked, plus up to 3 better alternatives.

    `schedule` holds the request's date and nearby dates (for "another day" alternatives).
    `today` (and optionally the current minute) are passed in because the engine never reads
    the calendar or the wall clock itself. Nothing is stored.
    """
    if budget is None:
        budget = SearchBudget(settings.search_budget_ms)
    evaluator = Evaluator(schedule, settings, today=today, now_minute=now_minute, budget=budget)
    result = evaluator.evaluate(enquiry)
    return replace(result, alternatives=find_alternatives(evaluator, enquiry, result))
