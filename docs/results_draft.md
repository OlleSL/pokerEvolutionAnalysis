# Results Draft (CSV-grounded)

**Status:** Draft working notes — not final paper prose.  
**Data:** `reports/metrics_*.csv` only (no Parquet re-parse).  
**Date:** 2026-09-08

Numbers are approximate; regenerate survivorship / breakpoints with:

```bash
python scripts/compute_survivorship.py --figures
```

---

## RQ1 — Evolution of Play

- **VPIP (population):** Mid-stakes rise then partial retreat. NL200 ~24.2% (2011) → peak ~27.8% (2018) → ~25.3% (2025). NL50 similar: ~24.7% → ~28.5% (2020) → ~25.6% (2025).
- **PFR:** Early climb, then flatten. NL200 ~16.0% (2011) → ~17.5% (2014) and stays ~17.1–17.8% thereafter. Two-piece break ~2013–2014 (`rq6_breakpoints.csv`).
- **3-bet:** Steady long rise. NL200 6.8% → 9.4%; NL50 5.8% → 8.7% (2011→2025).
- **C-bet:** Clear mid-decade trough. NL200 69% (2011) → **57.9% (2016)** → recovery to ~62–64%. NL50 trough ~59% (2017).
- **Fold to 3-bet:** U-shape. NL200 58% (2011) → **~43% (2018–19)** → ~51% (2025). Slope flip break ~2018 (NL200) / ~2020 (NL50).

---

## RQ2 — Evolution Across Stakes

- Directionally **same arcs** at NL50 and NL200 (VPIP hump, c-bet trough, rising 3-bet); NL200 often leads timing (earlier c-bet trough, earlier fold-to-3bet flip).
- **NL5K** thinner and noisier; VPIP stays higher (~26–29% early) with smaller survivor vs leaver gaps than mid-stakes.
- Cross-stake **levels** still differ (e.g. early PFR higher at NL200/NL5K than NL50), but **trends** look more parallel than diverging after ~2016.
- Full convergence tests (interaction models) still exploratory — use `metrics_by_year.csv` + stake×year plots.

---

## RQ3 — Positional / Matchup Dynamics

- Exported: `metrics_by_position.csv`, `metrics_matchups.csv` (BTN/BB, SB/BB, etc.).
- Draft takeaway from prior metric export: blind defense and steal dynamics move with the same aggression / fold-to-3bet era shifts as RQ1; detailed position-by-year write-up still pending Power BI / notebook pass.
- Player homogeneity: `metrics_player_dispersion.csv` (IQR/SD of player rates by stake×year).

---

## RQ4 — Outcomes and Risk

### What we can say (CSV-only)

- **Population bb/100** (`metrics_bb100.csv`, granularity=`year` / `overall`):
  - NL200 overall **−5.81** bb/100; tracks roughly with rake equiv **−5.88** (validation mindset: population should lose ~rake).
  - NL50 overall **−9.11** vs rake equiv **−9.24**.
  - NL5K overall **−0.57** vs rake equiv **−0.50** (thin / different mix).
- **NL200 yearly:** ~−4.7 to −5.4 early → trough ~**−6.5** (2019–20) → recovery toward **−5.6** (2025).
- **Player-year dispersion** (`metrics_bb100_dispersion.csv`): among players with ≥500 hands, NL200 median player bb/100 stays near 0 to −5; **IQR widens** from ~21 (2011) toward ~33–37 later — a simple volatility / heterogeneity proxy.

### Blocked / deferred

- **All-in EV:** not computable from these CSVs; needs hole cards at all-in. Anonymous HHs omit many hole cards → sparse/biased. Documented in `docs/metric_definitions.md` (All-in EV). **Deferred.**
- **NL50 player-level bb100** looks systematically implausible (medians ~180+ bb/100 in survivorship tables) — treat as **broken pending local `net_won` re-parse**. Prefer NL200/NL5K for player outcome contrasts; population NL50 yearly series may still be usable for rake-tracking checks.

