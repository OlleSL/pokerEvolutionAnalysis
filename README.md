# Longitudinal Evolution of Online No-Limit Hold'em Strategy

Empirical study of how PokerStars 6-max NL cash game strategy evolved across **~15 years** and **three stake tiers**, using a large corpus of raw hand histories.

## Overview

This project analyzes population-level and player-level strategic change over time, comparing **NL50**, **NL200**, and **NL5K** datasets. The study addresses six research questions (RQ1–RQ6) covering preflop/postflop evolution, cross-stake differences, positional matchups, outcomes/variance, player survivorship, and temporal effects.

**Key docs**

| File | Purpose |
|------|---------|
| [`scope.md`](scope.md) | Boundaries, inclusion rules, RQs, deliverables |
| [`outline.md`](outline.md) | Full study outline + work tracker |

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

## Pipeline (planned)

```
raw .txt  →  Python parser (with filters)  →  Parquet  →  DuckDB  →  analysis
```

**Current status:** Data extracted and under audit. Parser and Parquet layer not yet built.

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
# Exact hand counts with USD / cap / included breakdown (~2–4 hrs full corpus)
python scripts\count_hands.py
python scripts\count_hands.py --dataset NL50
python scripts\count_hands.py --quick

# Coverage heatmap (after hand count)
python scripts\plot_coverage_heatmap.py

# Initial exploration audit
python scripts\explore_data.py
```

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
- [x] Spot-validate hand history format
- [x] Initial data exploration
- [ ] Exact hand counts + coverage heatmap (in progress)
- [ ] Lock cap-game policy for NL5K
- [ ] Sample parser with inclusion filters
- [ ] Full parse to Parquet → DuckDB
- [ ] Analysis (RQ1–RQ6)

---

*Last updated: 2026-08-11*
