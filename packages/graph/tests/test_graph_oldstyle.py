"""The forgiving reader for old hand-typed calendar entries (SPEC §5.4 take-over, step 2).

The subjects here copy the shapes found in the real calendar, with made-up names.
"""

import pytest

from engine.rules import TicketType
from graph.oldstyle import (
    ClientMapError,
    ClientRule,
    Headcount,
    OldEntry,
    body_count_hints,
    body_ticket,
    parse_client_map,
    read_body_headcount,
    read_client,
    read_headcount,
    read_old_entry,
    read_status,
    squeeze,
    subject_ticket,
)

SUNSHINE = ClientRule(
    name="Sunshine TourGroup", words=("sunshine", "tourgroup"), ticket_type="TINGKAT"
)


@pytest.mark.parametrize(
    ("text", "adults", "children"),
    [
        ("18 students + 2 teachers= 20 pax", 2, 18),  # parts add up to the total
        ("42 students, 5 adults = 47 pax", 5, 42),
        ("27 students+ 12 parents = 27 students", 0, 27),  # parts don't add up: the total wins
        ("30 + 6 staff= 36 pax", 36, 0),
        ("CHE + CT=29 students", 0, 29),
        ("(PHE + CT)= 30 pax", 30, 0),
        ("PHE-86 pax including teachers", 86, 0),  # a 'pax' number is a total
        ("MICE PHE 44pax", 44, 0),
        ("PHE 77 kids", 0, 77),
        ("PHE-34 students", 0, 34),
        ("2 adults PHE + CT", 2, 0),
        ("42 students, 5 adults", 5, 42),
        ("Pandan CC 1 person", 1, 0),
    ],
)
def test_headcounts(text: str, adults: int, children: int) -> None:
    assert read_headcount(text) == Headcount(adults=adults, children=children)


@pytest.mark.parametrize(
    "text",
    [
        "Bluebird Primary",
        "Skool4Kidz CHE",  # a number inside a word isn't a headcount
        "Weekly JIAK 99 meeting",  # a number without a people word isn't either
        "Visit for 22 July 27 event",
        "Bluebird = 0 pax",
        "0 pax",
        "1.5 pax",
    ],
)
def test_no_headcount(text: str) -> None:
    assert read_headcount(text) is None


def test_headcount_total_without_word_means_people() -> None:
    assert read_headcount("Bluebird = 12") == Headcount(adults=12, children=0)


def test_headcount_people_property() -> None:
    assert Headcount(adults=2, children=3).people == 5


def test_body_headcount_comes_from_the_pax_line() -> None:
    body = (
        "Date of Event: 12 Oct\nTime: 10am, 3 buses\nNumber of Pax: 73 students, 13 adults\nPax: 5"
    )
    assert read_body_headcount(body) == Headcount(adults=13, children=73)
    assert read_body_headcount("No. of pax: 44pax ( 35 Chd + 9 Adt )") == Headcount(44, 0)
    assert read_body_headcount("Pax : Minimum 30 (Estimate)") == Headcount(30, 0)
    assert read_body_headcount("Pax：12") == Headcount(12, 0)


def test_body_headcount_skips_pax_lines_without_numbers() -> None:
    assert read_body_headcount("Number of Pax: TBC\nNumber of Pax: 0\nPax: 9 pax") == Headcount(
        9, 0
    )
    assert read_body_headcount("About 80 agents for lunch") is None
    assert read_body_headcount("Number of Pax: tbc") is None


@pytest.mark.parametrize(
    ("subject", "ticket"),
    [
        ("Bluebird PHE + CT", "PREMIUM_CT"),
        ("Bluebird PHE+ CT voucher", "PREMIUM_CT"),
        ("Bluebird (PHE)", "PREMIUM"),
        ("Bluebird -PHE", "PREMIUM"),
        ("Bluebird PHE Only", "PREMIUM"),
        ("Bluebird CHE+CT-36 students", "CLASSIC_CT"),
        ("Bluebird CHE + CT=29 students", "CLASSIC_CT"),
        ("Bluebird CHE", "CLASSIC"),
        ("Bluebird Premium with Chef's Table", "PREMIUM_CT"),
        ("Bluebird classic chefs table", "CLASSIC_CT"),
        ("Bluebird Tingkat", "TINGKAT"),
        (
            "Bluebird PHE. wants tingkat first",
            "PREMIUM",
        ),  # every experience includes a Tingkat meal
    ],
)
def test_ticket_words_in_subject(subject: str, ticket: TicketType) -> None:
    assert subject_ticket(subject) == (ticket, "")