---

## RQ5 — Player Survivorship and Adaptation

**Definitions** (`scripts/compute_survivorship.py`):  
- **survivor_3plus:** ≥3 distinct years in `metrics_player_year.csv` (already ≥500 hands/year).  
- **one_year:** observed in exactly one year.  
- **two_year:** bridge group (reported in CSVs).

### Counts (career, all years)

| Dataset | one_year | survivor_3plus |
|---------|----------|----------------|
| NL50 | 12,098 | 688 |
| NL200 | 6,567 | 768 |
| NL5K | 506 | 240 |

### Style contrast (unweighted mean of per-player career means)

| Dataset | Cohort | VPIP | PFR | 3-bet | Fold→3bet | C-bet | BB fold | median bb100 |
|---------|--------|------|-----|-------|-----------|-------|---------|--------------|
| NL200 | one_year | **30.5** | 16.2 | 6.9 | 41.3 | **67.3** | 61.4 | −6.9 |
| NL200 | survivor | **24.0** | 17.5 | 8.3 | 51.0 | **61.4** | 68.4 | +2.5 |
| NL50 | one_year | **28.7** | 15.8 | 6.5 | 43.3 | 65.1 | 66.0 | *(unreliable)* |
| NL50 | survivor | **23.4** | 16.8 | 7.5 | 49.9 | 60.2 | 71.5 | *(unreliable)* |
| NL5K | one_year | 29.0 | 18.2 | 7.9 | 44.8 | 67.7 | 61.5 | −4.2 |
| NL5K | survivor | 26.2 | 18.2 | 8.4 | 51.5 | 65.6 | 61.8 | +1.4 |

**Pattern:** Survivors are **tighter (lower VPIP)**, slightly **higher PFR / 3-bet**, **fold more to 3-bets**, **c-bet less**, and **fold more from BB** than one-year players — i.e. more selective/reg-like. At NL200, hand-weighted mean bb100 is ~**−3.7** (one-year) vs ~**+3.0** (survivors).

### By era (NL200 VPIP example)

| Era | one_year VPIP | survivor VPIP | Gap |
|-----|---------------|---------------|-----|
| 2011–2015 | 25.5 | 23.7 | ~1.8 |
| 2016–2020 | **33.7** | 24.6 | **~9.1** |
| 2021–2025 | **33.0** | 23.8 | **~9.2** |

Survivor–leaver style gap **widens after 2015**; short-timers look much looser in the mid/late window while survivors stay near ~24% VPIP.

Outputs: `metrics_survivorship_summary.csv`, `metrics_survivorship_by_era.csv`, figures under `reports/survivorship_figures/`.

Stayers YoY deltas remain in `metrics_player_stayers.csv` (adaptation among multi-year players — complementary to survivor vs leaver levels).

---

## RQ6 — Temporal / External Effects

See **`docs/rq6_timeline.md`**. Short version:

- Strongest statistical breaks: **fold-to-3bet flip ~2018–2020**, **c-bet trough ~2015–2017**, **PFR flatten ~2013–2014**, **VPIP peak/break ~2018–2020**.
- Candidate external context: solver mainstreaming, training culture, PokerStars product/traffic shifts, **COVID 2020** volume/mix shock.
- Explicitly **associational** — do not claim event X caused metric Y.

---

## Remaining gaps (not blocked on this PR)

1. All-in EV deferred (hole cards).
2. Local re-parse for **NL200/NL5K `net_won` polish** and NL50 bb100 fix (Parquet; not in this cloud workspace).
3. Formal write-up / Power BI dashboards; archetype clustering polish if desired.
4. Survival curves (Kaplan–Meier) still optional — current RQ5 uses multi-year thresholds, which matches available CSV grain.

---

*Draft only — cite CSVs, not this file, in any final manuscript.*
