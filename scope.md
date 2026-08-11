# Project Scope

**Project:** Longitudinal Evolution of Online No-Limit Hold'em Strategy  
**Workspace:** `C:\Users\olles\code\dataAnalysisProject`  
**Last updated:** 2026-08-11  
**Status:** Data acquisition and extraction complete; analysis not yet started

---

## 1. Purpose

This study investigates whether and how online poker strategy has changed over approximately a decade using a large longitudinal dataset of PokerStars 6-max No-Limit Hold'em cash game hand histories. Rather than short-term samples, the project compares strategic evolution across **three stakes**, at both **population** and **individual-player** levels, including **survivorship** among players who remain active over multiple years.

Use this document as the shared reference for collaborators and for tracking scope changes.

---

## 2. Background & Motivation

### 2.1 Background

Online No-Limit Hold'em is a strategic environment defined by imperfect information and continuous adaptation. The rules of poker remain relatively constant, but population strategy can evolve as players respond to opponents and to the wider pool. Increasing availability of tracking software, solvers, GTO tools, training sites, and large-scale statistical information may have accelerated strategic change.

### 2.2 Motivation

- Investigate long-term strategic evolution (~10 years), not just short windows  
- Use a large longitudinal dataset across multiple stakes  
- Compare population-level and individual-player changes  
- Examine whether long-term surviving players behave differently from players who disappear from the observed pool  

### 2.3 Research Gap

Existing research has examined poker performance, player behavior, strategy classification, persistence of performance, and gambling behavior. Comparatively little research examines the **long-term evolution of strategic characteristics** in an online poker population across:

- Multiple stakes  
- Positions and specific matchups  
- Individual players  
- Player survivorship and adaptation  

This study addresses that gap.

---

## 3. Research Questions

| # | Question | Focus |
|---|----------|-------|
| **RQ1** | **Evolution of Play** — How have key preflop and postflop strategic behaviors in 6-max online cash games evolved over the study period? | Population trends |
| **RQ2** | **Evolution Across Stakes** — How does strategic evolution differ across stakes, and have strategic behaviors converged or diverged between stakes over time? | NL50 vs NL200 vs NL5K |
| **RQ3** | **Positional and Matchup Dynamics** — How have positional strategies and key matchups (especially BTN vs BB, SB vs BB) evolved over time and across stakes? | Position & matchup |
| **RQ4** | **Outcomes and Risk** — How have win rates, all-in EV, and variance evolved over time and across stakes? | bb/100, EV, volatility |
| **RQ5** | **Player Survivorship and Adaptation** — How does strategic behavior evolve among multi-year active players, and how do long-term survivors differ from players who leave the observed pool? | Longitudinal players |
| **RQ6** | **Temporal and External Effects** — Are there identifiable temporal disruptions or external events associated with changes in behavior, volume, or game characteristics? | COVID, structural changes |

---

## 4. Data Assets

### 4.1 Datasets

| Dataset | Stakes | Year range | `.txt` files | Approx. size | Source archive |
|---------|--------|------------|--------------|--------------|----------------|
| `NL50/` | $0.25 / $0.50 | 2010–2025 | 80,734 | ~25.7 GB | `data/raw/Stars_NL50_2010-2025_9852867 (1).zip` |
| `NL200/` | $1 / $2 | 2011–2025 | 133,553 | ~22.2 GB | `data/raw/NL200_98523kjvfgjs52 (2).zip` |
| `NL5K/` | $25 / $50 (cap) | 2011–2025 | 66,727 | ~8.1 GB | `data/raw/5k_9852kjvfjsg5298g.zip` |

**Combined:** ~280,000 hand history files, ~56 GB extracted.  
**Estimated raw hands:** ~47M (pre-filter); exact included count pending `scripts/count_hands.py`.

### 4.2 Inclusion criteria (study corpus)

Raw files are **never deleted**. Filters are applied during parsing.

| Rule | Decision |
|------|----------|
| **Study window** | **2011–2025** (2010 excluded — only NL50 has it) |
| **Currency** | **USD only** — exclude EUR, GBP, and other non-USD |
| **Game type** | **Exclude all cap games** — exclude `$X Cap` and NL5K cap tables |
| **Table size** | **6-max tables only** |
| **Players dealt (primary analysis)** | **Exactly 6** — shorthanded hands excluded from main positional/matchup metrics (stored with flag for optional use) |
| **Coverage balance** | Keep all years for all stakes even when volume differs; may **subsample** high-volume stake-years later so comparisons stay clean |

### 4.3 Data characteristics

- **Platform:** PokerStars  
- **Format:** 6-max NL Hold'em cash games, raw `.txt` hand histories  
- **Fields available:** Hand IDs, timestamps, stakes, players, positions, actions, pots, rake, boards, results  
- **Structure:** `{dataset}/{year}/*.txt` (+ original `.zip` / `.rar` archives retained alongside extracted files)

### 4.4 Planned data representation

```
Raw hand histories → Python parser → validation/cleaning → Parquet → DuckDB → analysis
```

