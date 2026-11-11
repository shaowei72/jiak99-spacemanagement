"""Reads the bookings typed by hand into the operations@ calendar before the app (SPEC §5.4 take-over).

The old entries are free text, for example:

    [Confirmed] Bluebird Primary- 18 students + 2 teachers= 20 pax (PHE + CT)
    [Soft Block] Kingfisher Travel PHE 44pax
    Canceled: [Soft Block] Pandan CC

The reader is forgiving: it takes what it can find and leaves the rest empty, so a person can fill
it in on the review sheet. It finds:

- the status tag ('[Confirmed]', '[Soft Block]', '[Soft Book]', '[Rescheduled]') and 'Canceled:';
- the headcount, from the subject, else from a 'Number of Pax: ...' line in the body;
- the ticket type, from ticket words in the subject (PHE, CHE, Premium, Classic, Tingkat, + CT),
  else from the client map (a small settings file), else from a product name in the body
  ('Premium Hawker Experience with Chef's Table');
- the client: the start of the subject, up to the first ticket word, headcount, '-' or '('.
"""

import re
import tomllib
from dataclasses import dataclass

from engine.rules import TICKET_TYPES, TicketType

STATUS_CONFIRMED = "confirmed"
STATUS_SOFT_BLOCK = "soft block"
STATUS_RESCHEDULED = "rescheduled"
BOOKING_STATUSES = (STATUS_CONFIRMED, STATUS_SOFT_BLOCK, STATUS_RESCHEDULED)

# Words that count people. Children words are counted as children, the rest as adults.
_PAX_WORDS = ("pax", "people", "person", "persons")
_CHILD_WORDS = ("student", "students", "kid", "kids", "child", "children", "baby", "babies")
_ADULT_WORDS = (
    "adult", "adults", "teacher", "teachers", "parent", "parents", "senior", "seniors",
    "staff", "guest", "guests",
)  # fmt: skip
_PERSON_WORD = "|".join(_PAX_WORDS + _CHILD_WORDS + _ADULT_WORDS)

# '18 students', '44pax' - a number that isn't part of a word or a bigger number ('Skool4Kidz').
_COUNT = re.compile(rf"(?<![\w.])(\d{{1,4}})\s*({_PERSON_WORD})(?![a-z])", re.IGNORECASE)
# '= 20 pax', '=29 students', '= 30'
_TOTAL = re.compile(rf"=\s*(\d{{1,4}})\s*({_PERSON_WORD})?(?![a-z])", re.IGNORECASE)
# 'Number of Pax: ...', 'No. of pax: ...', 'Pax: ...' at the start of a body line
PAX_LINE = re.compile(r"^\s*(?:(?:number|no\.?)\s+of\s+)?pax\s*[:：]\s*(.*)$", re.IGNORECASE)
_FIRST_NUMBER = re.compile(r"(?<![\w.])(\d{1,4})(?![\d])")

_TICKET_WORD = re.compile(r"(?<![a-z])(phe|che|premium|classic|tingkat)(?![a-z])", re.IGNORECASE)
CHEFS_TABLE = re.compile(r"(?<![a-z])(ct|chef['’]?s?\s*table)(?![a-z])", re.IGNORECASE)
PRODUCT = re.compile(r"(?<![a-z])(premium|classic)\s+hawker(?![a-z])", re.IGNORECASE)
_PRODUCT_WORD = re.compile(r"(?<![a-z])(premium|classic)(?![a-z])", re.IGNORECASE)

_CANCELLED = re.compile(r"^\s*cancell?ed\s*:\s*", re.IGNORECASE)
_TAG = re.compile(r"^\s*\[\s*([^\]\[]*?)\s*\]\s*")
_LOOSE_TAG = re.compile(r"^\s*\[?\s*(confirmed|soft\s*block|soft\s*book)(?![a-z])\s*\]?\s*", re.I)
_RESCHEDULED_PREFIX = re.compile(r"^\s*rescheduled\s*[-:–]?\s*", re.IGNORECASE)
_CLIENT_STOP = re.compile(r"\s[-–]|[-–]\s|\(|=|\.\s")


