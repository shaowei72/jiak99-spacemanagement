# Test scenarios

Files in this folder:

| File | What it is |
| --- | --- |
| `week.json` | Eight days of existing sessions (Tue 2026-12-01 to Tue 2026-12-08), each built for a purpose |
| `scenarios.json` | 29 enquiries against that week, each with the expected answer |
| `busy-day.json` | Single example day used by the CLI tests |

Run them all with `uv run scenarios` (or `uv run scenarios --show S11` for one in full). They also run as part of
`uv run pytest`, so a change that alters any answer is caught. "Today" is fixed at **Tue 2026-12-01**.

**Key rule (SPEC §2):** each group gets a zone of its own. Once a zone is taken, it is sold out for that time
slot, however few people are in it. Only unoccupied zones can be sold. A group over 25 takes several zones.
All 4 zones can run at the same time with any mix of ticket types. Alternatives never suggest a downgrade.

---

## 1. Method used to construct the scenarios

### Step 1: List the factors that change the engine's answer

| Factor | Values tested |
| --- | --- |
| Day load | quiet · lunch peak · fragmented · sold out |
| Zone planning | all floating · fully locked by ops (every session pinned) |
| Group size | small (below the warning size) · up to 25 · 26 (needs a 2nd zone) · 30 · 40 · 60 · 75 |
| Free zones vs zones needed | **exactly enough** · **one short** |
| Taken zones with spare seats | small groups in a zone, so spare seats exist but must not be sold |
| Start time | 09:00 (opening) · midday · near closing (19:30) · off the 30-min grid (10:15) |
| Ticket type | all four, including the two equally long Classic types · upgrade vs downgrade |
| Calendar | today · past · **last day of the 6-month window** · **one day beyond** · closed day · blackout |
| Settings | defaults · turnaround 15 min · small-group warning on |

### Step 2: Use boundary values, not random ones

Bugs cluster at edges, so the scenarios use them on purpose:

- a group that takes *exactly* the last free zone(s), next to one that needs *one zone more* than is free
  (S02/S27, S20/S21);
- spare seats in a taken zone that must not be sold (S01, S10, S17);
- day 183 vs day 184 of the booking window (S07/S08);
- 09:00–10:30, exactly 1.5 h, but too short once turnaround is added (S14).

### Step 3: Cover every possible outcome at least once

Each thing the engine can answer is hit by at least one scenario:

| Outcome | Scenarios |
| --- | --- |
| Verdict `ACCEPT_GOOD` | S01, S02, S07, S12, S13, S16, S25 |
| Verdict `ACCEPT_COSTLY` | S03, S04, S14, S20, S23, S28, S29 |
| Verdict `NOT_POSSIBLE` | S05, S06, S08, S09, S10, S11, S15, S17, S18, S19, S21, S22, S24, S26, S27 |
| Placement: one zone | S01, S12, S13, S16, S20, S25 |
| Placement: several zones (group over 25) | S02, S23 |
| Rule: spare seats in a taken zone are not sold | S01, S10, S17 |
| Reason: unsellable gap | S03, S14, S20, S23, S28 |
| Reason: small group | S04 |
| Reason: all zones in use | S10, S11, S17, S18 |
| Reason: not enough free zones for a big group | S21, S22, S27 |
| Reason: blocked by pinned sessions | S15 |
| Reason: ends after close / off grid / past / beyond window | S05 / S06 / S09 / S08 |
| Reason: closed day / blackout | S26 / S24 |
| Alternative: shift start time | S03, S05, S14, S20, S23, S24, S29 |
| Alternative: upgrade ticket type | S28 |
| Rule: never suggest a downgrade | S05, S19, S29 |
| Alternative: another day | S11, S18, S19, S21, S26 |
| No better option exists | S12 |

### Step 4: Write the expected answer before running

Every expectation was worked out by hand from the rules in `SPEC.md` *before* running. When the engine disagreed,
the cause was investigated rather than the expectation simply overwritten:

- **S23:** the expectation was wrong. Starting at 13:30 leaves 12:00–13:30 free, which is 90 min and sellable,
  so 13:30 rightly beats 12:00.
