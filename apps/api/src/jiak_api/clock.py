"""What "today" and "now" are, in Singapore time. Tests replace this with a fixed clock."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

SINGAPORE = timezone(timedelta(hours=8))  # UTC+8, no daylight saving


@dataclass(frozen=True)
class Now:
    """The current Singapore date and minute, in the engine's format."""

    today: str  # 'YYYY-MM-DD'
    minute: int  # minutes since midnight


def singapore_now() -> Now:
    """The real current time in Singapore."""
    moment = datetime.now(SINGAPORE)
    return Now(today=moment.strftime("%Y-%m-%d"), minute=moment.hour * 60 + moment.minute)