class ClientMapError(Exception):
    """The client map file can't be used; the message says why."""


@dataclass(frozen=True)
class ClientRule:
    """A client whose entries name no ticket type. Matches when every word in `words` is in the
    subject (any case; spaces and punctuation ignored)."""

    name: str
    words: tuple[str, ...]
    ticket_type: TicketType


@dataclass(frozen=True)
class Headcount:
    """How many people an entry is for."""

    adults: int
    children: int

    @property
    def people(self) -> int:
        """Adults and children together."""
        return self.adults + self.children


@dataclass(frozen=True)
class OldEntry:
    """What the reader found in one old calendar entry. Empty strings / None = not found.

    `status` is 'confirmed', 'soft block', 'rescheduled', another tag in lower case (e.g.
    'site visit') or ''. `people_found` is 'subject' or 'body'; `ticket_found` says how the ticket
    was found ('subject', 'client map: <name>', 'body'). `notes` are things a person should look at.
    `body_counts` are headcounts mentioned anywhere in the body ('5pax', '60 Students'), as hints
    for a person when no headcount was found; they are not used on their own.
    """

    booking_like: bool
    status: str
    cancelled: bool
    headcount: Headcount | None
    people_found: str
    ticket_type: TicketType | None
    ticket_found: str
    client: str
    notes: tuple[str, ...] = ()
    body_counts: tuple[str, ...] = ()


def squeeze(text: str) -> str:
    """Lower case, with everything but letters and digits removed: 'Tour Group!' -> 'tourgroup'."""
    return "".join(character for character in text.lower() if character.isalnum())


def parse_client_map(text: str) -> list[ClientRule]:
    """Read the client map (TOML). Each `[[client]]` has `name`, `words` and `ticket` (a ticket
    code such as "TINGKAT"). Raises `ClientMapError`."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ClientMapError(f"not valid TOML: {error}") from error
    rules: list[ClientRule] = []
    for number, item in enumerate(data.get("client", []), start=1):
        name = item.get("name")
        words = item.get("words")
        ticket = item.get("ticket")
        if not isinstance(name, str) or not name.strip():
            raise ClientMapError(f"client {number}: needs a name")
        if not isinstance(words, list) or not words or not all(isinstance(w, str) for w in words):
            raise ClientMapError(f"client {name!r}: `words` must be a list of words")
        if ticket not in TICKET_TYPES:
            codes = ", ".join(TICKET_TYPES)
            raise ClientMapError(f"client {name!r}: ticket must be one of {codes}, not {ticket!r}")
        squeezed = tuple(squeeze(word) for word in words)
        if not all(squeezed):
            raise ClientMapError(f"client {name!r}: a word has no letters or digits")
        rules.append(ClientRule(name=name.strip(), words=squeezed, ticket_type=ticket))
    return rules


def _split(counts: list[tuple[int, str]]) -> Headcount:
    """Add up '(number, word)' pairs into adults and children."""
    adults = 0
    children = 0
    for number, word in counts:
        if word.lower() in _CHILD_WORDS:
            children += number
        else:
            adults += number
    return Headcount(adults=adults, children=children)


def _counts(text: str) -> list[tuple[int, str]]:
    """Every '18 students' in the text, as (18, 'students')."""
    return [(int(match[1]), match[2]) for match in _COUNT.finditer(text)]


def read_headcount(text: str) -> Headcount | None:
    """The headcount in a piece of free text, or None.

    '18 students + 2 teachers= 20 pax' -> 2 adults, 18 children (the total after '=' wins; the parts
    are used when they add up to it). '86 pax including teachers' -> 86 adults (a 'pax' number is a
    total). '42 students, 5 adults' -> 5 adults, 42 children.
    """
    total_match = _TOTAL.search(text)
    if total_match is not None:
        total = int(total_match[1])
        if total == 0:
            return None
        parts = _counts(text[: total_match.start()])
        if parts and sum(number for number, _ in parts) == total:
            return _split(parts)
        word = (total_match[2] or "pax").lower()
        if word in _CHILD_WORDS:
            return Headcount(adults=0, children=total)
        return Headcount(adults=total, children=0)

    parts = [(number, word) for number, word in _counts(text) if number > 0]
    if not parts:
        return None
    for number, word in parts:
        if word.lower() in _PAX_WORDS:
            return Headcount(adults=number, children=0)
    return _split(parts)


def read_body_headcount(body: str) -> Headcount | None:
    """The headcount from the first 'Number of Pax: ...' line of a body that has one, or None.
    After that label a bare number counts as people: 'Pax: Minimum 30 (Estimate)' -> 30."""
    for line in body.splitlines():
        match = PAX_LINE.match(line)
        if match is None:
            continue
        found = read_headcount(match[1])
        if found is not None:
            return found
        number = _FIRST_NUMBER.search(match[1])
        if number is not None and int(number[1]) > 0:
            return Headcount(adults=int(number[1]), children=0)
    return None


def _ticket(premium: bool, classic: bool, tingkat: bool, chefs_table: bool) -> TicketType | None:
    """The ticket type from the words found; None when they don't name exactly one."""
    if premium and classic:
        return None
    if premium:
        return "PREMIUM_CT" if chefs_table else "PREMIUM"
    if classic:
        return "CLASSIC_CT" if chefs_table else "CLASSIC"
    if tingkat:
        return "TINGKAT"
    return None


