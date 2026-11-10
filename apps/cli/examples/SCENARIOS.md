# Test scenarios

Files in this folder:

| File | What it is |
| --- | --- |
| `week.json` | Eight days of existing groups (Tue 2026-12-01 to Tue 2026-12-08), each built for a purpose |
| `scenarios.json` | 31 enquiries against that week, each with the expected answer |
| `busy-day.json` | Single example day used by the CLI tests and `uv run seed` |

Run them all with `uv run scenarios` (or `uv run scenarios --show S11` for one in full). They also run as part of
`uv run pytest`, so a change that alters any answer is caught. "Today" is fixed at **Tue 2026-12-01**.

**Key rules (SPEC §2, v0.4 zone routes):**

- Each zone has its own activity: A Tour/Exhibit, B Dining, C Chef's Table / Dining, D Sandart.
- A group moves through its ticket's zones back to back, in 30-min steps, in any order the app picks (its route):

  | Ticket | Zones (the app picks one set) | Stay |
  | --- | --- | --- |
  | Tingkat | B, C or D | 30 min |
  | CHE | A + one of B, C, D | 1 h |
  | CHE+CT | A + D + one of B, C | 1.5 h |
  | PHE | A, B, C, D | 2 h |
  | PHE+CT | A 30 min, B 1 h, C 1 h, D 30 min | 3 h |

- A 30-min zone slot holds one group at most, however few people are in it. Groups over 25 are split evenly
  (26 → 13 + 13, 40 → 20 + 20), each sub-group with its own route.
- Every ticket except Tingkat visits zone A, so **zone A is the bottleneck**: it holds 2 groups per hour.
- A free slot that no ticket except Tingkat could still use is "unsellable" (dead); a booking that adds dead slots
  is COSTLY.
- Alternatives never suggest a downgrade, and never Tingkat instead of another ticket.

---

## 1. Method used to construct the scenarios

### Step 1: List the factors that change the engine's answer

| Factor | Values tested |
| --- | --- |
| Day load | quiet · lunch peak · fragmented · sold out |
| Route planning | all floating · fully locked by ops (every route pinned) |
| Group size | small (below the warning size) · up to 25 · 26 (first split) · 30 · 40 · 60 · 75 |
| Room vs groups needed | **exactly enough** · **one short** (zone A per hour, zone slots per start) |
| Taken zone slots with spare seats | small groups, so spare seats exist but must not be sold |
| Start time | 09:00 (opening) · midday · near closing (19:00–19:30) · off the 30-min grid (10:15) |
| Ticket type | all five, incl. Tingkat · upgrade vs downgrade |
| Calendar | today · past · **last day of the 6-month window** · **one day beyond** · closed day · blackout |
| Settings | defaults · turnaround 15 min · small-group warning on |

### Step 2: Use boundary values, not random ones

Bugs cluster at edges, so the scenarios use them on purpose:

- a group that takes *exactly* the last free slot in each zone, next to one that needs one sub-group more than
  there is room for (S20/S21, S02/S27);
- spare seats in a taken zone slot that must not be sold (S01, S10, S17);
- day 183 vs day 184 of the booking window (S07/S08);
- a 1 h Classic that just misses zone A, while a 1.5 h Classic + CT reaches it (S14).

### Step 3: Cover every possible outcome at least once

| Outcome | Scenarios |
| --- | --- |
| Verdict `ACCEPT_GOOD` | S01, S02, S03, S07, S12, S13, S16, S20, S23, S25, S31 |
| Verdict `ACCEPT_COSTLY` | S04, S28, S29 |
| Verdict `NOT_POSSIBLE` | S05, S06, S08, S09, S10, S11, S14, S15, S17, S18, S19, S21, S22, S24, S26, S27, S30 |
| Placement: one group | S01, S12, S13, S16, S20, S25, S31 |
| Placement: split group | S02, S23 |
| Rule: spare seats in a taken zone slot are not sold | S01, S10, S17 |
| Reason: unsellable slots | S28, S29 |
| Reason: small group | S04 |
| Reason: a needed zone is in use | S10, S11, S14, S15 |
| Reason: every usable zone is in use | S17, S30 |
| Reason: not enough room in zone A for a split group | S21, S22, S27 |
| Reason: ends after close / off grid / past / beyond window | S05 / S06 / S09 / S08 |
| Reason: closed day / blackout | S26 / S24 |
| Alternative: shift start time | S05, S11, S24, S30 |
| Alternative: upgrade ticket type | S14, S28 |
| Rule: never suggest a downgrade (or Tingkat instead) | S05, S19, S29, S30 |
| Alternative: another day | S18, S19, S21, S26, S29 |
| No better option exists | S03, S12 |

### Step 4: Write the expected answer before running

