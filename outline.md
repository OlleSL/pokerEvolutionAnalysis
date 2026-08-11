# Study Outline & Work Tracker

**Project:** Longitudinal Evolution of Online No-Limit Hold'em Strategy  
**Last updated:** 2026-08-11  
**Scope reference:** [`scope.md`](./scope.md)

This document is the full study outline (sections 1–9) plus a practical work tracker. Update task statuses as work progresses.

---

## Phase Overview

| Phase | Name | Maps to | Status |
|-------|------|---------|--------|
| 0 | Setup & documentation | §1 | **In progress** |
| 1 | Data acquisition | §3.3 | **Complete** |
| 2 | Extraction & spot validation | §3.3 | **Complete** |
| 3 | Data audit, cleaning, parsing | §3.4–3.5, §4.1 | Not started |
| 4 | EDA & dataset overview | §5.1 | Not started |
| 5 | Core analysis (RQ1–RQ6) | §4.2–4.10, §5.2–5.10 | Not started |
| 6 | Discussion, conclusion, write-up | §6–8 | Not started |

---

# 1. Introduction

## 1.1 Background

- Online No-Limit Hold'em as a strategic environment  
- Imperfect information and strategic interaction  
- Players continuously adapt to opponents and the wider player pool  
- Rules remain relatively constant; population strategy can evolve  
- Increasing availability of tracking software, solvers, GTO tools, training sites, and large-scale statistical information  

## 1.2 Motivation

- Investigate whether and how online poker strategy changed over ~10 years  
- Use a large longitudinal dataset, not short-term samples  
- Compare multiple stakes  
- Investigate population-level and individual-player changes  
- Compare long-term active players vs players who disappear from the observed pool  

## 1.3 Research Gap

Existing research covers performance, behavior, strategy classification, persistence, and gambling behavior — but comparatively little on **long-term strategic evolution** across stakes, positions, matchups, individual players, and survivorship.

## 1.4 Research Questions

See [`scope.md`](./scope.md) §3 for RQ1–RQ6 summary.

| RQ | Title |
|----|-------|
| RQ1 | Evolution of Play |
| RQ2 | Evolution Across Stakes |
| RQ3 | Positional and Matchup Dynamics |
| RQ4 | Outcomes and Risk |
| RQ5 | Player Survivorship and Adaptation |
| RQ6 | Temporal and External Effects |

---

# 2. Related Work

## 2.1 Poker Performance and Persistence
- Whether poker performance persists over time  
- Player-level performance; skill vs variance  
- Prior studies using online hand-history data  

## 2.2 Poker Player Strategy Classification
- Tight/aggressive/loose/passive archetypes  
- Clustering based on observed actions  
- Relevance to player-archetype analysis (§4.8)  

## 2.3 Longitudinal Analysis of Online Behavior
- Tracking individuals over time  
- Behavioral adaptation; population-level change  
- Entry and exit from populations  

## 2.4 Large-Scale Behavioral Data
- Methodologies from longitudinal datasets, population evolution, survival analysis, behavioral adaptation (including non-poker fields)  

## 2.5 Research Gap
- Summarize what prior work has not addressed and how this study contributes  

**Status:** Not started

---

# 3. Data

## 3.1 Dataset

- PokerStars NL Hold'em **cash games**, **6-max**  
- ~10 years; three stakes (NL50, NL200, NL5K)  
- ~30 million hands (exact count after audit)  

## 3.2 Raw Hand Histories

PokerStars text format including: hand IDs, timestamps, stakes, players, positions, actions, pots, rake, boards, results.

## 3.3 Data Collection

| Item | Status |
|------|--------|
| NL50 archive downloaded & extracted | Done |
| NL200 archive downloaded & extracted | Done |
| NL5K archive downloaded & extracted | Done |
| Archives stored in `data/raw/` | Done |
| Coverage by year/stake documented | Not started |

### Current inventory (file-level, pre-audit)

```
NL50/   80,734 txt   2010–2025   ~25.7 GB
NL200/ 133,553 txt   2011–2025   ~22.2 GB
NL5K/    66,727 txt   2011–2025    ~8.1 GB
```

## 3.4 Data Cleaning

Inclusion filters (applied at parse time; raw files kept):

- [x] Exclude **cap games** (all stakes)
- [x] Exclude **non-USD** (EUR, GBP, etc.)
- [x] Limit to **2011–2025**
- [ ] Corrupted / unreadable hand histories  
- [ ] Duplicate hands  
- [ ] Incomplete hands  
- [ ] Invalid/inconsistent records  
- [ ] Formatting differences between years  
- [ ] Exact hand counts with filters — `scripts/count_hands.py`  
- [ ] Coverage heatmap — `scripts/plot_coverage_heatmap.py`  
- [ ] Optional subsampling for balanced stake × year comparisons  

## 3.5 Data Representation

```
Raw hand histories → Python → structured data → Parquet → DuckDB
```

