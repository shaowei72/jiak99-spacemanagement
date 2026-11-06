# CLAUDE.md

Zone capacity app for an F&B attraction: 4 identical zones × 25 people, sessions of 1.5/2/3 h, Outlook (Microsoft 365)
as the user layer. **Read `SPEC.md` before any work — it is the contract.**

## How to work here
- Build in the milestone order in `SPEC.md` §6. Finish one milestone (tests green) before starting the next.
- Start with `packages/engine`: pure functions, no I/O, no `Date` objects (minutes since midnight + `YYYY-MM-DD`).
- The acceptance tests in `SPEC.md` §7 are mandatory. Write them first, then make them pass.
- Business rules in `SPEC.md` §2 are fixed; settings marked "default" must stay configurable.
- No hold state. An enquiry is either booked or nothing is stored.
- Never call the real Microsoft Graph unless `GRAPH_MODE=real`. Use the fake gateway for dev and tests.
- Do not change the tenant, mailboxes or permissions yourself; list what the admin must do in `README.md`.
- Record any deviation from `SPEC.md` (stack, rule interpretation) in `DECISIONS.md` with a one-line reason.
- If a rule is ambiguous, pick the option that keeps zones flexible and sales options widest, note it in
  `DECISIONS.md`, and carry on.

## Commands (create these in Milestone 1–2)
- `pnpm install`
- `pnpm test` — all packages
- `pnpm --filter engine test`
- `pnpm dev` — api + web via Docker Compose
- `pnpm import:outlook --dry-run` — Milestone 4
