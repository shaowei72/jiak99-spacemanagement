# How the engine works

A plain-English tour of `packages/engine` (SPEC v0.4, zone routes). File names point to the code that does each
part.

## 1. The picture it works with

The engine sees a day as a grid of 4 zones × 24 half-hour slots (09:00–21:00):

```
        09:00 09:30 10:00 10:30 11:00 ...  20:30
Zone A   [ ]   [ ]   [ ]   [ ]   [ ]  ...   [ ]
Zone B   [ ]   [ ]   [ ]   [ ]   [ ]  ...   [ ]
Zone C   [ ]   [ ]   [ ]   [ ]   [ ]  ...   [ ]
Zone D   [ ]   [ ]   [ ]   [ ]   [ ]  ...   [ ]
```

Each cell is free, holds a group, is being reset (turnaround), or is blacked out.

A **route** is a path through the grid. A PHE group at 10:00 on route `ADBC` takes A 10:00, D 10:30, B 11:00 and
C 11:30: a staircase of four cells. Its **footprint** is the set of cells it takes. A PHE+CT route takes two cells
in a row in B and in C (its 60-minute visits).

**Speed trick (`timeline.py`):** the 96 cells are stored as the 96 bits of one whole number (a "mask"). Bit
`zone × 24 + slot` is 1 if that cell is in the set. "Do these two routes clash?" is then a single operation,
`a & b`, instead of a loop over cells.

## 2. The question to answer (`assign.py`)

Given the day's groups, pick one route for each so that:

1. **No clashes:** no cell holds two groups. Pinned or locked groups keep their route.
2. **Least waste:** among all valid plans, choose the one with the fewest **dead slots**.

A **dead slot** is a free cell that no new CHE, CHE+CT, PHE or PHE+CT group could ever use, given everything else.
Tingkat is ignored, because it fits almost anywhere and would hide real waste. To test a cell, the engine tries
every route of every ticket and start time that passes through it. If none fits, the cell is dead. This is
`dead_mask` in `timeline.py`.

The number of possible plans explodes: 15 groups with up to 24 routes each is more combinations than could ever be
tried one by one. The engine avoids trying them all with the ideas below.

## 3. Search 1: "Can the rest still fit?" (`_can_finish`)

The engine places groups one at a time, **in start-time order**. For each group it tries each route that doesn't
clash, then moves on to the next group. If a group has no route left, it backs up and tries the previous group's
next route. This is called depth-first search with backtracking.

**What makes it fast is remembering.** Because groups are placed in start order, a group starting at 14:00 can never
touch a cell before 14:00. So whether the remaining groups can still fit depends only on the cells **from 14:00
onwards**. Two different mornings that leave the same afternoon look identical to this question.

The engine stores each answer (yes/no, plus the first route that works) under a key made of the step number and the
cells from the current start onwards. When it meets the same situation again, it looks the answer up instead of
searching again. This is called memoisation.

This search is always exact. If a plan exists it finds one, and if it says "not possible", it has proved it.

## 4. Search 2: fewest dead slots (`_best`)

This is the optimisation. It walks the same tree (groups in start order, each route tried in turn), but only goes
down branches where `_can_finish` says yes, so it never wastes time on dead ends.

**Counting waste as it goes ("settling").** Whether a cell is dead depends only on cells within about 3 hours of it,
the longest route. Once the search has moved more than about 3 hours past a cell, nothing placed later can change
it. So the engine counts the cell at that point ("settles" it) and drops it from what it needs to remember. The total
waste is the sum of these counts.

**Remembering again.** What's left to decide depends only on cells that aren't settled yet, so results are
memoised under that smaller key. The answer stored is "from this situation, the fewest dead slots still to come is X,
by choosing route Y here".

**Pruning (branch and bound).** This is what makes the optimisation fast:

- A free cell that is already dead and lies *before* the current group's start time can never recover. Every later
  group starts later and can't use it.
- So at each point the engine knows a **minimum it will pay anyway**.
- If that minimum already reaches the best plan found so far, the whole branch is skipped.
- It also stops early when it finds a branch that costs no more than that unavoidable minimum, because nothing can
  beat it.

On the hardest generated test day this cut the states visited from 18,368 to 501, and the time from 400 ms to 24 ms.

**Is it really the best plan?** Yes. The property tests (`tests/test_properties.py`) generate hundreds of random
days, try *every* combination of routes by brute force, and check that the engine's answer matches the true minimum.
A separate slow, simple version of the dead-slot rule checks `dead_mask`.

**The time budget.** One enquiry check gets 200 ms in total (SPEC §4.1, `budget.py`). If time runs out, the
optimiser stops improving. For each group still left, it takes the first route that fits, which search 1 already
knows, so finishing never needs backtracking. "Does it fit?" stays exact; only the "least waste" polish is cut short.
In practice most checks finish well inside the budget.

## 5. How an enquiry check uses this (`evaluate.py`, `alternatives.py`)

Take "10:00 · PHE · 10":

1. **Validity:** is the start on the grid, does it end by 21:00, is the date in the booking window, and is the time
   clear of blackouts and closed days?
2. **After:** add the new group (split evenly if over 25, e.g. 40 → 20 + 20) as groups with no fixed route yet, and
   plan the day.
   - **Not possible:** no plan exists. The reason only says what is true in *every* plan of the day, e.g.
     "Zone A is in use 10:00–11:00." A slot counts as in use only if no plan can keep it free, so a group the
     app could move elsewhere is never blamed. (The engine asks for a plan that keeps that one slot free; if
     there is none, the slot is in use.)
3. **Before** (only when it fits): the best plan for the day as it is, giving dead slots *before*.
   - **Good:** dead slots after = before.
   - **Costly:** dead slots went up. The reason lists the new dead cells, e.g. "leaves B 20:00–20:30 and
     C 20:30–21:00 unsellable."
4. **Alternatives:** the same check runs on about 25 variations:
   - start ±30 and ±60 minutes;
   - upgrades only: never a downgrade, never Tingkat as a replacement;
   - the nearest other day.

   Variations strictly better than the request are kept and ranked, and the top 3 are returned. Ranking is by
   verdict, then added waste, then number of sub-groups, then size of change. The "before" plan for each date is
   worked out once and reused.

Existing groups whose route isn't pinned can be **re-routed** to make room for a new booking. That's why a
booking's route can change until ops locks the day, and why the Outlook title shows the current order.

## Where to look in the code

| File | What it does |
| --- | --- |
| `rules.py` | Tickets, zone sets, visit lengths, every possible route (`ROUTES`), opening hours |
| `model.py` | `Session` (a group with an optional route), `DayState`, `Settings`, `Enquiry` |
| `timeline.py` | The grid as masks, route footprints, the dead-slot rule (`dead_mask`), `dead_gaps` for display |
| `assign.py` | The two searches: `_can_finish` (fit) and `_best` (fewest dead slots) |
| `evaluate.py` | One enquiry as asked: validity, splitting, verdict, reason texts |
| `alternatives.py` | Variations of the request, ranking, the top 3 |
| `availability.py`, `metrics.py` | The times-that-fit board and day figures, built on `assign` |
