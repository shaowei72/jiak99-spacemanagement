# CLAUDE.md

Zone capacity app for an F&B attraction: 4 identical zones × 25 people, 4 ticket types (Classic, Classic + Chef's
Table, Premium, Premium + Chef's Table; 1.5–3 h), Outlook (Microsoft 365) as the user layer. **Read `SPEC.md` before any work — it is the contract.**

## How to work here
- Build in the milestone order in `SPEC.md` §6. Finish one milestone (tests green) before starting the next.
- Stack is Python 3.13 + uv (see `SPEC.md` §5.1). Only the Outlook add-in shell is TypeScript.
- Start with `packages/engine`: pure functions, standard library only, no I/O, no `datetime`/`date` objects
  (`int` minutes since midnight + `YYYY-MM-DD` strings). Fully type-hinted; `mypy --strict` must pass.
- Write code the owner can read: they are learning Python. Prefer plain, explicit code over clever idioms, and
  give public functions short docstrings.
- The acceptance tests in `SPEC.md` §7 are mandatory. Write them first, then make them pass.
- Business rules in `SPEC.md` §2 are fixed; settings marked "default" must stay configurable.
- Ticket types are identified by their codes (`CLASSIC`, `CLASSIC_CT`, `PREMIUM`, `PREMIUM_CT`) everywhere in code
  and data. Product names are display-only (`TICKET_NAMES` / `TICKET_SHORT_NAMES` in `engine/rules.py`).
- No hold state. An enquiry is either booked or nothing is stored.
- No sharing. A zone taken by a group is sold out for that time slot, whatever the group size.
- Never call the real Microsoft Graph unless `GRAPH_MODE=real`. Use the fake gateway for dev and tests.
- Do not change the tenant, mailboxes or permissions yourself; list what the admin must do in `README.md`.
- Record any deviation from `SPEC.md` (stack, rule interpretation) in `DECISIONS.md` with a one-line reason.
- If a rule is ambiguous, pick the option that keeps zones flexible and sales options widest, note it in
  `DECISIONS.md`, and carry on.

## Commands (create these in Milestone 1–2)
- `uv sync` — install all workspace packages and dev tools
- `uv run pytest` — all packages
- `uv run pytest packages/engine` — engine only
- `uv run pytest --cov` — with coverage (engine must stay at 100%)
- `uv run check 2026-12-01 10:00 PREMIUM 10 [--day FILE | --empty-day] [--board operations|sales | --no-board]`
  — try an enquiry (defaults: `apps/cli/examples/busy-day.json` and the operations board, zones A–D)
- `uv run scenarios [--show S11]` — run the scenario week (`apps/cli/examples/SCENARIOS.md`)
- `uv run ruff check . && uv run ruff format --check . && uv run mypy` — lint, format, types
- `uv run seed [busy-day | week | FILE] [--replace]` — load an example day file into the database
- `docker compose up` — api (incl. web pages) + PostgreSQL
- `uv run import-outlook --dry-run` — Milestone 4 (console script defined in `apps/api`)
