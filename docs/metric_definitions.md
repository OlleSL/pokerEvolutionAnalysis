# Metric Definitions

**Purpose:** Rulebook for turning parsed `actions` / `players` / `hands` into statistics. Implemented in DuckDB SQL or Python using `[parquet_schema.md](parquet_schema.md)`.

**Status:** Core metrics defined (2026-08-11).

---

## Global conventions

### Streets 

- `preflop` — before flop is dealt
- `flop`, `turn`, `river` — after board cards for that street

### Action order

- Use `action_index` from the `actions` table (ascending).
- Blind posts (`post_sb`, `post_bb`) happen first preflop.

### Stake-aware amounts (NL50 / NL200 / NL5K)

Hand histories do **not** say “1 BB” — they use dollar amounts. All preflop price logic must use the `**bb` value parsed from that hand** (from `hands.bb`).


| Concept              | Implementation                               |
| -------------------- | -------------------------------------------- |
| “1 BB” / limp level  | `amount <= bb` (call BB)                     |
| “Above 1 BB” / raise | `amount > bb` (or raise action type)         |
| Open sizing          | First raise where total to-call exceeds `bb` |


**Examples:**

- NL50: `bb = 0.50` — limp/call BB is $0.50; raise to $1.25 is > 1 BB
- NL200: `bb = 2.00`
- NL5K: `bb = 50.00`

Never hard-code blind sizes; always join `hands.bb` for the hand.

### Table & player count

**Table type:** 6-max tables only (from hand history header).

`**num_players_dealt`:** Store on every hand. Use for analysis filters:


| Filter                  | When to use                                                       |
| ----------------------- | ----------------------------------------------------------------- |
| `num_players_dealt = 6` | Primary analysis — positional stats, matchups, population metrics |
| `num_players_dealt < 6` | Exclude by default; optional secondary analysis only              |


Shorthanded tables play differently; filtering to 6 dealt keeps statistics clean. Parser keeps all hands; queries apply the filter.

### Positions (6-max)

Use **LJ, HJ, CO, BTN, SB, BB** (not UTG/MP).

When fewer than 6 players are dealt, drop positions from the **left**:


| Players dealt | Positions               |
| ------------- | ----------------------- |
| 6             | LJ, HJ, CO, BTN, SB, BB |
| 5             | HJ, CO, BTN, SB, BB     |
| 4             | CO, BTN, SB, BB         |
| 3             | BTN, SB, BB             |
| 2             | SB, BB                  |


Assign relative to button and seats actually dealt in.

---

## Preflop metrics

### VPIP — Voluntarily Put $ In Pot

**Intent:** Player voluntarily puts additional money in preflop beyond forced blinds.

**Counts as VPIP:**

- Call/limp beyond forced blind (putting money in beyond SB/BB post)
- Any preflop raise
- All-in call or raise preflop (when voluntary)

**Does NOT count:**

- Fold preflop
- SB/BB posting forced blinds only with no further money
- BB checking when limped to (no extra money beyond BB)

**Rule:** Blinds only count if player puts **more money in** after their forced post.

---

### PFR — Preflop Raise

**Intent:** Player increases the bet size **preflop only**.

**Counts as PFR:**

- Any preflop raise (open, 3-bet, 4-bet, 5-bet+, etc.)
- Preflop re-raise chains on the same street
- All-in preflop when it constitutes a raise

**Does NOT count:**

- Postflop bets or raises (use c-bet / barrel stats)
- Limp (call without raise)
- Call facing raise
- Blind posts alone

---

### RFI — Raise First In

**Intent:** First player to make the price **greater than 1 BB** (`amount > hands.bb`).

**Counts as RFI:**

- First raise above BB — **including over limpers**
- Example: limp, then raise → counts as RFI for the raiser

**Does NOT count:**

- Limp (call at BB level)
- Call facing existing raise
- Blind posts alone

**Track by position:** LJ, HJ, CO, BTN, SB.

---

### RFI when folded to

**Intent:** Replaces “steal.” All players before hero folded (no limps, no raises); hero is first to act with money.

**Counts as RFI when folded to:**

- Folded to player and they raise to **> hands.bb**

**Does NOT count:**

- Limp when folded to
- Action when prior player limped or raised

**Subset of RFI** with stricter pot condition.

---

### 3-bet

**Intent:** **Separate stat from 4-bet.** First re-raise over an open raise (pot already has one raise above BB).

**Counts as 3-bet:**

- Raise facing exactly one prior raise (the open)
- Squeeze: open + caller(s), then raise → still a **3-bet**  
Example: HJ opens, CO calls, BTN raises → BTN 3-bet

**Does NOT count:**

- Open raise (RFI)
- 4-bet (separate stat below)
- Call facing raise

**Opportunity / numerator:** Track 3-bet % = 3-bets / 3-bet opportunities (optional).

---

### 4-bet

**Intent:** **Separate stat from 3-bet.** Additional raise **on top of** an existing 3-bet.

**Counts as 4-bet:**

- Raise facing a 3-bet (re-raise over the 3-bettor)

**Does NOT count:**

- 3-bet
- 5-bet+ — track as 4-bet+ or separate bucket if needed later

