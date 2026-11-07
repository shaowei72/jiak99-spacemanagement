"""Run every scenario in a scenarios file and compare with the expected answers.

    uv run scenarios                 # summary: one line per scenario
    uv run scenarios --show S11      # full output for one scenario
    uv run scenarios --verbose       # full output for all
    uv run scenarios --file my-scenarios.json

Exit code 1 if any scenario doesn't match, so it can run in tests.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine import Enquiry, EnquiryResult, Settings, check_enquiry
from engine.text import format_time
from jiak_cli.check import format_result, load_days, parse_ticket_type, parse_time

DEFAULT_FILE = Path(__file__).resolve().parents[2] / "examples" / "scenarios.json"


@dataclass(frozen=True)
class Outcome:
    scenario_id: str
    title: str
    enquiry: Enquiry
    result: EnquiryResult
    problems: list[str]  # empty if everything matched


def run_scenario(scenario: dict[str, Any], days: dict[str, Any], today: str) -> Outcome:
    asked = scenario["enquiry"]
    enquiry = Enquiry(
        date=asked["date"],
        start=parse_time(asked["start"]),
        ticket_type=parse_ticket_type(asked["type"]),
        adults=int(asked.get("adults", 0)),
        children=int(asked.get("children", 0)),
    )
    options = scenario.get("settings", {})
    settings = Settings(
        turnaround_minutes=int(options.get("turnaround", 0)),
        small_group_warn_below=int(options.get("warn_below", 0)),
    )
    result = check_enquiry(days, settings, enquiry, today=today)
    return Outcome(
        scenario["id"], scenario["title"], enquiry, result, compare(result, scenario["expect"])
    )


def compare(result: EnquiryResult, expect: dict[str, Any]) -> list[str]:
    """Differences between the result and what the scenario expects."""
    problems: list[str] = []
    if result.verdict != expect["verdict"]:
        problems.append(f"verdict {result.verdict}, expected {expect['verdict']}")
    if "placement" in expect:
        kind = result.placement.kind if result.placement else "none"
        if kind != expect["placement"]:
            problems.append(f"placement {kind}, expected {expect['placement']}")
    if "reason_contains" in expect and expect["reason_contains"] not in result.reason:
        problems.append(f"reason doesn't mention {expect['reason_contains']!r}")
    offered = [f"{a.date} {format_time(a.start)} {a.ticket_type}" for a in result.alternatives]
    for option in expect.get("options_include", []):
        if option not in offered:
            problems.append(f"options don't include {option}")
    for option in expect.get("options_exclude", []):
        if option in offered:
            problems.append(f"options wrongly include {option}")
    if "first_alternative" in expect:
        if result.alternatives:
            first = result.alternatives[0]
            actual = f"{first.date} {format_time(first.start)} {first.ticket_type}"
        else:
            actual = "none"
        if actual != expect["first_alternative"]:
            problems.append(f"first option {actual}, expected {expect['first_alternative']}")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scenarios", description="Run test scenarios.")
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE, help="scenarios JSON file")
    parser.add_argument("--show", metavar="ID", help="print full output for one scenario")
    parser.add_argument("--verbose", action="store_true", help="print full output for all")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    spec: dict[str, Any] = json.loads(args.file.read_text(encoding="utf-8"))
    days = load_days(args.file.parent / spec["days"])
    scenarios = spec["scenarios"]
    if args.show:
        scenarios = [s for s in scenarios if s["id"] == args.show]

    outcomes = [run_scenario(s, days, spec["today"]) for s in scenarios]
    for outcome in outcomes:
        mark = "ok  " if not outcome.problems else "FAIL"
        print(f"{mark} {outcome.scenario_id}  {outcome.result.verdict:<13}  {outcome.title}")
        for problem in outcome.problems:
            print(f"           - {problem}")
        if args.verbose or args.show:
            print(
                "\n".join(
                    "           " + line
                    for line in format_result(outcome.enquiry, outcome.result).splitlines()
                )
            )
            print()

    failed = sum(1 for o in outcomes if o.problems)
    print(f"\n{len(outcomes) - failed} of {len(outcomes)} scenarios match.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