- **S03:** originally the engine suggested moving the customer three days later just to *join* a session. The
  no-sharing rule (2026-11-07) removed joining, and the top option is now "start 10:30, same day".

When the no-sharing rule arrived, 9 scenarios were rewritten. All 27 matched the new hand-worked expectations on
the first run. S28–S29 were added for the no-downgrade rule.

### Next step: representative data

These days are realistic-looking but invented. The best addition is a **replay of a real past week**: enter the
actual sessions in a new day file and the actual enquiries as scenarios, with what sales actually did as the
expected answer.

---

## 2. The scenarios

### The week (`week.json`)

Each session is one group in one zone. Numbers are people in that group.

| Date | Day | Purpose | Sessions |
| --- | --- | --- | --- |
| 2026-12-01 | Tue | Quiet: two lunch groups, two zones free at 12:00 | 12:00 Classic 8, 12:00 Premium 14 |
| 2026-12-02 | Wed | Lunch peak: all 4 zones taken around 12:00 | 2× 11:00 Premium + CT (25, 20), 11:30 Premium 25, 12:00 Classic 18, 18:00 Premium 22 |
| 2026-12-03 | Thu | Fragmented: sessions scattered through the day | 09:00 Classic 12, 10:30 Premium 20, 13:00 Classic 10, 14:00 Premium 16, 16:00 Premium + CT 25 |
| 2026-12-04 | Fri | Ops locked the plan: every session pinned | A: 09:00 Premium, 12:00 Premium + CT · B: 10:00 Premium, 13:00 Premium + CT · C, D: Premium + CT at 09:00, 12:00, 15:00 |
| 2026-12-05 | Sat | Sold out: every zone back to back 09:00–21:00 | 23 groups; some smaller than 25 (spare seats exist but are not sellable) |
| 2026-12-06 | Sun | Big groups: a 60-person company in three zones | 3× 10:00 Premium (25, 25, 10), 14:00 Classic 20 |
| 2026-12-07 | Mon | Private event 14:00–17:00 blocks the venue | 10:00 Classic 10 + blackout |
| 2026-12-08 | Tue | Closed all day | — |

### The enquiries (`scenarios.json`)

"CT" = Chef's Table. "First option" is the top alternative the engine must suggest.

