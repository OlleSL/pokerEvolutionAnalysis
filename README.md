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
raw .txt  →  parse_corpus.py  →  Parquet (data/parsed/)  →  compute_all_metrics.py  →  reports/
```

**Current status:** Full corpus parsed (22.3M primary hands). Metrics exported to `reports/metrics_*.csv`. Re-parse running after `net_won` parser fix.

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

# Export all metrics → reports/metrics_*.csv
python scripts/compute_all_metrics.py --path data/parsed --json

# Validate metrics
python scripts/validate_metrics.py --path data/parsed
```

Power BI / visualization: load CSVs from `reports/`. Use `metrics_by_position.csv` for VPIP/PFR/RFI (not legacy sample files).

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
- [ ] Re-parse after net_won fix (in progress when triggered)
- [ ] Power BI / visualization dashboards
- [ ] Analysis (RQ1–RQ6)

---

*Last updated: 2026-08-26*
