# RQ6 — Temporal / External Effects (Annotated Timeline)

**Status:** Draft associations only — **not causal proof**.  
**Sources:** public poker-industry chronology + statistical break years from `reports/rq6_breakpoints.csv` (two-piece OLS grid search on `metrics_by_year.csv` / `metrics_preflop.csv`).  
**Regenerate breakpoints:** `python scripts/compute_survivorship.py`

---

## How to read this

- **External era markers** are candidate confounders / context for interpreting population shifts.
- **Statistical break years** minimize two-piece OLS SSE on yearly population series. A high `sse_improvement_frac` means a split linear trend fits better than one line — it does **not** identify which event caused the break.
- Multiple external events can overlap one break year; stake tiers can break in different years.

---

## Candidate external eras (approximate)

| Window | Marker | Why it may matter |
|--------|--------|-------------------|
| ~2009–2012 | Tracking / HUD mainstream at mid–low stakes | Faster population learning of frequencies; tighter/reg-heavy pools earlier at NL200+ |
| ~2011–2014 | Training sites & coaching scale-up (videos, forums → structured courses) | More shared “default” strategies; rising PFR / aggression norms |
| ~2013–2016 | Solver tools enter serious study pipelines (PioSOLVER era → wider access) | More balanced c-betting, higher 3-bet, less pure polar defaults — especially postflop |
| ~2014–2017 | PokerStars ownership / product changes; competitive traffic shifts | Volume and recreational mix can move even if “strategy education” is unchanged |
| ~2016–2019 | Solver + GTO trainer / range tools more mainstream among regs | Population fold-to-3bet and sizing may re-stabilize after an overshoot period |
| **2020** | **COVID lockdowns** — massive online volume spike | Influx of recreational / returning players; temporary looseness and volume shocks |
| 2021–2023 | Post-COVID normalization + continued solver literacy | Pool may re-tighten; survivors vs short-timers diverge more clearly |
| 2024–2025 | Mature solver-era pool at studied stakes | High 3-bet floors; c-bet recovered from mid-2010s trough but below early-2010s peaks |

These dates are **rounded industry landmarks**, not measured treatment starts in this corpus.

---

## Statistical breaks already found (this repo)

From `reports/rq6_breakpoints.csv` (best interior break year; slopes are units of the metric **per year**):

### High-confidence shape changes (`sse_improvement_frac` ≳ 0.80)

| Dataset | Metric | Break year | Left slope | Right slope | Reading (descriptive) |
|---------|--------|------------|------------|-------------|------------------------|
| NL200 | fold_to_3bet_pct | **2018** | −2.47 | +1.37 | Steep decline then **slope flip** upward |
| NL50 | fold_to_3bet_pct | **2020** | −1.30 | +1.97 | Same U-shape; trough later than NL200 |
| NL50 | cbet_pct | **2017** | −1.62 | +0.35 | C-bet **trough** then mild recovery |
| NL200 | cbet_pct | **2015** | −2.32 | +0.47 | Earlier trough at mid stakes |
| NL200 | vpip_pct | **2018** | +0.58 | −0.33 | Rise then flatten/decline |
| NL50 | vpip_pct | **2020** | +0.46 | −0.70 | Peak near COVID window then drop |
| NL200 / NL50 | bb100 (population) | **2019 / 2020** | negative → positive | Population win-rate trough then partial recovery |

### Early PFR rise then flatten (matches exploratory note ~2014)

| Dataset | Metric | Break year | Left slope | Right slope |
|---------|--------|------------|------------|-------------|
| NL50 | pfr_pct | **2014** | +0.57 | +0.04 |
| NL200 | pfr_pct | **2013** | +0.65 | +0.02 |

PFR climbs early in the study window, then largely **plateaus** while 3-bet keeps rising — consistent with a shift from “raise more often” to “raise *and* re-raise more selectively.”

### Aligning breaks with external eras (associative only)

| Pattern in data | Nearest external markers (candidates) |
|-----------------|----------------------------------------|
| C-bet trough ~**2015–2018** | Solver mainstreaming / anti-auto-cbet learning |
| Fold-to-3bet slope flip ~**2018–2020** | Solver + trainer norms; then COVID mix shock |
| VPIP peak / break ~**2018–2020** | Late loose period + COVID recreational influx |
| PFR early rise then flat ~**2013–2014** | Pre-solver training / aggression culture |

---

## What this does *not* establish

1. **No causal identification** — no difference-in-differences, no synthetic control, no instrument for “solver adoption.”
2. **Coverage bias** — third-party HH collections can change which games/players are sampled over time.
3. **Stake migration** — players leaving NL50 for NL200 look like “leavers” in one dataset.
4. **Rake / traffic** — population `bb100` should track rake (see RQ4 notes); level shifts can be economic, not strategic.
5. **NL5K** — thinner sample; break years are noisier (`sse_improvement` often lower).

---

## Related outputs

| File | Role |
|------|------|
| `reports/rq6_breakpoints.csv` | Recomputed two-piece OLS breaks |
| `reports/metrics_by_year.csv` | Population VPIP/PFR/3-bet/c-bet/bb100 |
| `reports/metrics_preflop.csv` | Fold-to-3bet, 4-bet, etc. |
| `reports/hand_counts_monthly.csv` | Volume context (e.g. 2020) |
| `docs/results_draft.md` | Bullet findings RQ1–RQ6 |

---

*Last updated: 2026-09-08*