| Table | Key fields |
|-------|------------|
| **Hands** | hand_id, timestamp, year, month, stake, game_type, table_size, pot, rake, board |
| **Players** | hand_id, player_id/name, seat, position, stack, result |
| **Actions** | hand_id, player, street, action, amount, action_sequence |
| **Showdowns** | hand_id, player, hole_cards, showdown_info |

---

# 4. Methodology

## 4.1 Data Processing Pipeline

```
Raw hand histories
      ↓
 Python parser
      ↓
 Validation and cleaning
      ↓
 Structured data (Parquet)
      ↓
 DuckDB
      ↓
 Statistical analysis
```

## 4.2 Strategic Metrics

**Preflop:** VPIP, PFR, RFI, 3-bet, 4-bet, fold to 3-bet, steal, squeeze  
**Postflop:** flop c-bet, turn double-barrel, river aggression, check-raise, fold to c-bet, bet frequency, bet sizing  

## 4.3 Positional Metrics

UTG, MP, CO, BTN, SB, BB — frequencies, win rates, aggression, position-specific changes over time.

## 4.4 Matchup Analysis

- **BTN vs BB:** open frequency, BB fold/call/3-bet, BTN response  
- **SB vs BB:** open/limp, aggression, postflop behavior, profitability  

## 4.5 Temporal Analysis

- Year-by-year; across stakes, positions, player groups  
- **Year × Stake** interactions — did stakes evolve differently?  

## 4.6 Player-Level Longitudinal Analysis

- **Player × Year × Stake** profiles  
- Track: volume, VPIP, PFR, 3-bet, 4-bet, aggression, steal, positional behavior, win rate  
- **Critical distinction:** population change vs individual player adaptation  

## 4.7 Survivorship Analysis

- Define active-player threshold (e.g. minimum hands/year)  
- 1-, 3-, 5-, 10-year survival within dataset  
- Entry/exit, volume changes, stake movement, strategy changes  
- Methods: Kaplan–Meier, Cox proportional hazards (if appropriate)  
- Terminology: **observed survivorship in dataset**, not proof of quitting poker  

## 4.8 Player Archetypes

Exploratory clustering on VPIP, PFR, 3-bet, 4-bet, c-bet, aggression, steal, BB defense.

- Cluster count and characteristics  
- Prevalence over time  
- Movement between profiles  

## 4.9 Outcomes and Risk

bb/100, all-in EV, actual winnings, EV/winnings gap, standard deviation, variance, volatility.

## 4.10 Statistical Methods

Descriptive statistics, confidence intervals, **effect sizes**, regression, trend analysis, Year × Stake interactions, survival analysis, clustering.

> With ~30M hands, statistical significance alone is insufficient — emphasize effect size and practical significance.

---

# 5. Results

*To be populated after analysis.*

| Section | Content | RQ | Status |
|---------|---------|-----|--------|
| 5.1 | Dataset overview (hands, players, years, stakes, volume, quality) | — | Not started |
| 5.2 | Evolution of preflop strategy | RQ1 | Not started |
| 5.3 | Evolution of postflop strategy | RQ1 | Not started |
| 5.4 | Differences across stakes | RQ2 | Not started |
| 5.5 | Positional and matchup evolution | RQ3 | Not started |
| 5.6 | Outcomes and variance | RQ4 | Not started |
| 5.7 | Player-level evolution | RQ5 | Not started |
| 5.8 | Player survivorship | RQ5 | Not started |
| 5.9 | Player archetypes | RQ5 | Not started |
| 5.10 | Temporal/external events | RQ6 | Not started |

---

# 6. Discussion

## 6.1 Interpretation of Findings
## 6.2 Evolution of the Poker Meta
## 6.3 Differences Between Stakes
## 6.4 Player Adaptation
## 6.5 Survivorship
## 6.6 Broader Implications
## 6.7 Limitations

- Dataset coverage and selection bias  
- Player identification across years  
- **Survivorship bias** within dataset  
- Missing players/hands; stake migration  
- Rake/game condition changes  
- PokerStars population shifts  
- Confounding variables; observational (not experimental) data  
- NL5K cap-game vs deep-stack NL50/NL200  

**Status:** Not started

---

# 7. Conclusion

## 7.1 Summary
## 7.2 Contributions
## 7.3 Future Research

- More stakes/sites/formats; tournament poker  
- Longer time periods; GTO/solver comparisons  
- Predictive modeling of player survival  

**Status:** Not started

---

# 8. References

Academic papers, books, datasets, software documentation.

**Starting points:**
- *Beyond Chance? The Persistence of Performance in Online Poker*  
- Player strategy clustering/classification literature  
- Longitudinal behavioral analysis  
- Survival analysis and clustering methodology  

**Status:** Not started

---

# 9. Appendices

| Appendix | Content |
|----------|---------|
| A | Data processing — parser, cleaning rules, exclusion criteria |
| B | Metric definitions — exact VPIP, PFR, 3-bet, c-bet, etc. |
| C | Statistical model specifications |
| D | Additional results tables/figures |

