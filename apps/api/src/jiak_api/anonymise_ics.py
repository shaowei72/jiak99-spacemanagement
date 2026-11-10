"""Make an anonymised copy of the real calendar file, for tests (SPEC §5.4 "Test data").

    uv run anonymise-ics REAL.ics packages/graph/tests/fixtures/operations-anon.ics

Prints which client names were replaced (real -> made-up) and any warnings. Read the output file
and the warnings BEFORE committing it: the GitHub repo is public. Never commit the real file or the
printed name list.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from graph.anonymise import anonymise
from graph.ics import IcsError, parse_ics, write_ics
from jiak_api.import_outlook import DEFAULT_CLIENT_MAP, TakeOverError, load_client_map


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="anonymise-ics", description="Make an anonymised copy of a calendar file for tests."
    )
    parser.add_argument("real_file", help="the .ics file saved from the calendar")
    parser.add_argument("out_file", help="where to write the anonymised copy")
    parser.add_argument("--clients", default=str(DEFAULT_CLIENT_MAP), help="client map file")
    args = parser.parse_args(argv)

    real_path = Path(args.real_file)
    out_path = Path(args.out_file)
    try:
        if out_path.resolve() == real_path.resolve():
            raise TakeOverError("The output file must not be the real file.")
        rules = load_client_map(Path(args.clients))
        try:
            events = parse_ics(real_path.read_text(encoding="utf-8"))
        except OSError as error:
            raise TakeOverError(f"Can't read {real_path}: {error.strerror}") from error
        except IcsError as error:
            raise TakeOverError(f"{real_path}: {error}") from error
    except TakeOverError as error:
        print(f"anonymise-ics: {error}", file=sys.stderr)
        return 2

    result = anonymise(events, rules)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(write_ics(list(result.events)), encoding="utf-8", newline="")

    print(f"{len(result.events)} events written to {out_path}")
    print(f"{len(result.names)} client names replaced (for your check only, don't commit this):")
    for real, fake in result.names:
        print(f"  {real}  ->  {fake}")
    if result.warnings:
        print(f"{len(result.warnings)} warnings, look at these before committing:")
        for warning in result.warnings:
            print(f"  {warning}")
    else:
        print("No warnings. Still read the file before committing it.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
