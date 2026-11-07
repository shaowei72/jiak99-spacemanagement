from pathlib import Path

import pytest

from jiak_cli.check import main

EXAMPLE = Path(__file__).parent.parent / "examples" / "busy-day.json"


def test_cli_prints_verdict_reason_and_options(capsys: pytest.CaptureFixture[str]) -> None:
    main(["2026-12-01", "10:00", "premium", "10", "--today", "2026-12-01"])
    out = capsys.readouterr().out
    assert "Verdict: ACCEPT_COSTLY" in out
    assert "leaves 09:00–10:00 in one zone unsellable" in out
    assert "1. ACCEPT_GOOD   2026-12-01 10:30 · Premium  Start 10:30 instead of 10:00" in out


def test_cli_reads_a_day_file_and_prints_the_board(capsys: pytest.CaptureFixture[str]) -> None:
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
        ]
    )
    out = capsys.readouterr().out
    assert "Reason:  Fits in a free zone with no unsellable gaps." in out
    assert "Board for 12 people" in out
    assert "  09:00     x          x          x          x" in out  # all 4 zones taken
    assert "  11:00     +          +          +          +" in out


def test_cli_rejects_unknown_ticket_type() -> None:
    with pytest.raises(SystemExit):
        main(["2026-12-01", "10:00", "T60", "4"])


@pytest.mark.parametrize("text", ["CLASSIC_CT", "classic_ct", "classic-ct", "Classic CT"])
def test_cli_accepts_ticket_type_spellings(text: str, capsys: pytest.CaptureFixture[str]) -> None:
    main(["2026-12-01", "10:30", text, "4", "--today", "2026-12-01"])
    assert "10:30 · Classic + Chef's Table" in capsys.readouterr().out
