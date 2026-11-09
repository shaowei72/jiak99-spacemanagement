import argparse
from pathlib import Path

import pytest

from jiak_cli.check import ZONE_COLUMN_WIDTH, main, parse_ticket_type

EXAMPLE = Path(__file__).parent.parent / "examples" / "busy-day.json"


def test_cli_prints_verdict_reason_and_options(capsys: pytest.CaptureFixture[str]) -> None:
    main(["2026-12-01", "20:30", "classic", "8", "--today", "2026-12-01", "--empty-day"])
    out = capsys.readouterr().out
    assert "Verdict: NOT_POSSIBLE" in out
    assert "A 1 h CHE from 20:30 would end after the 21:00 close." in out
    assert "1. ACCEPT_GOOD   2026-12-01 20:00 · CHE  Start 20:00 instead of 20:30" in out


def test_cli_sales_board(capsys: pytest.CaptureFixture[str]) -> None:
    main(
        [
            "2026-12-01",
            "11:00",
            "PREMIUM",
            "12",
            "--day",
            str(EXAMPLE),
            "--today",
            "2026-12-01",
            "--board",
            "sales",
        ]
    )
    out = capsys.readouterr().out
    assert "Reason:  Fits (route " in out
    assert "Sales board for 12 people" in out
    assert "Operations board" not in out
    assert "Tingkat     CHE     CHE+CT      PHE     PHE+CT" in out
    # 09:30: B, C and D are all in use in busy-day.json, and so is A.
    assert "  09:30     x         x         x         x         x" in out
    assert "  11:00     +         +         +         +         +" in out


def board_rows(out: str) -> dict[str, list[str]]:
    """Operations board rows: '09:00' -> the 4 zone cells (A-D)."""
    rows: dict[str, list[str]] = {}
    for line in out.splitlines():
        if line.startswith("  ") and line[2:7].count(":") == 1 and line[7:8] == " ":
            w = ZONE_COLUMN_WIDTH
            cells = [line[8 + w * i : 8 + w * (i + 1)].strip() for i in range(4)]
            rows[line[2:7]] = cells
    return rows


def test_cli_operations_board_is_the_default(capsys: pytest.CaptureFixture[str]) -> None:
    main(["2026-12-01", "11:30", "PREMIUM", "10", "--today", "2026-12-01"])
    out = capsys.readouterr().out
    assert "Operations board for 2026-12-01" in out
    assert "Sales board" not in out
    assert "        A           B           C           D" in out
    rows = board_rows(out)
    assert len(rows) == 24  # 09:00 to 20:30
    # Group 3 is pinned to route ABDC in busy-day.json: A 30 min, B 1 h, D 30 min, C 1 h.
    assert rows["09:00"][0] == "3 PHE+CT"
    assert [rows[t][1] for t in ("09:30", "10:00")] == ["3 PHE+CT"] * 2
    assert rows["10:30"][3] == "3 PHE+CT"
    assert [rows[t][2] for t in ("11:00", "11:30")] == ["3 PHE+CT"] * 2
    assert "3 = 09:00 · PHE+CT, 12 people, route ABDC, pinned" in out
    # The request is on the board: one slot in each zone from 11:30 to 13:30.
    request_cells = [cell for t in ("11:30", "12:00", "12:30", "13:00") for cell in rows[t]]
    assert request_cells.count("* PHE") == 4
    assert "* = 11:30 · PHE, 10 people, route " in out


def test_cli_operations_board_without_a_fit(capsys: pytest.CaptureFixture[str]) -> None:
    main(["2026-12-01", "10:00", "TINGKAT", "4", "--today", "2026-12-01"])
    out = capsys.readouterr().out
    assert "Zones B, C and D are in use at 10:00." in out
    assert "*" not in "".join(cell for row in board_rows(out).values() for cell in row)


def test_cli_operations_board_split_group_and_turnaround(
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(
        [
            "2026-12-01",
            "10:00",
            "PREMIUM",
            "40",
            "--empty-day",
            "--turnaround",
            "15",
            "--today",
            "2026-12-01",
        ]
    )
    out = capsys.readouterr().out
    rows = board_rows(out)
    # Two sub-groups of 20, each in a different zone at 10:00, each zone reset after its visit.
    assert rows["10:00"].count("* PHE") == 2
    assert rows["10:30"].count("(reset)") == 2
    assert "is split into 2 groups (20 + 20)" in out
    assert "(reset) = turnaround" in out


def test_cli_operations_board_blackouts_and_closed_days(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    day_file = tmp_path / "days.json"
    day_file.write_text(
        '{"2026-12-01": {"blackouts": [{"start": "14:00", "end": "17:00"}], "sessions": []},'
        ' "2026-12-02": {"closed": true}}',
        encoding="utf-8",
    )
    main(["2026-12-01", "10:00", "CLASSIC", "4", "--day", str(day_file), "--today", "2026-12-01"])
    rows = board_rows(capsys.readouterr().out)
    assert rows["14:00"] == ["blackout"] * 4
    assert rows["17:00"] == ["."] * 4

    main(["2026-12-02", "10:00", "CLASSIC", "4", "--day", str(day_file), "--today", "2026-12-01"])
    assert "Closed all day." in capsys.readouterr().out


def test_cli_operations_board_with_an_overfull_day_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Three Classic groups at 10:00 all need zone A within one hour: one too many.
    three_groups = ", ".join(['{"start": "10:00", "type": "CLASSIC", "adults": 2}'] * 3)
    day_file = tmp_path / "days.json"
    day_file.write_text(f'{{"2026-12-01": [{three_groups}]}}', encoding="utf-8")
    main(["2026-12-01", "15:00", "CLASSIC", "4", "--day", str(day_file), "--today", "2026-12-01"])
    assert "The groups in the day file don't all fit." in capsys.readouterr().out


def test_cli_no_board_hides_the_board(capsys: pytest.CaptureFixture[str]) -> None:
    main(["2026-12-01", "10:00", "premium", "10", "--today", "2026-12-01", "--no-board"])
    assert "board" not in capsys.readouterr().out.lower()


def test_cli_rejects_an_unknown_board() -> None:
    with pytest.raises(SystemExit):
        main(["2026-12-01", "10:00", "premium", "10", "--board", "kitchen"])


def test_cli_refuses_a_day_file_together_with_empty_day() -> None:
    with pytest.raises(SystemExit):
        main(["2026-12-01", "10:00", "premium", "10", "--day", str(EXAMPLE), "--empty-day"])


def test_cli_rejects_unknown_ticket_type() -> None:
    with pytest.raises(SystemExit):
        main(["2026-12-01", "10:00", "T60", "4"])


@pytest.mark.parametrize(
    "text", ["CLASSIC_CT", "classic_ct", "classic-ct", "Classic CT", "CHE+CT", "che+ct", "CHE + CT"]
)
def test_cli_accepts_ticket_type_spellings(text: str, capsys: pytest.CaptureFixture[str]) -> None:
    main(["2026-12-01", "10:30", text, "4", "--today", "2026-12-01"])
    assert "10:30 · CHE+CT" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("text", "code"),
    [("che", "CLASSIC"), ("PHE", "PREMIUM"), ("phe+ct", "PREMIUM_CT"), ("tingkat", "TINGKAT")],
)
def test_short_labels_are_read_as_codes(text: str, code: str) -> None:
    assert parse_ticket_type(text) == code


def test_unknown_ticket_type_lists_the_labels() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match=r"Tingkat, CHE, CHE\+CT, PHE, PHE\+CT"):
        parse_ticket_type("XHE")
