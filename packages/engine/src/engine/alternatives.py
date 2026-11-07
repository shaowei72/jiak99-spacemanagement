"""Alternatives: better options to pitch to the customer (SPEC §4.4)."""

from dataclasses import dataclass

from engine.dates import add_days, is_valid_date, weekday
from engine.evaluate import Alternative, EnquiryResult, Evaluator, Verdict
from engine.model import Enquiry, Settings
from engine.rules import GRID, TICKET_MINUTES, TICKET_TYPES, TicketType, is_downgrade
from engine.text import format_time, ticket_label

MAX_ALTERNATIVES = 3
TIME_SHIFTS = (-60, -30, 30, 60)

_VERDICT_RANK: dict[Verdict, int] = {"ACCEPT_GOOD": 0, "ACCEPT_COSTLY": 1, "NOT_POSSIBLE": 2}


@dataclass(frozen=True)
class _Candidate:
    """One changed version of the request, and how big the change is."""

    date: str
    start: int
    ticket_type: TicketType
    shift_steps: int  # 30-min steps away from the requested start
    type_changed: bool
    day_offset: int  # days away from the requested date

    @property
    def change_size(self) -> int:
        size = self.shift_steps + (1 if self.type_changed else 0)
        if self.day_offset:
            size += 2 + abs(self.day_offset)
        return size

    @property
    def change_kind(self) -> int:
        """Tie-break: time shifts before type changes before day changes."""
        if self.day_offset:
            return 2
        return 1 if self.type_changed else 0

    def as_enquiry(self, original: Enquiry) -> Enquiry:
        return Enquiry(self.date, self.start, self.ticket_type, original.adults, original.children)


def _quality(result: EnquiryResult) -> tuple[int, int, int]:
    """How well a result uses space (lower is better): verdict, dead minutes, zones taken.

    SPEC §4.4 criterion 3 says zoneMinutesAdded; we compare the number of zones instead, so a
    shorter ticket isn't preferred just for being shorter (see DECISIONS.md; T5 needs this).
    """
    return (_VERDICT_RANK[result.verdict], result.dead_minutes_delta, result.new_session_count)


def find_alternatives(
    evaluator: Evaluator, enquiry: Enquiry, own: EnquiryResult
) -> tuple[Alternative, ...]:
    """Up to 3 options that are strictly better than the request's own result, best first."""
    if enquiry.ticket_type not in TICKET_MINUTES or enquiry.headcount < 1:
        return ()  # nothing sensible to vary: the request itself is malformed
    own_quality = _quality(own)
    better: list[tuple[_Candidate, EnquiryResult]] = []

    for candidate in _same_day_candidates(enquiry):
        result = evaluator.evaluate(candidate.as_enquiry(enquiry))
        if _quality(result) < own_quality:
            better.append((candidate, result))

    other_day = _nearest_better_day(evaluator, enquiry, own_quality)
    if other_day is not None:
        better.append(other_day)

    settings = evaluator.settings
    better.sort(key=lambda pair: _rank(pair[0], pair[1], enquiry, settings))
    return tuple(_describe(c, r, enquiry) for c, r in better[:MAX_ALTERNATIVES])


def _same_day_candidates(enquiry: Enquiry) -> list[_Candidate]:
    """Shift time (same type, ±30 and ±60 min) and change type (same start, upgrades only).

    There is no "join a nearby session" candidate: a zone taken by a group is sold out (SPEC §2).
    A downgrade (lower level, or losing the Chef's Table) is never suggested.
    """
    candidates = [
        _Candidate(
            date=enquiry.date,
            start=enquiry.start + shift,
            ticket_type=enquiry.ticket_type,
            shift_steps=abs(shift) // GRID,
            type_changed=False,
            day_offset=0,
        )
        for shift in TIME_SHIFTS
    ]
    candidates += [
        _Candidate(
            date=enquiry.date,
            start=enquiry.start,
            ticket_type=ticket_type,
            shift_steps=0,
            type_changed=True,
            day_offset=0,
        )
        for ticket_type in TICKET_TYPES
        if ticket_type != enquiry.ticket_type and not is_downgrade(enquiry.ticket_type, ticket_type)
    ]
    return candidates


def _nearest_better_day(
    evaluator: Evaluator, enquiry: Enquiry, own_quality: tuple[int, int, int]
) -> tuple[_Candidate, EnquiryResult] | None:
    """4. Another day: same start and type on +1, −1, +2, −2 ... days. Only the nearest one."""
    if not is_valid_date(enquiry.date):
        return None
    for distance in range(1, evaluator.settings.alternative_day_search + 1):
        for offset in (distance, -distance):
            candidate = _Candidate(
                date=add_days(enquiry.date, offset),
                start=enquiry.start,
                ticket_type=enquiry.ticket_type,
                shift_steps=0,
                type_changed=False,
                day_offset=offset,
            )
            # Days outside the booking window or in the past come back NOT_POSSIBLE.
            result = evaluator.evaluate(candidate.as_enquiry(enquiry))
            if _quality(result) < own_quality:
                return candidate, result
    return None


def _revenue_cents(candidate: _Candidate, enquiry: Enquiry, settings: Settings) -> int:
    if settings.prices is None or candidate.ticket_type not in settings.prices:
        return 0
    price = settings.prices[candidate.ticket_type]
    return enquiry.adults * price.adult_cents + enquiry.children * price.child_cents


def _rank(
    candidate: _Candidate, result: EnquiryResult, enquiry: Enquiry, settings: Settings
) -> tuple[int, ...]:
    """Sort key, best first (SPEC §4.4 'better' ordering).

    After the spec's criteria, ties go to the earlier date, earlier start, shorter ticket,
    so results are always in the same order.
    """
    return (
        *_quality(result),
        candidate.change_size,
        candidate.change_kind,
        -_revenue_cents(candidate, enquiry, settings),
        candidate.day_offset,
        candidate.start,
        TICKET_TYPES.index(candidate.ticket_type),  # shortest first, then without Chef's Table
    )


def _describe(candidate: _Candidate, result: EnquiryResult, enquiry: Enquiry) -> Alternative:
    changes: list[str] = []
    if candidate.day_offset:
        changes.append(
            f"{weekday(candidate.date)} {candidate.date} instead of "
            f"{weekday(enquiry.date)} {enquiry.date}"
        )
    if candidate.start != enquiry.start:
        changes.append(
            f"start {format_time(candidate.start)} instead of {format_time(enquiry.start)}"
        )
    if candidate.type_changed:
        changes.append(
            f"{ticket_label(candidate.ticket_type)} instead of {ticket_label(enquiry.ticket_type)}"
        )
    change_summary = ", ".join(changes)
    change_summary = change_summary[:1].upper() + change_summary[1:]

    if result.verdict == "ACCEPT_GOOD":
        gain_summary = "Fits with no unsellable gaps"
    else:
        gain_summary = result.reason

    return Alternative(
        date=candidate.date,
        start=candidate.start,
        ticket_type=candidate.ticket_type,
        verdict=result.verdict,
        change_summary=change_summary,
        gain_summary=gain_summary,
    )
