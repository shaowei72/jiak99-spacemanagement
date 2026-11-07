# Jiak99 zone capacity app

Helps sales and operations sell and allocate 4 identical zones (up to 25 people each). Each group gets a zone of
its own: once a zone is taken, it is sold out for that time slot, however few people are in it.

| Code | Ticket | Length |
| --- | --- | --- |
| `CLASSIC` | Classic Hawker Experience | 1.5 h |
| `CLASSIC_CT` | Classic Hawker Experience + Chef's Table | 1.5 h |
| `PREMIUM` | Premium Hawker Experience | 2 h |
| `PREMIUM_CT` | Premium Hawker Experience + Chef's Table | 3 h |

`SPEC.md` is the contract, `DECISIONS.md` records deviations, `CLAUDE.md` has working rules.

## Status

MVP 0 (Milestone 1, the engine) is done: `packages/engine`, plus a command-line tool to try it.

## Setup

Install [uv](https://docs.astral.sh/uv/), then:

```
uv sync          # installs Python 3.13, the packages and dev tools
uv run pytest    # all tests
```

## Try an enquiry

```
uv run check 2026-12-01 10:00 PREMIUM 10
uv run check 2026-12-01 11:30 PREMIUM 10 --day apps/cli/examples/busy-day.json --board
uv run check 2026-12-01 10:30 classic-ct 6 --children 2
uv run check --help
```

Ticket types can be typed in any case, with `_`, `-` or a space (`classic-ct`, `Classic CT`).

Write a day file in the same format as `apps/cli/examples/week.json` to try real days.

## Scenario week

`uv run scenarios` checks 29 enquiries against an 8-day test week and compares each answer with the expected
one. How the scenarios were built and what each one tests: `apps/cli/examples/SCENARIOS.md`.

## What the Microsoft 365 admin will need to do

Nothing yet. Needed from Milestone 4 (see `SPEC.md` §5.4): an Entra app registration with `Calendars.ReadWrite`
application permission, restricted to the bookings and zone mailboxes.
