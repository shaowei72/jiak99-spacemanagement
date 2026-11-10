"""Reads (and writes) an .ics calendar file saved from Outlook (SPEC §5.4 take-over, `--from-ics`).

Only what the take-over needs is read: id, subject, body, start, end, busy status, cancelled and
whether the event repeats. Like the rest of the package there are no `datetime` objects:

- Times written with a time zone (`DTSTART;TZID=Singapore Standard Time:20261012T100000`) are
  taken as written: Outlook saves them in the venue's own time.
- Times in UTC (ending in 'Z') are moved to Singapore time (UTC+8, no daylight saving).
- A date without a time (`DTSTART;VALUE=DATE:20261008`) is an all-day event.

A repeating event (RRULE) is read once, at its first date; its repeats are not worked out.
"""

import re
from dataclasses import dataclass

from engine.dates import add_days, days_between, is_valid_date

MINUTES_PER_DAY = 24 * 60
SINGAPORE_UTC_OFFSET = 8 * 60  # minutes

# 'NAME;PARAM=value;PARAM="quoted value":the value'
_PROPERTY = re.compile(r'^([A-Za-z0-9-]+)((?:;[A-Za-z0-9-]+=(?:"[^"]*"|[^";:]*))*):(.*)$')
_DATE_TIME = re.compile(r"^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})(Z?))?$")


class IcsError(Exception):
    """The file isn't a calendar file the app can read; the message says where."""


@dataclass(frozen=True)
class IcsEvent:
    """One event from the file.

    `start` and `end` are minutes since midnight of `date`; `end` goes past 1440 when the event ends
    on a later day (an all-day event runs from 0 to 1440 per day). `busy_status` is Outlook's
    FREE / TENTATIVE / BUSY / OOF, or '' when not given.
    """

    uid: str
    date: str
    start: int
    end: int
    subject: str
    body: str = ""
    all_day: bool = False
    busy_status: str = ""
    cancelled: bool = False
    recurring: bool = False


def unfold(text: str) -> list[str]:
    """The file's lines, with long lines joined back together (a line starting with a space or a
    tab continues the line before it)."""
    lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw_line.startswith((" ", "\t")) and lines:
            lines[-1] += raw_line[1:]
        else:
            lines.append(raw_line)
    return lines


def unescape(value: str) -> str:
    r"""Undo the .ics text escapes: '\n' -> new line, '\,' -> ',', '\;' -> ';', '\\' -> '\'."""
    result: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character == "\\" and index + 1 < len(value):
            following = value[index + 1]
            result.append("\n" if following in "nN" else following)
            index += 2
        else:
            result.append(character)
            index += 1
    return "".join(result)


def escape(value: str) -> str:
    r"""The opposite of `unescape`, for writing a file."""
    value = value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return value.replace("\r\n", "\n").replace("\n", "\\n")


def _read_when(value: str, line_number: int) -> tuple[str, int, bool]:
    """'(date, minute, all_day)' from a DTSTART / DTEND value such as '20261012T100000'."""
    match = _DATE_TIME.match(value.strip())
    if match is None:
        raise IcsError(f"line {line_number}: can't read the date/time {value!r}")
    year, month, day, hour, minute, _second, utc = match.groups()
    date = f"{year}-{month}-{day}"
    if not is_valid_date(date):
        raise IcsError(f"line {line_number}: not a real date {value!r}")
    if hour is None:  # a date only: an all-day event
        return date, 0, True
    minutes = int(hour) * 60 + int(minute)
    if utc:
        minutes += SINGAPORE_UTC_OFFSET
        if minutes >= MINUTES_PER_DAY:
            minutes -= MINUTES_PER_DAY
            date = add_days(date, 1)
    return date, minutes, False