def subject_ticket(subject: str) -> tuple[TicketType | None, str]:
    """(ticket type or None, a note for the review sheet or '') from the words in a subject.

    'PHE + CT' -> PREMIUM_CT, 'CHE' -> CLASSIC, 'Tingkat' -> TINGKAT (only when no PHE/CHE is named:
    every experience includes a Tingkat meal).
    """
    words = {match[1].lower() for match in _TICKET_WORD.finditer(subject)}
    premium = bool(words & {"phe", "premium"})
    classic = bool(words & {"che", "classic"})
    chefs_table = CHEFS_TABLE.search(subject) is not None
    if premium and classic:
        return None, "subject names both PHE and CHE"
    if chefs_table and not (premium or classic):
        return None, "subject has CT but no PHE/CHE"
    return _ticket(premium, classic, "tingkat" in words, chefs_table), ""


def body_ticket(body: str) -> tuple[TicketType | None, str]:
    """(ticket type or None, note) from product names in a body: 'PREMIUM HAWKER EXPERIENCE WITH
    CHEF'S TABLE' -> PREMIUM_CT. A Chef's Table counts only on the same line as the product name."""
    found: set[TicketType] = set()
    for line in body.splitlines():
        if PRODUCT.search(line) is None:
            continue
        # 'classic/premium hawker experience' names both, so look at the whole line
        products = {match[1].lower() for match in _PRODUCT_WORD.finditer(line)}
        chefs_table = CHEFS_TABLE.search(line) is not None
        ticket = _ticket("premium" in products, "classic" in products, False, chefs_table)
        if ticket is None:
            return None, "body names both Premium and Classic"
        found.add(ticket)
    if len(found) > 1:
        return None, "body names more than one ticket"
    if found:
        return found.pop(), ""
    return None, ""


def client_map_ticket(subject: str, rules: list[ClientRule]) -> ClientRule | None:
    """The first client rule whose words are all in the subject, or None."""
    squeezed = squeeze(subject)
    for rule in rules:
        if all(word in squeezed for word in rule.words):
            return rule
    return None


