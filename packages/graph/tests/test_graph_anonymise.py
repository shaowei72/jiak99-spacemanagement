"""Making test data from the real calendar without its personal data (SPEC §5.4 "Test data")."""

from graph.anonymise import anonymise, kept_body, made_up_name
from graph.ics import IcsEvent


def event(uid: str, subject: str, date: str = "2026-11-20", body: str = "") -> IcsEvent:
    return IcsEvent(uid=uid, date=date, start=600, end=720, subject=subject, body=body)


def test_made_up_names_never_repeat() -> None:
    names = [made_up_name(number) for number in range(200)]
    assert len(set(names)) == 200
    assert names[0] == "Bluebird Primary"
    assert names[72] == "Bluebird Primary 2"


def test_clients_are_replaced_consistently() -> None:
    events = [
        event("real-2", "[Confirmed] Tan Ah Kow Pte Ltd PHE 20 pax", date="2026-12-01"),
        event("real-1", "[Soft Block] tan ah kow pte ltd- 12 pax", date="2026-11-01"),
        event("real-3", "[Confirmed] Lim Family CHE (8 pax)"),
    ]
    result = anonymise(events, [])
    assert [e.subject for e in result.events] == [
        "[Soft Block] Bluebird Primary- 12 pax",
        "[Confirmed] Kingfisher Primary CHE (8 pax)",
        "[Confirmed] Bluebird Primary PHE 20 pax",
    ]
    assert [e.uid for e in result.events] == ["anon-1", "anon-2", "anon-3"]
    assert result.names == (
        ("tan ah kow pte ltd", "Bluebird Primary"),
        ("Lim Family", "Kingfisher Primary"),
    )
    assert result.warnings == ()


def test_client_in_pieces_is_replaced_word_by_word() -> None:
    (only,) = anonymise([event("x", "[Soft Block] PHE -seniors")], []).events
    assert only.subject == "[Soft Block] PHE -Bluebird Primary"
    (spaced,) = anonymise([event("y", "[Soft Block] PHE - Ong  Kim")], []).events
    assert spaced.subject == "[Soft Block] PHE - Bluebird Primary  Bluebird Primary"


def test_other_events_lose_their_text() -> None:
    events = [
        event("a", "Photoshoot with Mei Ling", body="call 9123 4567"),
        event("b", "Canceled: Weekly meeting"),
    ]
    result = anonymise(events, [])
    assert [(e.subject, e.body) for e in result.events] == [
        ("Other event", ""),
        ("Canceled: Other event", ""),
    ]


def test_body_keeps_only_the_pax_line_and_product_names() -> None:
    body = (
        "Contact Name: Mrs Tan\nContact Number: 9123 4567\n"
        "PREMIUM HAWKER EXPERIENCE WITH CHEF’S TABLE @$50/STUDENT\n"
        "Number of Pax: 42 students, 5 adults\nNumber of Pax: 50\n"
        "Classic Hawker Experience\nclassic hawker, once more\nPremium hawker again"
    )
    assert kept_body(body) == (
        "Premium Hawker Experience with Chef's Table\n"
        "Number of Pax: 42 students, 5 adults\n"
        "Classic Hawker Experience\n"
        "Premium Hawker Experience"
    )


def test_warnings_for_anything_that_looks_personal() -> None:
    events = [
        event("a", "[Confirmed] 2 adults PHE + CT"),
        event("b", "[Confirmed] Bluebird PHE 9123 4567"),
        event("c", "[Confirmed] Bluebird PHE x@y.com 4 pax"),
        event("d", "[Confirmed] Bluebird PHE see www.example.com 4 pax"),
        event("e", "[Confirmed] Bluebird PHE +6591234567 4 pax"),
    ]
    result = anonymise(events, [])
    assert result.warnings == (
        "anon-1 2026-11-20: no client found, subject kept as typed",
        "anon-2 2026-11-20: subject has what looks like a phone number",
        "anon-3 2026-11-20: subject has what looks like an email address",
        "anon-4 2026-11-20: subject has what looks like an link",
        "anon-5 2026-11-20: subject has what looks like a phone number",
    )