---

# Work Tracker

## Phase 0 — Setup & Documentation

| Task | Status | Notes |
|------|--------|-------|
| Project workspace | Done | |
| `scope.md` | Done | Aligned with study plan |
| `outline.md` | Done | This file |
| Literature review (§2) | Not started | |
| Initialize git repo | Done | `.gitignore` excludes data; no commits yet |
| Python venv + dependencies | Done | `.venv/` — see `requirements.txt` |
| Python env + dependencies | Not started | Target: Python → Parquet → DuckDB |

## Phase 1 — Data Acquisition ✅

| Task | Status | Notes |
|------|--------|-------|
| Download & move archives to `data/raw/` | Done | NL50, NL200, NL5K |
| Extract to dataset folders | Done | |
| Extract nested archives (7-Zip) | Done | ~5,190 archives |
| Spot-check format & stakes | Done | |

## Phase 2 — Data Audit & Cleaning

| Task | Status | Notes |
|------|--------|-------|
| Count total hands (all stakes) | In progress | Est. ~47M from 600-file sample; full count pending |
| Coverage by year × stake documented | In progress | See `reports/data_exploration_summary.md` |
| 6-max filter verification | Done (sample) | 100% 6-max in 600-file sample |
| Duplicate detection | Not started | |
| Corrupt/incomplete hand log | In progress | 11 empty files in sample; few parse gaps |
| Data audit report (§3.4) | In progress | `reports/data_exploration_summary.md` |

## Phase 3 — Parsing & Infrastructure

| Task | Status | Notes |
|------|--------|-------|
| Evaluate/build PokerStars parser | Not started | |
| Define schema (§3.5) | Not started | |
| Pilot parse (1 year × 3 stakes) | Not started | |
| Full-corpus parse → Parquet | Not started | |
| Load into DuckDB | Not started | |
| Metric computation layer | Not started | §4.2–4.4 |

## Phase 4 — Core Analysis

| Task | Status | RQ |
|------|--------|-----|
| Preflop evolution | Not started | RQ1 |
| Postflop evolution | Not started | RQ1 |
| Cross-stake comparison & convergence | Not started | RQ2 |
| Positional & matchup analysis | Not started | RQ3 |
| Outcomes & variance | Not started | RQ4 |
| Player × Year × Stake profiles | Not started | RQ5 |
| Survivorship analysis | Not started | RQ5 |
| Player archetype clustering | Not started | RQ5 |
| Temporal/event analysis (e.g. COVID) | Not started | RQ6 |

## Phase 5 — Write-Up

| Task | Status | Section |
|------|--------|---------|
| Results draft | Not started | §5 |
| Discussion & limitations | Not started | §6 |
| Conclusion & future work | Not started | §7 |
| References | Not started | §8 |
| Appendices | Not started | §9 |

---

## Directory Layout

```
dataAnalysisProject/
├── scope.md
├── outline.md
├── data/
│   ├── raw/              # Original zip archives
│   └── parsed/           # Parquet output (planned)
├── NL5K/                 # Extracted hand histories
├── NL200/
├── NL50/
├── scripts/              # Parser, metrics, analysis (planned)
├── notebooks/            # EDA and results (planned)
└── reports/              # Audit, drafts, figures (planned)
```

---

## Decision Log

| Date | Decision | Outcome |
|------|----------|---------|
| 2026-08-11 | Inclusion rules | **USD only, no cap, 2011–2025**; subsample later if needed for balance |
| 2026-08-11 | Pipeline order | **Filter during parse** → Parquet → DuckDB (not pre-clean raw files) |
| 2026-08-11 | Dataset naming | `NL5K/`, `NL200/`, `NL50/` |
| 2026-08-11 | Keep nested archives | Retained for now (~5 GB) |

---

## Next Actions

1. **Exact hand counts (filtered)** — `python scripts/count_hands.py` (~2–4 hrs full corpus; use `--dataset NL50` to split up)
2. **Coverage heatmap** — `python scripts/plot_coverage_heatmap.py` (after step 1; needs `pip install matplotlib`)
3. **Sample parser** with filters on e.g. 2018 → validate → full parse → Parquet → DuckDB

---

## Session Notes

| Date | Summary |
|------|---------|
| 2026-08-10 | Project started |
| 2026-08-11 | All data extracted & validated (~280k txt files) |
| 2026-08-11 | Project docs created; layout cleaned (`data/raw/`, NL5K rename) |
| 2026-08-11 | `scope.md` and `outline.md` aligned with full study plan (RQ1–RQ6) |
| 2026-08-11 | Initial data exploration — `reports/data_exploration_summary.md` |
| 2026-08-11 | Inclusion rules locked; added `count_hands.py` and `plot_coverage_heatmap.py` |
| 2026-08-11 | Git `.gitignore`, `requirements.txt`, `.venv` with full stack installed |