@pytest.mark.parametrize(
    ("subject", "note"),
    [
        ("Bluebird School House", ""),
        ("Bluebird School House (Tour + CT)", "subject has CT but no PHE/CHE"),
        ("Bluebird PHE or CHE", "subject names both PHE and CHE"),
        ("SDC Holdings, Cheers, Phew", ""),  # ticket words only count as whole words
    ],
)
def test_no_ticket_in_subject(subject: str, note: str) -> None:
    assert subject_ticket(subject) == (None, note)


def test_ticket_from_product_names_in_body() -> None:
    assert body_ticket("PREMIUM HAWKER EXPERIENCE WITH CHEF’S TABLE @$50/STUDENT") == (
        "PREMIUM_CT",
        "",
    )
    assert body_ticket("Hi,\nClassic Hawker Experience\nCT- popiah") == ("CLASSIC", "")
    assert body_ticket("Premium Hawker Exp\nPremium Hawker Experience") == ("PREMIUM", "")
    assert body_ticket("Tingkat Meal\nNasi lemak") == (None, "")


def test_body_naming_several_tickets_gives_none() -> None:
    assert body_ticket("keen on your classic/premium hawker experience")[0] is None
    assert body_ticket("Classic Hawker Experience\nPremium Hawker Experience") == (
        None,
        "body names more than one ticket",
    )
    assert body_ticket("Your classic hawker or premium hawker?") == (
        None,
        "body names both Premium and Classic",
    )


@pytest.mark.parametrize(
    ("subject", "status", "cancelled", "rest"),
    [
        ("[Confirmed] Bluebird PHE", "confirmed", False, "Bluebird PHE"),
        ("[confirmed] 2 adults", "confirmed", False, "2 adults"),
        ("[Confirmed Bluebird (PHE) 6 pax", "confirmed", False, "Bluebird (PHE) 6 pax"),
        ("Confirmed Klook booking PHE", "confirmed", False, "Klook booking PHE"),
        ("[Soft Block] Bluebird", "soft block", False, "Bluebird"),
        ("[soft  block] Bluebird", "soft block", False, "Bluebird"),
        ("[Soft Book] Bluebird", "soft block", False, "Bluebird"),
        ("Canceled: [Soft Block] Bluebird", "soft block", True, "Bluebird"),
        ("Cancelled: Weekly meeting", "", True, "Weekly meeting"),
        ("[RESCHEDULED] Bluebird", "rescheduled", False, "Bluebird"),
        ("[Site visit] Bluebird Travel", "site visit", False, "Bluebird Travel"),
        ("Bluebird PHE", "", False, "Bluebird PHE"),
    ],
)
def test_status(subject: str, status: str, cancelled: bool, rest: str) -> None:
    assert read_status(subject) == (status, cancelled, rest)


@pytest.mark.parametrize(
    ("rest", "client"),
    [
        ("Bluebird High- 18 students + 2 teachers= 20 pax (PHE + CT)", "Bluebird High"),
        ("Bluebird Corporate Event- 19 pax", "Bluebird Corporate Event"),
        ("Bluebird Primary School PHE-86 pax including teachers", "Bluebird Primary School"),
        ("Bluebird CHE + CT- Ice Kacang", "Bluebird"),
        ("Bluebird School House (Tour + CT)", "Bluebird School House"),
        ("Bluebird Travel", "Bluebird Travel"),
        ("Bluebird Primary - 42 students, 5 adults = 47 pax", "Bluebird Primary"),
        ("Bluebird HQ 17- CHE + CT- Ondeh Ondeh (75 pax)", "Bluebird HQ 17"),
        ("Rescheduled- Bluebird CHE+CT = 27 students", "Bluebird"),
        ("Rescheduled Bluebird CHE+CT-36 students", "Bluebird"),
        ("Booked on Klook PHE. wants tingkat first (10 pax)", "Booked on Klook"),
        ("Bluebird RC (PHE + CT)= 30 pax", "Bluebird RC"),
        ("Bluebird & CO- PHE 44pax", "Bluebird & CO"),
        ("Bluebird Seniors-PHE", "Bluebird Seniors"),
        ("PHE -seniors", "seniors"),
        ("PHE-seniors", "seniors"),
        ("2 adults PHE + CT", ""),
        ("", ""),
    ],
)
def test_client(rest: str, client: str) -> None:
    assert read_client(rest) == client


def test_reads_a_whole_entry() -> None:
    entry = read_old_entry(
        "[Confirmed] Bluebird High- 18 students + 2 teachers= 20 pax (PHE + CT)",
        "Number of Pax: 18 students, 2 teachers",
        [],
    )
    assert entry == OldEntry(
        booking_like=True,
        status="confirmed",
        cancelled=False,
        headcount=Headcount(adults=2, children=18),
        people_found="subject",
        ticket_type="PREMIUM_CT",
        ticket_found="subject",
        client="Bluebird High",
    )


