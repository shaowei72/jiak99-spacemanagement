"""Makes test data from the real calendar without its personal data (SPEC §5.4 "Test data").

The GitHub repo is public, so the real calendar file never goes into it. This turns it into a copy
that can, after the owner has read it:

- booking-like events keep their date, times, status tag, ticket words and headcount, but the
  client name is replaced by a made-up one (the same client always gets the same made-up name);
- the body is dropped, except a 'Number of Pax: ...' line and a product name ('Premium Hawker
  Experience with Chef's Table'), which the reader uses;
- other events (meetings, site visits) become 'Other event', with no body;
- event ids become 'anon-1', 'anon-2', ...

Anything left that looks like a phone number, an email address or a link is listed as a warning.
"""

import re
from dataclasses import dataclass, replace

from graph.ics import IcsEvent
from graph.oldstyle import CHEFS_TABLE, PAX_LINE, PRODUCT, ClientRule, read_old_entry

_FIRST_NAMES = (
    "Bluebird", "Kingfisher", "Hornbill", "Pandan", "Rambutan", "Banyan", "Lotus", "Mangrove",
    "Starfruit", "Jasmine", "Tembusu", "Sunbird",
)  # fmt: skip
_SECOND_NAMES = ("Primary", "Preschool", "Travel", "Holdings", "Community Club", "Academy")

_PHONE = re.compile(r"(?<!\d)\d{4}\s?\d{4}(?!\d)|\+\d{6,}")
_LEAKS = (("email address", re.compile(r"@")), ("link", re.compile(r"https?://|www\.", re.I)))


@dataclass(frozen=True)
class Anonymised:
    """The anonymised events, the client names that were replaced (real -> made-up, for the
    owner's check; never commit this list) and warnings to look at before committing."""

    events: tuple[IcsEvent, ...]
    names: tuple[tuple[str, str], ...]
    warnings: tuple[str, ...]


def made_up_name(number: int) -> str:
    """The made-up client name number `number` (0, 1, 2, ...): 'Bluebird Primary', ..."""
    first = _FIRST_NAMES[number % len(_FIRST_NAMES)]
    second = _SECOND_NAMES[(number // len(_FIRST_NAMES)) % len(_SECOND_NAMES)]
    rounds = number // (len(_FIRST_NAMES) * len(_SECOND_NAMES))
    if rounds:
        return f"{first} {second} {rounds + 1}"
    return f"{first} {second}"


def kept_body(body: str) -> str:
    """The parts of a body that the reader uses and hold no personal data: the first 'Number of
    Pax' line, and each product name, written plainly ('Premium Hawker Experience with Chef's
    Table')."""
    kept: list[str] = []
    for line in body.splitlines():
        if PAX_LINE.match(line) is not None and not any(PAX_LINE.match(k) for k in kept):
            kept.append(line.strip())
        for match in PRODUCT.finditer(line):
            product = f"{match[1].capitalize()} Hawker Experience"
            if CHEFS_TABLE.search(line) is not None:
                product += " with Chef's Table"
            if product not in kept:
                kept.append(product)
    return "\n".join(kept)


def _replace_client(subject: str, client: str, fake: str) -> str:
    """`subject` with `client` (any case) replaced by `fake`. When the client isn't one piece of the
    subject, each of its words is replaced instead."""
    start = subject.lower().find(client.lower())
    if start >= 0:
        return subject[:start] + fake + subject[start + len(client) :]
    for word in client.split():
        subject = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", fake, subject, flags=re.I)
    return subject


def _leak_warnings(event: IcsEvent) -> list[str]:
    """Warnings for anything in the event that looks like personal data."""
    warnings: list[str] = []
    for part, text in (("subject", event.subject), ("body", event.body)):
        if _PHONE.search(text) is not None:
            warnings.append(f"{event.uid} {event.date}: {part} has what looks like a phone number")
        for what, pattern in _LEAKS:
            if pattern.search(text) is not None:
                warnings.append(f"{event.uid} {event.date}: {part} has what looks like an {what}")
    return warnings


def anonymise(events: list[IcsEvent], rules: list[ClientRule]) -> Anonymised:
    """The anonymised copy of `events` (see the module docstring), sorted by date and start."""
    names: dict[str, str] = {}  # lower-case real client -> made-up name
    real_names: dict[str, str] = {}  # lower-case real client -> as first written
    result: list[IcsEvent] = []
    warnings: list[str] = []

    for number, event in enumerate(sorted(events, key=lambda e: (e.date, e.start)), start=1):
        new_id = f"anon-{number}"
        entry = read_old_entry(event.subject, event.body, rules)
        if not entry.booking_like:
            subject = "Canceled: Other event" if entry.cancelled else "Other event"
            result.append(replace(event, uid=new_id, subject=subject, body=""))
            continue

        subject = event.subject
        if entry.client:
            key = entry.client.lower()
            if key not in names:
                names[key] = made_up_name(len(names))
                real_names[key] = entry.client
            subject = _replace_client(subject, entry.client, names[key])
        else:
            warnings.append(f"{new_id} {event.date}: no client found, subject kept as typed")
        anonymous = replace(event, uid=new_id, subject=subject, body=kept_body(event.body))
        warnings.extend(_leak_warnings(anonymous))
        result.append(anonymous)

    pairs = tuple((real_names[key], fake) for key, fake in names.items())
    return Anonymised(events=tuple(result), names=pairs, warnings=tuple(warnings))