def _event_from(properties: dict[str, tuple[str, str, int]], line_number: int) -> IcsEvent:
    """Build one event from its properties: name -> (params, value, line number)."""
    if "DTSTART" not in properties:
        raise IcsError(f"line {line_number}: event without a start (DTSTART)")
    _, value, number = properties["DTSTART"]
    date, start, all_day = _read_when(value, number)

    if "DTEND" in properties:
        _, value, number = properties["DTEND"]
        end_date, end_minute, _ = _read_when(value, number)
        end = days_between(date, end_date) * MINUTES_PER_DAY + end_minute
    elif all_day:
        end = MINUTES_PER_DAY
    else:
        end = start

    uid = properties.get("UID", ("", "", 0))[1].strip()
    if "RECURRENCE-ID" in properties:
        uid = f"{uid}/{properties['RECURRENCE-ID'][1].strip()}"

    def text(name: str) -> str:
        return unescape(properties[name][1]) if name in properties else ""

    return IcsEvent(
        uid=uid,
        date=date,
        start=start,
        end=end,
        subject=text("SUMMARY").strip(),
        body=text("DESCRIPTION").strip(),
        all_day=all_day,
        busy_status=text("X-MICROSOFT-CDO-BUSYSTATUS").strip().upper(),
        cancelled=text("STATUS").strip().upper() == "CANCELLED",
        recurring="RRULE" in properties,
    )


def parse_ics(text: str) -> list[IcsEvent]:
    """All events in an .ics file, in the order they appear. Raises `IcsError`."""
    lines = unfold(text)
    if not any(line.strip().upper() == "BEGIN:VCALENDAR" for line in lines):
        raise IcsError("not a calendar file (no BEGIN:VCALENDAR)")

    events: list[IcsEvent] = []
    properties: dict[str, tuple[str, str, int]] | None = None
    nesting = 0  # parts inside an event, such as an alarm (BEGIN:VALARM), are skipped
    for line_number, line in enumerate(lines, start=1):
        upper = line.strip().upper()
        if upper == "BEGIN:VEVENT":
            properties = {}
            nesting = 0
            continue
        if properties is None:
            continue
        if upper == "END:VEVENT":
            events.append(_event_from(properties, line_number))
            properties = None
        elif upper.startswith("BEGIN:"):
            nesting += 1
        elif upper.startswith("END:"):
            nesting -= 1
        elif nesting == 0:
            match = _PROPERTY.match(line)
            if match is not None:
                name = match[1].upper()
                properties.setdefault(name, (match[2], match[3], line_number))
    if properties is not None:
        raise IcsError("the file ends inside an event (no END:VEVENT)")
    return events


def _when_text(date: str, minutes: int, all_day: bool) -> str:
    """The value part of DTSTART / DTEND: '20261012T100000' or, for all-day, '20261012'."""
    days, minutes = divmod(minutes, MINUTES_PER_DAY)
    compact = add_days(date, days).replace("-", "")
    if all_day:
        return compact
    return f"{compact}T{minutes // 60:02d}{minutes % 60:02d}00"


def write_ics(events: list[IcsEvent], calendar_name: str = "Calendar") -> str:
    """An .ics file holding `events`, readable by `parse_ics` (used for the anonymised test file)."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:jiak99 anonymiser",
        f"X-WR-CALNAME:{escape(calendar_name)}",
    ]
    for event in events:
        if event.all_day:
            start = f"DTSTART;VALUE=DATE:{_when_text(event.date, event.start, True)}"
            end = f"DTEND;VALUE=DATE:{_when_text(event.date, event.end, True)}"
        else:
            zone = "TZID=Singapore Standard Time"
            start = f"DTSTART;{zone}:{_when_text(event.date, event.start, False)}"
            end = f"DTEND;{zone}:{_when_text(event.date, event.end, False)}"
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{event.uid}")
        lines.append(f"SUMMARY:{escape(event.subject)}")
        if event.body:
            lines.append(f"DESCRIPTION:{escape(event.body)}")
        lines.append(start)
        lines.append(end)
        if event.recurring:
            lines.append("RRULE:FREQ=WEEKLY")
        if event.cancelled:
            lines.append("STATUS:CANCELLED")
        if event.busy_status:
            lines.append(f"X-MICROSOFT-CDO-BUSYSTATUS:{event.busy_status}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
