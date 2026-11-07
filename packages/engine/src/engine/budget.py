"""Time budget for the zone-assignment search (SPEC §4.1).

The only place the engine reads a clock. Tests can pass in a fake clock.
"""

import time
from collections.abc import Callable


class SearchBudget:
    """A deadline shared by all `assign` calls made while checking one enquiry.

    When it runs out, searches stop improving their answer and return the best one found so far.
    Feasibility (does it fit at all?) is always decided exactly, whatever the budget.
    """

    def __init__(self, milliseconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._deadline = clock() + milliseconds / 1000

    def expired(self) -> bool:
        return self._clock() >= self._deadline