def read_status(subject: str) -> tuple[str, bool, str]:
    """(status, cancelled, the rest of the subject). 'Canceled: [Soft Block] Pandan CC' ->
    ('soft block', True, 'Pandan CC'). Status is '' when there is no tag."""
    cancelled = False
    match = _CANCELLED.match(subject)
    if match is not None:
        cancelled = True
        subject = subject[match.end() :]

    tag = ""
    match = _TAG.match(subject)
    if match is None:
        match = _LOOSE_TAG.match(subject)
    if match is not None:
        tag = " ".join(match[1].lower().split())
        subject = subject[match.end() :]

    if tag == "confirmed":
        status = STATUS_CONFIRMED
    elif tag.startswith("soft"):
        status = STATUS_SOFT_BLOCK
    elif tag == "rescheduled":
        status = STATUS_RESCHEDULED
    else:
        status = tag
    return status, cancelled, subject.strip()


def _first_stop(text: str) -> int:
    """Where the client name ends: the first ticket word, Chef's Table, headcount, ' - ', '(', '='
    or '. ' in `text` (its length if there is none)."""
    positions = [len(text)]
    for pattern in (_TICKET_WORD, CHEFS_TABLE, _COUNT, _CLIENT_STOP):
        match = pattern.search(text)
        if match is not None:
            positions.append(match.start())
    return min(positions)


def read_client(rest: str) -> str:
    """The client name from a subject without its status tag: 'Bluebird Primary- 20 pax (PHE)' ->
    'Bluebird Primary'. When the subject starts with a ticket word or headcount ('PHE -seniors'),
    those are taken out and what is left is the client ('seniors'); '' when nothing is left."""
    match = _RESCHEDULED_PREFIX.match(rest)
    if match is not None:
        rest = rest[match.end() :]
    client = rest[: _first_stop(rest)].strip(" -–:,.+")
    if client:
        return client
    left = rest
    for pattern in (_COUNT, _TICKET_WORD, CHEFS_TABLE):
        left = pattern.sub(" ", left)
    left = re.sub(r"[()\-–+=:,.]", " ", left)
    return " ".join(left.split())


def body_count_hints(body: str, most: int = 5) -> tuple[str, ...]:
    """Up to `most` different headcounts mentioned anywhere in a body, as written: ('5pax', '18pax')."""
    hints: list[str] = []
    for match in _COUNT.finditer(body):
        hint = " ".join(match[0].split())
        if hint not in hints:
            hints.append(hint)
        if len(hints) == most:
            break
    return tuple(hints)


def read_old_entry(subject: str, body: str, rules: list[ClientRule]) -> OldEntry:
    """Read one old calendar entry. `booking_like` is True when the subject has a booking status
    tag, a headcount or a ticket word; anything else (meetings, site visits) is left alone."""
    status, cancelled, rest = read_status(subject)
    notes: list[str] = []

    subject_count = read_headcount(rest)
    body_count = read_body_headcount(body)
    headcount = subject_count
    people_found = "subject" if subject_count is not None else ""
    if subject_count is None and body_count is not None:
        headcount = body_count
        people_found = "body"
    elif (
        subject_count is not None
        and body_count is not None
        and body_count.people != subject_count.people
    ):
        notes.append(f"body says {body_count.people} people")

    ticket_type, note = subject_ticket(rest)
    has_ticket_words = _TICKET_WORD.search(rest) is not None or CHEFS_TABLE.search(rest) is not None
    ticket_found = "subject" if ticket_type is not None else ""
    if note:
        notes.append(note)
    if ticket_type is None:
        rule = client_map_ticket(rest, rules)
        if rule is not None:
            ticket_type = rule.ticket_type
            ticket_found = f"client map: {rule.name}"
    if ticket_type is None:
        ticket_type, note = body_ticket(body)
        ticket_found = "body" if ticket_type is not None else ""
        if note:
            notes.append(note)

    booking_like = status in BOOKING_STATUSES or subject_count is not None or has_ticket_words
    return OldEntry(
        booking_like=booking_like,
        status=status,
        cancelled=cancelled,
        headcount=headcount,
        people_found=people_found,
        ticket_type=ticket_type,
        ticket_found=ticket_found,
        client=read_client(rest),
        notes=tuple(notes),
        body_counts=body_count_hints(body) if headcount is None else (),
    )
