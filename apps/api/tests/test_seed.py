"""The seed command: sample days go into the database and answer exactly like the JSON files."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from engine import Enquiry
from jiak_api import bookings, models
from jiak_api.clock import Now
from jiak_api.db import Database
from jiak_api.seed import EXAMPLES, main, resolve_day_file
from jiak_cli.check import parse_ticket_type, parse_time
from jiak_cli.scenarios import compare


def seed(database_url: str, *args: str) -> int:
    return main([*args, "--database-url", database_url, "--yes"])  # --yes: tests may use PostgreSQL


def test_busy_day_answers_like_the_check_cli(
    database_url: str, database: Database, client: TestClient
) -> None:
    assert seed(database_url) == 0  # busy-day.json is the default
    # Same enquiry as `uv run check 2026-12-01 09:00 PREMIUM 10`: zones A and B are full.
    params = {"date": "2026-12-01", "start": "09:00", "type": "PREMIUM", "adults": 10}
    body = client.get("/api/enquiries/check", params=params).json()
    assert body["verdict"] == "NOT_POSSIBLE"
    assert body["reason"] == "Zones A and B are in use 09:00–11:00."

    day = client.get("/api/days/2026-12-01").json()
    assert len(day["bookings"]) == 7
    pinned = [s for b in day["bookings"] for s in b["sessions"] if s["pinned"]]
    assert [(s["route"], s["type"]) for s in pinned] == [("ABDC", "PREMIUM_CT")]
    assert day["bookings"][0]["customer"]["name"].startswith("Sample group")


def test_every_scenario_gives_the_same_answer_from_the_database(
    database_url: str, database: Database
) -> None:
    """Seed the scenario week, then run all scenarios against the database instead of the file."""
    spec: dict[str, Any] = json.loads((EXAMPLES / "scenarios.json").read_text(encoding="utf-8"))
    assert seed(database_url, spec["days"].removesuffix(".json")) == 0
    now = Now(today=spec["today"], minute=0)

    problems: dict[str, list[str]] = {}
    for scenario in spec["scenarios"]:
        asked = scenario["enquiry"]
        enquiry = Enquiry(
            date=asked["date"],
            start=parse_time(asked["start"]),
            ticket_type=parse_ticket_type(asked["type"]),
            adults=int(asked.get("adults", 0)),
            children=int(asked.get("children", 0)),
        )
        options = scenario.get("settings", {})
        with database.make_session() as db:
            db.merge(
                models.AppSettings(
                    id=1,
                    turnaround_minutes=int(options.get("turnaround", 0)),
                    small_group_warn_below=int(options.get("warn_below", 0)),
                )
            )
            db.commit()
            result = bookings.check(db, enquiry, now)
        found = compare(result, scenario["expect"])
        if found:
            problems[scenario["id"]] = found
    assert problems == {}
    assert len(spec["scenarios"]) == 31


def test_week_keeps_closed_days_and_blackouts(database_url: str, database: Database) -> None:
    assert seed(database_url, "week") == 0
    with database.make_session() as db:
        day_rows = {row.date: row for row in db.scalars(select(models.DaySettings))}
    assert day_rows["2026-12-08"].closed is True
    assert day_rows["2026-12-07"].blackouts == [{"start": 14 * 60, "end": 17 * 60}]


def test_seeding_twice_needs_replace(
    database_url: str, database: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    assert seed(database_url) == 0
    assert seed(database_url) == 1
    assert "--replace" in capsys.readouterr().err

    assert seed(database_url, "--replace") == 0
    with database.make_session() as db:
        assert len(list(db.scalars(select(models.Booking)))) == 7
        assert len(list(db.scalars(select(models.Customer)))) == 7  # old ones removed
        actions = list(db.scalars(select(models.AuditLog.action)))
    assert actions == ["day.seed", "day.seed"]


def test_replace_also_wipes_real_bookings_on_those_dates(
    database_url: str, database: Database, client: TestClient
) -> None:
    booking = {
        "date": "2026-12-01",
        "start": "18:00",
        "type": "CLASSIC",
        "adults": 4,
        "customer": {"name": "Tan family"},
    }
    assert client.post("/api/bookings", json=booking).status_code == 201
    assert seed(database_url) == 1  # refuses: 2026-12-01 has a booking
    assert seed(database_url, "--replace") == 0
    names = [b["customer"]["name"] for b in client.get("/api/days/2026-12-01").json()["bookings"]]
    assert "Tan family" not in names


def test_refuses_a_non_sqlite_database_without_yes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--database-url", "postgresql+psycopg://jiak:secret@db.example.com/jiak"]) == 1
    err = capsys.readouterr().err
    assert "--yes" in err
    assert "secret" not in err  # never print the password


def test_unknown_day_file(database_url: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert seed(database_url, "nonsense") == 1
    err = capsys.readouterr().err
    assert "busy-day" in err
    assert "week" in err


def test_a_day_file_path(database_url: str, tmp_path: Path, database: Database) -> None:
    day_file = tmp_path / "mine.json"
    day_file.write_text(
        '{"2026-12-10": [{"start": "10:00", "type": "PHE", "adults": 30, "children": 5}]}',
        encoding="utf-8",
    )
    assert resolve_day_file(str(day_file)) == day_file
    assert seed(database_url, str(day_file)) == 0
    with database.make_session() as db:
        sessions = list(db.scalars(select(models.Session)))
    assert sorted((s.adults, s.children) for s in sessions) == [(12, 5), (18, 0)]


def test_a_pinned_group_over_25_is_refused(
    database_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    day_file = tmp_path / "bad.json"
    day_file.write_text(
        '{"2026-12-10": [{"start": "10:00", "type": "PHE", "adults": 30, "route": "ABCD", "pinned": true}]}',
        encoding="utf-8",
    )
    assert seed(database_url, str(day_file)) == 1
    assert "over 25" in capsys.readouterr().err


def test_a_bad_session_in_the_day_file_is_refused(
    database_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    day_file = tmp_path / "bad.json"
    day_file.write_text('{"2026-12-10": [{"start": "10:00", "type": "PHE"}]}', encoding="utf-8")
    assert seed(database_url, str(day_file)) == 1
    assert "2026-12-10 session 1: give it 'adults' or 'children'." in capsys.readouterr().err