Structured tables: **Hands**, **Players**, **Actions**, **Showdowns** (see `outline.md` §3.5).

---

## 5. In Scope

- PokerStars 6-max NL **USD deep-stack** cash hand histories at NL50, NL200, and NL5K  
- Longitudinal analysis **2011–2025**  
- Preflop and postflop strategic metrics (VPIP, PFR, 3-bet, c-bet, etc.)  
- Positional and matchup analysis (BTN vs BB, SB vs BB)  
- Player-level longitudinal profiles (Player × Year × Stake)  
- Survivorship analysis within the observed dataset  
- Player archetype clustering (exploratory)  
- Outcomes and risk (bb/100, all-in EV, variance)  
- Temporal/event analysis (e.g. COVID period)  
- Reproducible pipeline, documented methods, academic write-up  

---

## 6. Out of Scope

- Live play, real-time tools, or bot development  
- Non-PokerStars sites or formats (unless explicitly added later)  
- **Cap games** (all stakes, including NL5K cap tables)  
- **Non-USD** tables (EUR, GBP, etc.)  
- **2010** data (not shared across all stakes)  
- Deanonymization beyond screen names in hand histories  
- Claiming observed exit equals permanent cessation of poker (use **observed survivorship** terminology)  
- Publishing or sharing raw hand history files externally  
- Relying on statistical significance alone at ~30M hands — emphasize **effect size** and practical significance  

---

## 7. Methodology (summary)

| Area | Approach |
|------|----------|
| **Pipeline** | Sample parser with filters → validate → full parse → Parquet → DuckDB |
| **Metrics** | Preflop (VPIP, PFR, RFI, 3-bet, steal, …), postflop (c-bet, barrel, check-raise, …) |
| **Positional** | UTG, MP, CO, BTN, SB, BB frequencies and win rates |
| **Matchups** | BTN vs BB, SB vs BB opening/defense/3-bet/response |
| **Temporal** | Year-by-year, Year × Stake interactions |
| **Player-level** | Distinguish population change vs individual adaptation |
| **Survivorship** | Kaplan–Meier, Cox models (if appropriate); 1/3/5/10-year survival thresholds |
| **Archetypes** | Clustering on VPIP, PFR, 3-bet, aggression, steal, BB defense |
| **Statistics** | Descriptive stats, CIs, effect sizes, regression, trend analysis |

Full detail: [`outline.md`](./outline.md) §4.

---

## 8. Deliverables

| Deliverable | Description | Status |
|-------------|-------------|--------|
| Raw data (extracted) | Three dataset folders with `.txt` hand histories | **Done** |
| `scope.md` / `outline.md` | Project scope and study outline | **Done** |
| Data audit & cleaning report | Hand counts, duplicates, corrupted/incomplete hands | Not started |
| Parsing pipeline | Raw → Parquet with documented schema | Not started |
| DuckDB analysis layer | Queryable structured data | Not started |
| Results (§5 of outline) | Findings per RQ1–RQ6 | Not started |
| Discussion & conclusions | Interpretation, limitations, contributions | Not started |
| References & appendices | Metric definitions, model specs, extra tables | Not started |

---

## 9. Constraints & Assumptions

### Constraints

- ~56 GB extracted (+ ~5 GB archives); parsed/derived data will require additional storage  
- Batch processing required for full-corpus parse and analysis  
- Third-party compiled hand histories — selection bias possible  
- NL5K is mostly cap games — after cap exclusion, remaining NL5K USD deep-stack volume may be small  
- Screen names may not reliably track the same human across years  

### Assumptions

- Hand histories are authentic PokerStars exports  
- 6-max cash games dominate the corpus (to be verified in parser)  
- Observed player exit reflects dataset coverage, not necessarily real-world quitting  
- Cross-stake comparisons may use subsampling so each year/stake contributes comparably  

---

## 10. Success Criteria

1. Full corpus parsed with documented error rates and hand/player counts  
2. All six research questions addressed with reproducible analysis  
3. Effect sizes and practical significance emphasized alongside statistical tests  
4. Limitations (coverage, survivorship bias, stake migration, rake changes) explicitly discussed  
5. Methods documented sufficiently for replication  

---

## 11. Scope Change Log

| Date | Change | Rationale |
|------|--------|-----------|
| 2026-08-11 | Initial scope document created | Project kickoff after data extraction |
| 2026-08-11 | Renamed `5k/` → `NL5K/`; archives → `data/raw/` | Naming and layout cleanup |
| 2026-08-11 | Aligned scope with full study plan (RQ1–RQ6) | Incorporated provided research outline |
| 2026-08-11 | Inclusion rules: USD only, no cap, 2011–2025 | Cleaner comparable corpus; raw files retained |

---

## 12. Key References

- **Study outline & work tracker:** [`outline.md`](./outline.md)  
- **Starting literature:** *Beyond Chance? The Persistence of Performance in Online Poker*; clustering/classification of player strategies; longitudinal behavioral analysis; survival analysis methodology
