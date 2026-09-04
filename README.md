# Longitudinal Evolution of Online No-Limit Hold'em Strategy

Empirical study of how PokerStars 6-max NL cash game strategy evolved across **~15 years** and **three stake tiers**, using a large corpus of raw hand histories.

## Overview

This project analyzes population-level and player-level strategic change over time, comparing **NL50**, **NL200**, and **NL5K** datasets. The study addresses six research questions (RQ1–RQ6) covering preflop/postflop evolution, cross-stake differences, positional matchups, outcomes/variance, player survivorship, and temporal effects.

**Key docs**

| File | Purpose |
|------|---------|
| [`scope.md`](scope.md) | Boundaries, inclusion rules, RQs |
| [`outline.md`](outline.md) | Study outline + work tracker |
| [`docs/parquet_schema.md`](docs/parquet_schema.md) | Parser output tables (hands, players, actions) |
| [`docs/metric_definitions.md`](docs/metric_definitions.md) | VPIP, PFR, c-bet rules — **you fill this in** |

## Data

| Dataset | Stakes | Years | Files | Size |
|---------|--------|-------|-------|------|
| `NL50/` | $0.25 / $0.50 | 2010–2025 | ~81k | ~26 GB |
| `NL200/` | $1 / $2 | 2011–2025 | ~134k | ~22 GB |
| `NL5K/` | $25 / $50 | 2011–2025 | ~67k | ~8 GB |

Raw archives live in `data/raw/`. Hand history folders are **gitignored** (too large for version control).

### Inclusion criteria (applied at parse time)

- **Study window:** 2011–2025
- **Currency:** USD only
- **Cap games:** excluded (decision may be revisited after hand-count audit)
- **Table size:** 6-max
- Raw `.txt` files are never deleted

## Pipeline

```
raw .txt  →  parse_corpus.py  →  Parquet (data/parsed/)
          →  compute_all_metrics.py / compute_extended_metrics.py / compute_player_year_metrics.py
          →  reports/*.csv  →  Power BI
```

**Current status:** Full corpus parsed (22.3M primary hands). Population + player-year metrics exported to `reports/metrics_*.csv` (CSV only; ready for Power BI).

## Setup

Requires **Python 3.12+**.

```powershell
cd C:\Users\olles\code\dataAnalysisProject

# Create / activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### Dependencies

| Package | Role |
|---------|------|
| pandas | Data manipulation |
| pyarrow | Parquet I/O |
| duckdb | SQL analytics |
| matplotlib | Coverage heatmaps |
| jupyter | Notebooks |
| tqdm | Progress bars |

## Scripts

```powershell
# Pre-parse audits (outputs in reports/)
python scripts/run_overnight_audits.py
python scripts/plot_coverage_heatmap.py

# Parse full corpus → data/parsed/
python scripts/parse_corpus.py
python scripts/parse_corpus.py --force   # re-parse after parser fixes

# Population metrics → reports/metrics_*.csv
python scripts/compute_all_metrics.py --path data/parsed
python scripts/compute_extended_metrics.py --path data/parsed

# Player-year panel (RQ3/RQ4)
python scripts/compute_player_year_metrics.py --path data/parsed

# Validate metrics
python scripts/validate_metrics.py --path data/parsed
```

**Power BI import (CSV only):**

| Table | File | Typical RQ |
|-------|------|------------|
| Overall | `metrics_overall.csv` | context |
| By year | `metrics_by_year.csv` | RQ1 |
| Preflop / postflop | `metrics_preflop.csv`, `metrics_postflop.csv` | RQ1 |
| By position | `metrics_by_position.csv` | RQ2 |
| Matchups | `metrics_matchups.csv` | RQ2 |
| 3-bet sizing | `metrics_3bet_sizing.csv` | RQ1/RQ2 |
| bb/100 | `metrics_bb100.csv` | supporting |
| Monthly volume | `hand_counts_monthly.csv` | RQ5 context |
| Player panel | `metrics_player_year.csv` | RQ3/RQ4 |
| Homogeneity | `metrics_player_dispersion.csv` | RQ3 |
| Stayers / YoY | `metrics_player_stayers.csv` | RQ4 |

Outputs go to `reports/`.

## Project layout

```
dataAnalysisProject/
├── README.md           # This file
├── scope.md            # Project scope
├── outline.md          # Study outline + tracker
├── requirements.txt
├── .gitignore
├── scripts/            # Audit and parsing scripts
├── reports/            # Counts, heatmaps, exploration summaries
├── data/
│   └── raw/            # Original zip archives (local only)
├── NL50/               # Extracted hand histories (local only)
├── NL200/
└── NL5K/
```

## Status

- [x] Download and extract all three datasets
- [x] Pre-parse audits + coverage heatmaps
- [x] Parser + full corpus Parquet (`data/parsed/`)
- [x] Full-corpus metrics export (`reports/metrics_*.csv`)
- [x] Player-year / dispersion / stayers exports
- [ ] Optional re-parse after net_won polish (bb/100 only)
- [ ] Power BI / visualization dashboards
- [ ] Analysis (RQ1–RQ5)

---

*Last updated: 2026-08-26*
