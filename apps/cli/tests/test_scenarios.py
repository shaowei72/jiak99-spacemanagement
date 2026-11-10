"""The scenario week (apps/cli/examples) must keep matching its expected answers."""

import json
from pathlib import Path

import pytest

from jiak_cli.check import load_days
from jiak_cli.scenarios import DEFAULT_FILE, main


def test_all_scenarios_match(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([])
    out = capsys.readouterr().out
    assert exit_code == 0, out


def test_scenario_week_loads_closed_days_and_blackouts() -> None:
    days = load_days(DEFAULT_FILE.parent / "week.json")
    assert "_about" not in days
    assert days["2026-12-08"].closed
    assert len(days["2026-12-07"].blackouts) == 1
    assert len(days["2026-12-05"].sessions) == 24


def test_runner_reports_a_wrong_expectation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = json.loads(DEFAULT_FILE.read_text(encoding="utf-8"))
    spec["days"] = str(DEFAULT_FILE.parent / "week.json")
    spec["scenarios"] = [dict(spec["scenarios"][0], expect={"verdict": "NOT_POSSIBLE"})]
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps(spec), encoding="utf-8")

    assert main(["--file", str(wrong), "--verbose"]) == 1
    assert "verdict ACCEPT_GOOD, expected NOT_POSSIBLE" in capsys.readouterr().out


def test_runner_reports_a_bad_day_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    day_file = tmp_path / "days.json"
    day_file.write_text('{"2026-12-01": [{"start": "10:00", "type": "PHE"}]}', encoding="utf-8")
    spec_file = tmp_path / "scenarios.json"
    spec_file.write_text(
        json.dumps({"days": str(day_file), "today": "2026-12-01", "scenarios": []}),
        encoding="utf-8",
    )
    assert main(["--file", str(spec_file)]) == 1
    assert "give it 'adults' or 'children'" in capsys.readouterr().err