Every expectation was worked out by hand from the rules in `SPEC.md` *before* running. When the engine disagreed,
the cause was investigated rather than the expectation simply overwritten. For the v0.4 rewrite (2026-11-09), 29 of
31 matched on the first run; both others were mistakes in the hand-worked expectations:

- **S30:** I expected 11:00 Tingkat first, but time shifts are only ±30 and ±60 min from the asked 13:00, so 11:00
  is never a candidate; 14:00 is right.
- **S14:** I expected the Classic to fit at a cost. With a 15-min reset, the two lunch groups can't use zone A at
  12:00 *and* 12:30 (the first one's reset blocks 12:30), so the Premium group's A visit and its reset take
  13:00–14:00. A 1 h Classic at 13:00 can't reach A; a 1.5 h Classic + CT can (A at 14:00), which is why it is offered.

Earlier history (SPEC v0.3, one zone per group): S23 and S03 were investigated the same way; see git history.

### Next step: representative data

These days are realistic-looking but invented. The best addition is a **replay of a real past week**: enter the
actual groups in a new day file and the actual enquiries as scenarios, with what sales actually did as the
expected answer.

---

## 2. The scenarios

### The week (`week.json`)

Each entry is one group (or sub-group). Numbers are people in that group. Routes are given only where ops pinned
them; elsewhere the engine plans the routes.

| Date | Day | Purpose | Groups |
| --- | --- | --- | --- |
| 2026-12-01 | Tue | Quiet: two lunch groups | 12:00 Classic 8, 12:00 Premium 14 |
| 2026-12-02 | Wed | Lunch peak: every zone slot 12:00–14:00 taken | 4× 12:00 Premium (25, 20, 18, 7), 18:00 Premium 22 |
| 2026-12-03 | Thu | Fragmented: groups scattered through the day | 09:00 Classic 12, 10:30 Premium 20, 13:00 Classic 10, 14:00 Premium 16, 16:00 Premium + CT 25 |
| 2026-12-04 | Fri | Ops locked the plan: every route pinned | 09:00 PHE `ABCD`, 10:00 PHE `BCDA`, 12:00 PHE+CT `ABCD`, 15:00 PHE+CT `DCBA`, 3× 19:00 PHE `ABCD` `BCDA` `CDAB` |
| 2026-12-05 | Sat | Sold out: every zone slot 09:00–21:00 taken | 4 Premium groups every 2 h from 09:00 (24 groups, some small) |
| 2026-12-06 | Sun | Big groups: a 60-person company as three groups | 3× 10:00 Premium 20, 14:00 Classic 20 |
| 2026-12-07 | Mon | Private event 14:00–17:00 blocks the venue | 10:00 Classic 10 + blackout |
| 2026-12-08 | Tue | Closed all day | — |

### The enquiries (`scenarios.json`)

Labels: Tingkat, CHE = Classic, CHE+CT = Classic + Chef's Table, PHE = Premium, PHE+CT = Premium + Chef's Table.
"First option" is the top alternative the engine must suggest.

| ID | Date | Enquiry | What it tests | Expected verdict | Expected detail |
| --- | --- | --- | --- | --- | --- |
| S01 | Tue 12-01 | 12:00 PHE, 11 | Spare seats in taken zone slots aren't sold: own route | GOOD | one group |
| S02 | Tue 12-01 | 12:00 PHE, 30 | Split evenly; zone A then full 12:00–14:00 | GOOD | "split into 2 groups (15 + 15)" |
| S03 | Tue 12-01 | 10:00 PHE, 10 | The hour before is still sellable (e.g. by a Classic) | GOOD | no alternatives |
| S04 | Tue 12-01 | 15:00 CHE+CT, 4 (warning below 6) | Small-group warning | COSTLY | "only 4 people" |
| S05 | Tue 12-01 | 19:30 PHE, 8 | Ends after closing time; no Classic offered (downgrade) | NOT POSSIBLE | "21:00 close"; first option: 19:00 PHE |
| S06 | Tue 12-01 | 10:15 CHE, 4 | Start not on the 30-min grid | NOT POSSIBLE | "every 30 min" |
| S07 | 2027-06-02 | 10:30 PHE, 10 | Last day of the 6-month window | GOOD | |
| S08 | 2027-06-03 | 10:30 PHE, 10 | One day beyond the window | NOT POSSIBLE | "6-month" |
| S09 | 2026-11-30 | 10:30 PHE, 10 | Date in the past | NOT POSSIBLE | "in the past" |
| S10 | Wed 12-02 | 12:30 CHE, 7 | The 7-person group's 18 spare seats can't be sold | NOT POSSIBLE | "Zone A is in use 12:30–13:30" |
| S11 | Wed 12-02 | 12:00 CHE, 10 | Zone A full at lunch; an hour earlier fits | NOT POSSIBLE | "Zone A is in use 12:00–13:00"; first option: 11:00 CHE |
| S12 | Wed 12-02 | 17:30 PHE, 3 | Small group gets its own route; nothing better exists | GOOD | one group; no alternatives |
| S13 | Thu 12-03 | 12:00 CHE, 8 | Fits between groups | GOOD | one group |
| S14 | Tue 12-01 | 13:00 CHE, 6 (turnaround 15) | Resets leave no A slot for 1 h; 1.5 h reaches A at 14:00 | NOT POSSIBLE | "Zone A is in use 13:00–14:00"; first option: 13:00 CHE+CT |
| S15 | Fri 12-04 | 11:30 CHE, 4 | Pinned routes take zone A 11:30–12:30 | NOT POSSIBLE | "Zone A is in use 11:30–12:30" |
| S16 | Fri 12-04 | 11:00 PHE, 10 | Fits around pinned routes; A only free at 11:00 | GOOD | one group; route starts in A |
| S17 | Sat 12-05 | 13:00 PHE, 8 adults + 2 children | Sold out, though the 10-person group has 15 spare seats | NOT POSSIBLE | "Zones A, B, C and D are in use 13:00–15:00" |
| S18 | Sat 12-05 | 13:00 PHE, 11 | Sold out: nearest day offered | NOT POSSIBLE | first option: Sun 13:00 PHE |
| S19 | Sat 12-05 | 15:00 CHE+CT, 5 | Sold out: nearest day, not a switch to Classic | NOT POSSIBLE | first option: Sun 15:00 CHE+CT |
| S20 | Sun 12-06 | 10:00 PHE, 15 | Takes the last free slot in every zone 10:00–12:00 | GOOD | one group |
| S21 | Sun 12-06 | 10:00 PHE, 26 | Needs 2 groups, room for 1 | NOT POSSIBLE | "Zone A has room for only 1 group in 10:00–12:00"; first option: Mon 10:00 PHE |
| S22 | Sun 12-06 | 09:00 CHE, 75 | 3 groups need A within one hour; A holds 2 | NOT POSSIBLE | "Zone A has room for only 2 groups in 09:00–10:00" |
| S23 | Sun 12-06 | 13:00 PHE, 40 | Split around the 14:00 Classic group | GOOD | "split into 2 groups (20 + 20)" |
| S24 | Mon 12-07 | 13:00 PHE, 10 | Runs into the private event | NOT POSSIBLE | "14:00–17:00 blackout"; first option: 12:00 PHE |
| S25 | Mon 12-07 | 17:00 CHE+CT, 6 | Right after the private event | GOOD | one group |
| S26 | Tue 12-08 | 12:00 PHE, 10 | Closed day | NOT POSSIBLE | "closed"; first option: Wed 12-09 12:00 PHE |
| S27 | Tue 12-01 | 12:00 PHE, 60 | 3 groups, zone A has room for 2 | NOT POSSIBLE | "Zone A has room for only 2 groups in 12:00–14:00" |
| S28 | Fri 12-04 | 19:00 CHE, 8 | Classic strands B and C at the end of the day; upgrade fills them | COSTLY | "B 20:00–20:30 and C 20:30–21:00"; first option: 19:00 PHE |
| S29 | Fri 12-04 | 19:00 CHE+CT, 8 | PHE would fill the evening but drops the Chef's Table: not offered | COSTLY | "C 20:30–21:00"; first option: Thu 19:00 CHE+CT; options exclude 19:00 PHE |
| S30 | Wed 12-02 | 13:00 Tingkat, 4 | B, C and D full; other times offered, never another ticket in its place | NOT POSSIBLE | "Zones B, C and D are in use at 13:00"; first option: 14:00 Tingkat |
| S31 | Fri 12-04 | 16:00 CHE, 10 | Fits around the pinned afternoon route | GOOD | one group |

### Business decisions these scenarios reflect

- **Zone routes (2026-11-09):** zones have their own activity; groups move between them; Classic and Classic + CT
  may use alternative zone sets; Tingkat added; even splits.
- **No sharing (2026-11-07, per zone slot since 2026-11-09):** a taken zone slot is sold out (S01, S10, S17).
- **No downgrades:** a lower level or losing the Chef's Table is never suggested; Classic + CT ↔ Premium counts as
  a downgrade both ways (S05, S19, S29). Tingkat is never offered instead of another ticket (S30). Upgrades can be
  suggested (S14, S28).
- **Small-group warning:** off for now (S04 shows what it does when switched on).
- **Large groups across several start times:** not suggested yet (Phase 2); sales book each part separately.