**Not the same as 3-bet** — different opportunity and different numerator.

---

### Call 3-bet

**Intent:** Player **calls** (does not fold or re-raise) when facing a 3-bet.

**Opportunity:**

- Player faces a 3-bet (typically the original opener facing BTN/CO 3-bet, or cold-call spot facing open + 3-bet — define per analysis slice)

**Primary spot (recommended):**

- Player **made the open raise** (RFI / > BB)
- Faces a 3-bet
- **Calls** the 3-bet

**Counts as call 3-bet:** Call action in that spot.

**Does NOT count:** Fold (use fold to 3-bet) or 4-bet (use 4-bet).

---

### Call 4-bet

**Intent:** Player **calls** when facing a 4-bet.

**Opportunity:**

- Player **made the 3-bet**
- Faces a 4-bet

**Counts as call 4-bet:** Call action in that spot.

**Does NOT count:** Fold or 5-bet re-raise.

---

### Fold to 3-bet

**Opportunity:** Player was the **original raiser** (> BB) and faces a 3-bet.

**Counts as fold to 3-bet:** Player folds.

**Denominator:** Open → face 3-bet. **Numerator:** Fold.

---

### Fold to 4-bet

**Opportunity:** Player made the **3-bet** and faces a 4-bet.

**Counts as fold to 4-bet:** Player folds.

---

### Removed metrics

- **Steal** — use **RFI when folded to**
- **Squeeze** — not separate; squeeze spots count as **3-bet**

---

## Postflop metrics

### Preflop aggressor (PFA)

**Definition:** Last player to **raise** preflop. If no raise, no PFA (limped pot).

---

### Flop c-bet

**Opportunity:** Flop dealt; PFA in hand; PFA can act on flop.

**Counts as flop c-bet:** PFA **bets** flop.

**Does NOT count:** Non-PFA bet (donk); PFA check.

---

### Turn c-bet

**Opportunity:** Turn dealt; PFA in hand; PFA can act on turn.

**Counts as turn c-bet:** PFA **bets** turn.

---

### River c-bet

**Opportunity:** River dealt; PFA in hand; PFA can act on river.

**Counts as river c-bet:** PFA **bets** river.

---

### Double barrel

**Separate stat from triple barrel.**

**Intent:** PFA bets **flop and turn** (when opportunity exists on both streets).

**Counts as double barrel:**

- PFA bets flop **and** PFA bets turn

**Opportunity:** Flop and turn reached; PFA was flop bettor and faces turn.

**Does NOT count:** Turn bet without flop bet; river-only aggression.

---

### Triple barrel

**Separate stat from double barrel.**

**Intent:** PFA bets **flop, turn, and river** (when opportunity exists on each street).

**Counts as triple barrel:**

- PFA bets flop, turn, **and** river

**Opportunity:** All three postflop streets reached with PFA betting each.

---

### Fold to c-bet (flop / turn / river)

**Per street.**

**Counts as fold to c-bet:**

- Player is **not** the PFA
- PFA bets that street
- Player folds

**Notes:** Can be multiway; only vs **PFA’s** bet.

---

### Check-raise (flop / turn / river)

**Counts as check-raise:**

- Player checks
- Another player bets
- Player raises

**Typical spot:** OOP (e.g. BB vs BTN).

---

### Deferred

- **Aggression factor** — skipped for now

---

## Positional & matchup stats

### BTN vs BB

- BB first response facing a **BTN open raise** (fold / call / 3-bet)
- Exported in `reports/metrics_matchups_by_year.csv` (`btn_bb_*` columns)

### SB first-in

- When folded to the SB: **raise / limp / fold**
- Exported as `sb_raise_pct`, `sb_limp_pct`, `sb_fold_pct` in the same matchups file

### BB defense

- BB fold / call / 3-bet vs **any** open (LJ–SB)
- Exported as `bb_fold_pct`, `bb_call_pct`, `bb_three_bet_pct`

### 3-bet sizing

- Size of 3-bet in big blinds (`amount / bb`)
- `avg_3bet_size_bb`, `median_3bet_size_bb`, and average excluding large shoves (`< 8 BB`) in `metrics_3bet_sizing_by_year.csv`

### River c-bet vs triple barrel

- **River c-bet:** preflop aggressor bets the river whenever the hand reaches the river (broader)
- **Triple barrel:** preflop aggressor bets **flop and turn and river** (continuation chain only)
- Both are useful; they answer different questions

### All-in EV

**Status:** Not implemented. Requires showdown equity (both players' hole cards + board) at the all-in moment. Anonymous hand histories often omit hole cards for folders, so all-in EV is sparse and biased toward showdown hands. Prefer bb/100 as the outcome supporting metric for now.

---

## Implementation checklist

- Join `hands.bb` for all preflop price thresholds (never hard-code stakes)
- Store `num_players_dealt`; default filter `= 6` for positional analysis
- 3-bet and 4-bet as **separate** opportunity/numerator pairs
- Add call 3-bet, call 4-bet, fold to 4-bet
- Double barrel and triple barrel as **separate** stats
- PFR: preflop only

---

*Last updated: 2026-08-11*