| ID | Date | Enquiry | What it tests | Expected verdict | Expected detail |
| --- | --- | --- | --- | --- | --- |
| S01 | Tue 12-01 | 12:00 Premium, 11 | Spare seats in the 12:00 Premium zone aren't sold: own zone | GOOD | one zone |
| S02 | Tue 12-01 | 12:00 Premium, 30 | Takes exactly the last two free zones | GOOD | 2 zones; "needs 2 zones (25 + 5)" |
| S03 | Tue 12-01 | 10:00 Premium, 10 | Start leaves 09:00–10:00 unsellable | COSTLY | "09:00–10:00"; first option: 10:30 Premium |
| S04 | Tue 12-01 | 15:00 Classic + CT, 4 (warning below 6) | Small-group warning | COSTLY | "only 4 people" |
| S05 | Tue 12-01 | 19:30 Premium, 8 | Ends after closing time; no Classic offered (downgrade) | NOT POSSIBLE | "21:00 close"; first option: 19:00 Premium |
| S06 | Tue 12-01 | 10:15 Classic, 4 | Start not on the 30-min grid | NOT POSSIBLE | "every 30 min" |
| S07 | 2027-06-02 | 10:30 Premium, 10 | Last day of the 6-month window | GOOD | |
| S08 | 2027-06-03 | 10:30 Premium, 10 | One day beyond the window | NOT POSSIBLE | "6-month" |
| S09 | 2026-11-30 | 10:30 Premium, 10 | Date in the past | NOT POSSIBLE | "in the past" |
| S10 | Wed 12-02 | 12:00 Classic, 7 | 7 spare seats in the 12:00 Classic zone can't be sold | NOT POSSIBLE | "All 4 zones are in use at 12:00" |
| S11 | Wed 12-02 | 12:00 Classic, 10 | All zones taken at peak | NOT POSSIBLE | "All 4 zones are in use at 12:00"; first option: Thu 12:00 Classic |
| S12 | Wed 12-02 | 17:30 Premium, 3 | Small group gets a whole zone; nothing better exists | GOOD | one zone; no alternatives |
| S13 | Thu 12-03 | 12:30 Classic, 8 | Fits snugly between sessions | GOOD | one zone |
| S14 | Thu 12-03 | 10:30 Classic, 6 (turnaround 15) | 1.5 h gap too short once reset is added | COSTLY | "09:00–10:30"; first option: 11:00 Classic |
| S15 | Fri 12-04 | 11:00 Premium, 4 | Free time exists only by moving pinned sessions | NOT POSSIBLE | "without moving pinned or locked sessions" |
| S16 | Fri 12-04 | 16:00 Classic, 10 | Fits into a zone freed at 16:00 | GOOD | one zone |
| S17 | Sat 12-05 | 13:00 Premium, 8 adults + 2 children | 10 spare seats in the 13:00 Premium zone can't be sold | NOT POSSIBLE | "All 4 zones are in use at 13:00" |
| S18 | Sat 12-05 | 13:00 Premium, 11 | Sold out: nearest day offered | NOT POSSIBLE | "All 4 zones are in use"; first option: Sun 13:00 Premium |
| S19 | Sat 12-05 | 15:00 Classic + CT, 5 | Sold out: nearest day offered, not a switch to Classic | NOT POSSIBLE | first option: Sun 15:00 Classic + CT |
| S20 | Sun 12-06 | 10:00 Premium, 15 | Takes the last free zone, leaves 09:00–10:00 unsellable | COSTLY | one zone; "09:00–10:00"; first option: 10:30 Premium |
| S21 | Sun 12-06 | 10:00 Premium, 26 | Needs 2 zones, only 1 free | NOT POSSIBLE | "Only 1 zone free at 10:00; this group needs 2"; first option: Mon 10:00 Premium |
| S22 | Sun 12-06 | 09:00 Classic, 75 | Needs 3 zones, only 1 free at 10:00 | NOT POSSIBLE | "Only 1 zone free at 10:00; this group needs 3" |
| S23 | Sun 12-06 | 13:00 Premium, 40 | Two zones leave a gap | COSTLY | 2 zones; "12:00–13:00"; first option: 13:30 Premium |
| S24 | Mon 12-07 | 13:00 Premium, 10 | Runs into the private event | NOT POSSIBLE | "14:00–17:00 blackout"; first option: 12:00 Premium |
| S25 | Mon 12-07 | 17:00 Classic + CT, 6 | Right after the private event | GOOD | one zone |
| S26 | Tue 12-08 | 12:00 Premium, 10 | Closed day | NOT POSSIBLE | "closed"; first option: Wed 12-09 12:00 Premium |
| S27 | Tue 12-01 | 12:00 Premium, 60 | Needs 3 zones, only 2 free | NOT POSSIBLE | "Only 2 zones free at 12:00; this group needs 3" |
| S28 | Tue 12-01 | 19:00 Classic, 8 | Upgrade offered: Premium fills 19:00–21:00 | COSTLY | "20:30–21:00"; options include 19:00 Premium |
| S29 | Tue 12-01 | 19:00 Classic + CT, 8 | Premium would fit but drops the Chef's Table: not offered | COSTLY | first option: 19:30 Classic + CT; options exclude 19:00 Premium |

### Business decisions these scenarios reflect (2026-11-07)

- **No sharing:** a taken zone is sold out (S01, S10, S17).
- **No downgrades:** a lower level or losing the Chef's Table is never suggested; Classic + CT ↔ Premium counts
  as a downgrade both ways (S05, S19, S29). Upgrades can be suggested (S28).
- **Small-group warning:** off for now (S04 shows what it does when switched on).
- **Concurrency:** all 4 zones can run at once with any mix of ticket types; no Chef's Table limit.