def test_headcount_from_body_and_a_note_when_body_differs() -> None:
    from_body = read_old_entry("[Confirmed] Bluebird PHE", "Number of Pax: 22 pax", [])
    assert from_body.headcount == Headcount(22, 0)
    assert from_body.people_found == "body"

    differs = read_old_entry(
        "[Confirmed] Bluebird PHE- 29 students", "Number of Pax: 29 children, 13 parents", []
    )
    assert differs.headcount == Headcount(0, 29)
    assert differs.people_found == "subject"
    assert differs.notes == ("body says 42 people",)


def test_ticket_from_client_map_then_body() -> None:
    rules = [SUNSHINE]
    mapped = read_old_entry("[Confirmed] Sunshine Travel Tour Group 40 pax", "", rules)
    assert (mapped.ticket_type, mapped.ticket_found) == (
        "TINGKAT",
        "client map: Sunshine TourGroup",
    )

    from_body = read_old_entry("[Confirmed] Bluebird", "PREMIUM HAWKER EXPERIENCE", rules)
    assert (from_body.ticket_type, from_body.ticket_found) == ("PREMIUM", "body")

    subject_first = read_old_entry("[Confirmed] Sunshine TourGroup CHE", "", rules)
    assert (subject_first.ticket_type, subject_first.ticket_found) == ("CLASSIC", "subject")


def test_notes_from_unclear_tickets_and_hints_for_missing_headcounts() -> None:
    entry = read_old_entry(
        "[Soft Block] Bluebird (Tour + CT)",
        "17 Nov – 5pax\n24 Nov – 18pax\n25 Nov – 5pax\nclassic or premium hawker?",
        [],
    )
    assert entry.ticket_type is None
    assert entry.notes == ("subject has CT but no PHE/CHE", "body names both Premium and Classic")
    assert entry.headcount is None
    assert entry.body_counts == ("5pax", "18pax")


def test_body_count_hints() -> None:
    assert body_count_hints("1 pax\n2 pax\n3  pax\n4 pax\n5 pax\n6 pax") == (
        "1 pax",
        "2 pax",
        "3 pax",
        "4 pax",
        "5 pax",
    )
    assert body_count_hints("nothing here") == ()


@pytest.mark.parametrize(
    ("subject", "booking_like"),
    [
        ("[Confirmed] Bluebird Travel", True),  # status tag
        ("[Soft Block] Bluebird", True),
        ("Bluebird SAFTI- 30 pax PHE", True),  # headcount
        ("Booked on Klook PHE", True),  # ticket word
        ("Bluebird (Tour + CT)", True),  # Chef's Table counts as a ticket word
        ("Weekly JIAK 99 meeting", False),
        ("site recee", False),
        ("[Site visit] Bluebird Travel Agency", False),
        ("Visit for 22 July 27 event", False),
        ("Canceled: Weekly JIAK 99 meeting", False),
    ],
)
def test_booking_like(subject: str, booking_like: bool) -> None:
    assert read_old_entry(subject, "Number of Pax: 20 pax", []).booking_like is booking_like


def test_squeeze() -> None:
    assert squeeze("Tour Group!") == "tourgroup"
    assert squeeze("Sunshine-TourGroup 2") == "sunshinetourgroup2"


def test_parse_client_map() -> None:
    text = """
# comment
[[client]]
name = "Sunshine TourGroup"
words = ["Sunshine", "Tour Group"]
ticket = "TINGKAT"
"""
    assert parse_client_map(text) == [SUNSHINE]
    assert parse_client_map("") == []


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[[client", "not valid TOML"),
        ('[[client]]\nwords = ["a"]\nticket = "TINGKAT"', "client 1: needs a name"),
        ('[[client]]\nname = " "\nwords = ["a"]\nticket = "TINGKAT"', "client 1: needs a name"),
        ('[[client]]\nname = "A"\nticket = "TINGKAT"', "`words` must be a list"),
        ('[[client]]\nname = "A"\nwords = []\nticket = "TINGKAT"', "`words` must be a list"),
        ('[[client]]\nname = "A"\nwords = [1]\nticket = "TINGKAT"', "`words` must be a list"),
        ('[[client]]\nname = "A"\nwords = ["a"]\nticket = "PHE"', "ticket must be one of"),
        ('[[client]]\nname = "A"\nwords = ["--"]\nticket = "CLASSIC"', "has no letters"),
    ],
)
def test_client_map_errors(text: str, message: str) -> None:
    with pytest.raises(ClientMapError, match=message):
        parse_client_map(text)
