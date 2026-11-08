import argparse
from pathlib import Path

import pytest

from jiak_cli.check import ZONE_COLUMN_WIDTH, main, parse_ticket_type

EXAMPLE = Path(__file__).parent.parent / "examples" / "busy-day.json"


def test_cli_prints_verdict_reason_and_options(capsys: pytest.CaptureFixture[str]) -> None:
    main(["2026-12-01", "10:00", "premium", "10", "--today", "2026-12-01", "--empty-day"])
    out = capsys.readouterr().out
    assert "Verdict: ACCEPT_COSTLY" in out
    assert "leaves 09:00–10:00 in one zone unsellable" in out
    assert "1. ACCEPT_GOOD   2026-12-01 10:30 · PHE  Start 10:30 instead of 10:00" in out


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
    assert "Reason:  Fits in a free zone with no unsellable gaps." in out
    assert "Sales board for 12 people" in out
    assert "Operations board" not in out
    assert "  09:00    x        x        x        x" in out  # all 4 zones taken
    assert "  11:00    +        +        +        +" in out


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
    assert rows["09:00"][0] == "PHE+CT 12 p"  # pinned to A in busy-day.json
    assert rows["11:30"][0] == "PHE+CT"  # 3 hours: 09:00-12:00
    assert rows["12:00"][0] == "."
    # The request is on the board, with its headcount on its first row.
    assert "*PHE 10*" in rows["11:30"]
    zone = rows["11:30"].index("*PHE 10*")
    assert [rows[t][zone] for t in ("12:00", "12:30", "13:00")] == ["*PHE*"] * 3
    # The 30 minutes the reason talks about show up as a dead gap.
    assert "leaves 11:00–11:30 in one zone unsellable" in out
    assert "-dead-" in rows["11:00"]


def test_cli_operations_board_without_a_fit(capsys: pytest.CaptureFixture[str]) -> None:
    main(["2026-12-01", "10:00", "PREMIUM", "10", "--today", "2026-12-01"])
    out = capsys.readouterr().out
    assert "All 4 zones are in use" in out  # busy-day.json fills every zone at 10:00
    assert "*" not in "".join(cell for row in board_rows(out).values() for cell in row)


def test_cli_operations_board_big_group_and_turnaround(
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
    assert sorted(rows["10:00"][:2]) == ["*PHE 15*", "*PHE 25*"]
    assert rows["12:00"][:2] == ["(reset)", "(reset)"]
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
    five_groups = ", ".join(['{"start": "10:00", "type": "CLASSIC", "adults": 2}'] * 5)
    day_file = tmp_path / "days.json"
    day_file.write_text(f'{{"2026-12-01": [{five_groups}]}}', encoding="utf-8")
    main(["2026-12-01", "15:00", "CLASSIC", "4", "--day", str(day_file), "--today", "2026-12-01"])
    assert "don't fit in 4 zones" in capsys.readouterr().out


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
    ("text", "code"), [("che", "CLASSIC"), ("PHE", "PREMIUM"), ("phe+ct", "PREMIUM_CT")]
)
def test_short_labels_are_read_as_codes(text: str, code: str) -> None:
    assert parse_ticket_type(text) == code


def test_unknown_ticket_type_lists_the_labels() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match=r"CHE, CHE\+CT, PHE, PHE\+CT"):
        parse_ticket_type("XHE")
