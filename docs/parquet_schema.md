# Parquet Schema

This document defines the **structured data model** between raw hand histories and analysis. The parser's job is to extract facts from `.txt` files. **Metrics** (VPIP, PFR, c-bet, etc.) are computed later in DuckDB/SQL using the rules in [`metric_definitions.md`](metric_definitions.md).

```
PokerStars .txt  →  Python parser  →  Parquet tables  →  DuckDB  →  metrics & analysis
```

---

## Design principles

1. **Store facts, not metrics** — save actions and context; derive VPIP/PFR/c-bet in queries.
2. **One hand = one row in `hands`** — join everything else on `hand_id`.
3. **Partition Parquet** by `dataset` and `year` for fast filtering.
4. **Apply inclusion filters at parse time** — USD only, non-cap, 2011–2025, 6-max (see `scope.md`).
5. **Keep parse errors** in a separate log, not in the main tables.

---

## File layout (planned)

```
data/parsed/
├── hands/dataset=NL50/year=2018/part-000.parquet
├── players/dataset=NL50/year=2018/part-000.parquet
├── actions/dataset=NL50/year=2018/part-000.parquet
└── parse_errors.jsonl
```

---

## Table: `hands`

One row per hand.

| Column | Type | Description |
|--------|------|-------------|
| `hand_id` | string | PokerStars hand/game ID (primary key) |
| `dataset` | string | `NL50`, `NL200`, or `NL5K` |
| `timestamp` | timestamp | Hand start time (UTC or ET as in file) |
| `year` | int | From timestamp |
| `month` | int | From timestamp |
| `stakes_raw` | string | Original stakes string from header |
| `sb` | float | Small blind in USD |
| `bb` | float | Big blind in USD — **use for all RFI/3-bet/4-bet price logic** (never hard-code stake sizes) |
| `is_usd` | bool | Parsed/filter flag |
| `is_cap` | bool | Cap game flag |
| `table_name` | string | Table name |
| `max_players` | int | Table size (expect 6) |
| `button_seat` | int | Dealer seat number |
| `num_players_dealt` | int | Players dealt in — filter `= 6` for primary analysis (see metric_definitions.md) |
| `num_players_flop` | int | Players seeing flop |
| `pot_total` | float | Total pot (from summary) |
| `rake` | float | Rake taken |
| `board_flop` | string | e.g. `[Ah Kd 7c]` or null |
| `board_turn` | string | Turn card or null |
| `board_river` | string | River card or null |
| `last_street` | string | `preflop`, `flop`, `turn`, `river`, `showdown` |
| `had_showdown` | bool | Whether hand went to showdown |
| `source_file` | string | Relative path to source `.txt` |

**Not stored here (computed later):** VPIP, PFR, aggression factor, bb/100.

---

## Table: `players`

One row per player per hand.

| Column | Type | Description |
|--------|------|-------------|
| `hand_id` | string | FK → `hands` |
| `player_name` | string | Screen name |
| `seat` | int | Seat number (1–6) |
| `position` | string | `LJ`, `HJ`, `CO`, `BTN`, `SB`, `BB` (see metric_definitions.md) |
| `stack_start` | float | Starting stack in USD |
| `net_won` | float | Net result for this hand (+ won, − lost) |
| `hole_card_1` | string | e.g. `Ah` or null if not shown |
| `hole_card_2` | string | e.g. `Kd` or null |
| `showed_hand` | bool | Showdown or showed at end |
| `is_hero` | bool | Optional: if we ever track a single hero; usually false for population study |

**Computed later from `actions`:** `vpip`, `pfr`, `three_bet`, `saw_flop`, `folded_street`, etc.

---

## Table: `actions`

One row per action (including blind posts).

| Column | Type | Description |
|--------|------|-------------|
| `hand_id` | string | FK → `hands` |
| `action_index` | int | 0-based order within hand |
| `street` | string | `preflop`, `flop`, `turn`, `river` |
| `player_name` | string | Who acted |
| `action_type` | string | Normalized type (see below) |
| `amount` | float | Amount put in this action (0 for check/fold) |
| `to_call` | float | Amount facing before action (optional, helps 3-bet logic) |
| `is_all_in` | bool | All-in flag |
| `raw_line` | string | Original text line (debugging) |

### Normalized `action_type` values

| Value | PokerStars examples |
|-------|---------------------|
| `post_sb` | posts small blind |
| `post_bb` | posts big blind |
| `post_ante` | posts ante |
| `fold` | folds |
| `check` | checks |
| `call` | calls |
| `bet` | bets |
| `raise` | raises |
| `all_in` | may also set `is_all_in=true` on call/raise |
| `return` | uncalled bet returned |
| `show` | shows hand |
| `muck` | doesn't show |

**Important for metrics:** preserve `action_index` order — RFI, 3-bet, c-bet logic depends on sequence and who acted first on each street.

---

## Table: `parse_errors` (optional JSONL)

| Column | Type | Description |
|--------|------|-------------|
| `source_file` | string | File path |
| `hand_id` | string | If partial parse |
| `error_type` | string | `truncated`, `duplicate`, `bad_header`, etc. |
| `message` | string | Detail |

---

## What the parser must decide (logic you define)

These are **not** columns — they are SQL/Python rules over `actions`. Document your rules in `metric_definitions.md`.

| Metric | Needs from schema |
|--------|-------------------|
| **VPIP** | Preflop actions; exclude blind posts; what counts as voluntary |
| **PFR** | First raise preflop; exclude limp-call |
| **RFI** | First **raise** (not call) when action is unopened; not a limp |
| **3-bet** | Raise after an open raise; action sequence on preflop |
| **Fold to 3-bet** | Facing a 3-bet after you opened |
| **Steal** | RFI from CO/BTN/SB when folds to you |
| **C-bet** | Preflop aggressor bets flop; heads-up or multiway? (you define) |
| **Fold to c-bet** | Facing c-bet on flop |

Your metric doc should specify edge cases: limps, dead blinds, all-ins, cap games (filtered out), multiway pots, checks that aren't c-bet opportunities, etc.

---

## DuckDB usage (later)

```sql
-- Example: join for analysis (metrics defined in metric_definitions.md)
SELECT h.year, h.dataset, p.position, ...
FROM hands h
JOIN players p ON h.hand_id = p.hand_id
JOIN actions a ON h.hand_id = a.hand_id
WHERE h.is_usd AND NOT h.is_cap
```

DuckDB reads Parquet directly:

```sql
INSTALL httpfs; -- if needed
SELECT * FROM read_parquet('data/parsed/hands/dataset=NL50/year=2018/*.parquet');
```

---

## Open questions (fill in as we parse samples)

- [ ] Position mapping for 6-max when 2–5 players dealt (empty seats)
- [ ] How to handle `Run it twice` / side pots (if present)
- [ ] All-in before flop — does PFR count if shove is first raise?
- [ ] Multiway c-bet — count only HU flop or all spots?
- [ ] Currency parsing — confirm USD-only after filter

---

*Last updated: 2026-08-11*
