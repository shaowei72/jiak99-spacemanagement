"""`uv run import-outlook` (dry run) and `uv run anonymise-ics` (SPEC §5.4 take-over)."""

import csv
from pathlib import Path

import pytest

from graph.ics import parse_ics
from jiak_api import anonymise_ics, import_outlook
from jiak_api.clock import Now

CALENDAR = (
    "BEGIN:VCALENDAR\r\n"
    "BEGIN:VEVENT\r\nUID:1\r\nSUMMARY:[Confirmed] Bluebird Primary PHE- 20 pax\r\n"
    "DTSTART;TZID=Singapore Standard Time:20261020T100000\r\n"
    "DTEND;TZID=Singapore Standard Time:20261020T120000\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:2\r\nSUMMARY:[Soft Block] Sunshine TourGroup 30 pax\r\n"
    "DESCRIPTION:Contact: 9123 4567\r\n"
    "DTSTART;TZID=Singapore Standard Time:20261120T110000\r\n"
    "DTEND;TZID=Singapore Standard Time:20261120T113000\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:3\r\nSUMMARY:[Soft Block] Kingfisher Travel\r\n"
    "DTSTART;TZID=Singapore Standard Time:20261121T140000\r\n"
    "DTEND;TZID=Singapore Standard Time:20261121T160000\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:4\r\nSUMMARY:Weekly meeting\r\n"
    "DTSTART;TZID=Singapore Standard Time:20261111T133000\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


@pytest.fixture
def ics_file(tmp_path: Path) -> Path:
    path = tmp_path / "operations.ics"
    path.write_text(CALENDAR, encoding="utf-8")
    return path


def read_sheet(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def test_dry_run_writes_the_review_sheet_next_to_the_file(
    ics_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert import_outlook.main(["--from-ics", str(ics_file), "--today", "2026-11-10"]) == 0
    sheet = ics_file.with_name("operations-review.csv")
    rows = read_sheet(sheet)
    assert [(r["date"], r["action"], r["ticket"]) for r in rows] == [
        ("2026-10-20", "history", "PREMIUM"),
        ("2026-11-20", "book", "TINGKAT"),  # from the client map in apps/api/client-tickets.toml
        ("2026-11-21", "fill in", ""),
    ]
    out = capsys.readouterr().out
    assert "Read 4 events from operations.ics: 3 look like bookings, 1 others" in out
    assert "  book               1" in out
    assert "2026-11-21   14:00  fill in        Kingfisher Travel" in out
    assert "no headcount found; no ticket type found" in out
    assert "Nothing was stored." in out


def test_existing_sheet_is_kept_unless_overwrite(
    ics_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sheet = tmp_path / "mine.csv"
    sheet.write_text("my corrections", encoding="utf-8")
    args = ["--from-ics", str(ics_file), "--out", str(sheet), "--today", "2026-11-10"]
    assert import_outlook.main(args) == 2
    assert "already exists" in capsys.readouterr().err
    assert sheet.read_text(encoding="utf-8") == "my corrections"

    assert import_outlook.main([*args, "--overwrite"]) == 0
    assert len(read_sheet(sheet)) == 3


def test_nothing_to_look_at(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "past.ics"
    path.write_text(CALENDAR, encoding="utf-8")
    assert import_outlook.main(["--from-ics", str(path), "--today", "2027-01-01"]) == 0
    assert "Future events to look at" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ([], "Say where to read from"),
        (["--from-graph"], "--from-graph is not built yet"),
        (["--from-ics", "x.ics", "--commit"], "--commit goes with --review"),
        (["--review", "missing.csv"], "Can't read missing.csv"),
        (["--from-ics", "x.ics", "--today", "12/11/2026"], "--today must be a date"),
        (["--from-ics", "missing.ics", "--today", "2026-11-10"], "Can't read missing.ics"),
        (["--from-ics", "x.ics", "--clients", "missing.toml"], "Can't read the client map"),
        (["--review", "x.csv", "--carry-over", "old.csv"], "--carry-over goes with --from-ics"),
    ],
)
def test_errors(args: list[str], message: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert import_outlook.main(args) == 2
    assert message in capsys.readouterr().err


def write_sheet(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_carry_over_copies_corrections_into_a_fresh_sheet(
    ics_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    early = tmp_path / "early.csv"
    args = ["--from-ics", str(ics_file), "--today", "2026-11-10"]
    assert import_outlook.main([*args, "--out", str(early)]) == 0
    rows = read_sheet(early)
    rows[2]["people"] = "12"  # Kingfisher Travel: filled in by the owner
    rows[2]["ticket"] = "CLASSIC"
    rows[1]["people"] = "25"  # Sunshine TourGroup: Outlook says 30
    rows.append({**rows[0], "event_id": "99", "client": "Deleted since"})
    write_sheet(early, rows)
    capsys.readouterr()

    fresh = tmp_path / "fresh.csv"
    assert import_outlook.main([*args, "--out", str(fresh), "--carry-over", str(early)]) == 0
    rows = read_sheet(fresh)
    assert [(r["people"], r["ticket"], r["action"]) for r in rows] == [
        ("20", "PREMIUM", "history"),
        ("25", "TINGKAT", "check by hand"),
        ("12", "CLASSIC", "book"),
    ]
    assert rows[1]["notes"].startswith(
        "kept from the earlier sheet: people 25 (Outlook now says 30)"
    )
    out = capsys.readouterr().out
    assert "From the earlier sheet early.csv:" in out
    assert "  corrections carried over   2" in out
    assert "  disagree with Outlook now  1" in out
    assert "  new since then             0" in out
    assert "  no longer in the calendar  1\n    2026-10-20 10:00 Deleted since" in out


def test_carry_over_problems(
    ics_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sheet = tmp_path / "sheet.csv"
    old_text = "date,client\n2026-12-01,Tan\n"
    sheet.write_text(old_text, encoding="utf-8")
    args = ["--from-ics", str(ics_file), "--today", "2026-11-10", "--carry-over", str(sheet)]
    assert import_outlook.main([*args, "--out", str(tmp_path / "fresh.csv")]) == 2
    assert "no event_id column" in capsys.readouterr().err
    assert import_outlook.main([*args, "--out", str(sheet), "--overwrite"]) == 2
    assert "is where the fresh sheet would go" in capsys.readouterr().err
    assert sheet.read_text(encoding="utf-8") == old_text


def test_bad_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    not_ics = tmp_path / "notes.ics"
    not_ics.write_text("hello", encoding="utf-8")
    assert import_outlook.main(["--from-ics", str(not_ics), "--today", "2026-11-10"]) == 2
    assert "not a calendar file" in capsys.readouterr().err

    bad_map = tmp_path / "clients.toml"
    bad_map.write_text('[[client]]\nname = "A"\nwords = ["a"]\nticket = "X"', encoding="utf-8")
    assert import_outlook.main(["--from-ics", str(not_ics), "--clients", str(bad_map)]) == 2
    assert "ticket must be one of" in capsys.readouterr().err


def test_default_take_over_date_is_today(
    ics_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(import_outlook, "singapore_now", lambda: Now(today="2026-11-21", minute=0))
    assert import_outlook.main(["--from-ics", str(ics_file)]) == 0
    assert "Take-over date 2026-11-21" in capsys.readouterr().out


def test_the_client_map_in_the_repo_is_valid() -> None:
    rules = import_outlook.load_client_map(import_outlook.DEFAULT_CLIENT_MAP)
    assert [rule.ticket_type for rule in rules if rule.name == "Sunshine TourGroup"] == ["TINGKAT"]


def test_anonymise_ics(ics_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out_file = tmp_path / "fixtures" / "anon.ics"
    assert anonymise_ics.main([str(ics_file), str(out_file)]) == 0
    events = parse_ics(out_file.read_text(encoding="utf-8"))
    assert [event.subject for event in events] == [
        "[Confirmed] Bluebird Primary PHE- 20 pax",  # by chance the same as the made-up name
        "Other event",
        "[Soft Block] Kingfisher Primary 30 pax",
        "[Soft Block] Hornbill Primary",
    ]
    assert all(event.body == "" for event in events)  # the phone number is gone
    out = capsys.readouterr().out
    assert "4 events written" in out
    assert "Kingfisher Travel  ->  Hornbill Primary" in out
    assert "No warnings." in out


def test_anonymise_ics_warnings_and_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    real = tmp_path / "real.ics"
    real.write_text(
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:1\r\nSUMMARY:[Confirmed] 2 adults PHE\r\n"
        "DTSTART:20261020T100000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n",
        encoding="utf-8",
    )
    assert anonymise_ics.main([str(real), str(tmp_path / "anon.ics")]) == 0
    assert "1 warnings, look at these" in capsys.readouterr().out

    assert anonymise_ics.main([str(real), str(real)]) == 2
    assert "must not be the real file" in capsys.readouterr().err
    assert anonymise_ics.main([str(tmp_path / "none.ics"), str(tmp_path / "a.ics")]) == 2
    assert "Can't read" in capsys.readouterr().err
    real.write_text("hello", encoding="utf-8")
    assert anonymise_ics.main([str(real), str(tmp_path / "a.ics")]) == 2
    assert "not a calendar file" in capsys.readouterr().err
