"""RQ5 survivorship + lightweight RQ4/RQ6 CSV exports (CSV-only; no Parquet).

Reads existing reports/metrics_*.csv and writes:

  - reports/metrics_survivorship_summary.csv
      Survivor (years_active >= 3) vs one-year / two-year style means by dataset
  - reports/metrics_survivorship_by_era.csv
      Same comparison within observation eras (2011–2015, 2016–2020, 2021–2025)
  - reports/metrics_bb100_dispersion.csv
      Player-year bb/100 dispersion by dataset × year (min-hands filter)
  - reports/rq6_breakpoints.csv
      Two-piece OLS break-year grid search on population yearly series
  - reports/survivorship_figures/*.png  (optional; --figures)

Definitions
-----------
- Player grain is dataset × player_name (a player can appear in multiple stakes).
- years_active = count of distinct years with a player-year row (already ≥500 hands
  in metrics_player_year.csv from compute_player_year_metrics.py).
- survivor_3plus: years_active >= 3
- one_year: years_active == 1
- two_year: years_active == 2  (reported for context; primary contrast is survivor vs one_year)

Style metrics are first averaged within player across their years, then averaged
across players in the cohort. Hand-weighted cohort means and medians are also
exported (bb/100 is heavy-tailed; NL50 bb/100 is known-unreliable pending net_won
re-parse).

Usage:
  python scripts/compute_survivorship.py
  python scripts/compute_survivorship.py --min-survivor-years 3 --figures
  python scripts/compute_survivorship.py --reports-dir reports
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORTS = PROJECT_ROOT / "reports"

STYLE_COLS = [
    "vpip_pct",
    "pfr_pct",
    "three_bet_pct",
    "fold_to_3bet_pct",
    "cbet_pct",
    "bb_fold_pct",
    "bb100",
]

ERAS = [
    ("2011-2015", 2011, 2015),
    ("2016-2020", 2016, 2020),
    ("2021-2025", 2021, 2025),
]

BREAKPOINT_METRICS = [
    ("metrics_by_year.csv", "vpip_pct"),
    ("metrics_by_year.csv", "pfr_pct"),
    ("metrics_by_year.csv", "three_bet_pct"),
    ("metrics_by_year.csv", "cbet_pct"),
    ("metrics_by_year.csv", "bb100"),
    ("metrics_preflop.csv", "fold_to_3bet_pct"),
    ("metrics_preflop.csv", "four_bet_pct"),
]


def _hand_weighted_mean(series: pd.Series, weights: pd.Series) -> float:
    mask = series.notna() & weights.notna() & (weights > 0)
    if not mask.any():
        return float("nan")
    return float(np.average(series[mask], weights=weights[mask]))


def assign_cohort(years_active: int, min_survivor_years: int) -> str:
    if years_active >= min_survivor_years:
        return f"survivor_{min_survivor_years}plus"
    if years_active == 1:
        return "one_year"
    if years_active == 2:
        return "two_year"
    return f"active_{years_active}y"


def build_player_panel(player_year: pd.DataFrame, min_survivor_years: int) -> pd.DataFrame:
    """One row per dataset × player with career span and mean style metrics."""
    agg = {
        "year": ["nunique", "min", "max"],
        "hands": "sum",
    }
    for col in STYLE_COLS:
        if col in player_year.columns:
            agg[col] = "mean"

    g = player_year.groupby(["dataset", "player_name"], as_index=False).agg(agg)
    # Flatten MultiIndex columns from agg
    flat = []
    for a, b in g.columns:
        if b == "" or b is None:
            flat.append(a)
        elif a == "year" and b == "nunique":
            flat.append("years_active")
        elif a == "year" and b == "min":
            flat.append("first_year")
        elif a == "year" and b == "max":
            flat.append("last_year")
        elif a == "hands":
            flat.append("hands_total")
        else:
            flat.append(a)
    g.columns = flat
    g["span_years"] = g["last_year"] - g["first_year"] + 1
    g["cohort"] = g["years_active"].map(
        lambda y: assign_cohort(int(y), min_survivor_years)
    )
    return g


def cohort_summary(panel: pd.DataFrame) -> pd.DataFrame:
    """Mean / median / hand-weighted style metrics by dataset × cohort."""
    rows = []
    for (dataset, cohort), sub in panel.groupby(["dataset", "cohort"], sort=True):
        row = {
            "dataset": dataset,
            "cohort": cohort,
            "n_players": int(len(sub)),
            "hands_total": int(sub["hands_total"].sum()),
            "mean_years_active": float(sub["years_active"].mean()),
            "mean_first_year": float(sub["first_year"].mean()),
            "mean_last_year": float(sub["last_year"].mean()),
        }
        for col in STYLE_COLS:
            if col not in sub.columns:
                continue
            row[f"mean_{col}"] = float(sub[col].mean())
            row[f"median_{col}"] = float(sub[col].median())
            row[f"hw_mean_{col}"] = _hand_weighted_mean(sub[col], sub["hands_total"])
        rows.append(row)
    return pd.DataFrame(rows)


def survivorship_by_era(
    player_year: pd.DataFrame, min_survivor_years: int
) -> pd.DataFrame:
    """Compare survivors vs short-timers using only player-years inside each era.

    Cohort membership still uses career years_active across the full study window
    (survivors who played in an era vs one-year players observed in that era).
    """
    career = (
        player_year.groupby(["dataset", "player_name"], as_index=False)
        .agg(years_active=("year", "nunique"), first_year=("year", "min"), last_year=("year", "max"))
    )
    career["cohort"] = career["years_active"].map(
        lambda y: assign_cohort(int(y), min_survivor_years)
    )

    merged = player_year.merge(
        career[["dataset", "player_name", "cohort", "years_active"]],
        on=["dataset", "player_name"],
        how="left",
    )

    rows = []
    for era_label, y0, y1 in ERAS:
        era_rows = merged[(merged["year"] >= y0) & (merged["year"] <= y1)]
        # Player means within the era, then cohort means
        player_era = (
            era_rows.groupby(["dataset", "player_name", "cohort", "years_active"], as_index=False)
            .agg(
                hands=("hands", "sum"),
                **{c: (c, "mean") for c in STYLE_COLS if c in era_rows.columns},
                n_years_in_era=("year", "nunique"),
            )
        )
        for (dataset, cohort), sub in player_era.groupby(["dataset", "cohort"], sort=True):
            row = {
                "era": era_label,
                "era_start": y0,
                "era_end": y1,
                "dataset": dataset,
                "cohort": cohort,
                "n_players": int(len(sub)),
                "hands_total": int(sub["hands"].sum()),
                "mean_years_active_career": float(sub["years_active"].mean()),
                "mean_years_in_era": float(sub["n_years_in_era"].mean()),
            }
            for col in STYLE_COLS:
                if col not in sub.columns:
                    continue
                row[f"mean_{col}"] = float(sub[col].mean())
                row[f"median_{col}"] = float(sub[col].median())
                row[f"hw_mean_{col}"] = _hand_weighted_mean(sub[col], sub["hands"])
            rows.append(row)
    return pd.DataFrame(rows)


def bb100_dispersion(player_year: pd.DataFrame, min_hands: int) -> pd.DataFrame:
    """Dispersion of player-year bb/100 within dataset × year."""
    if "bb100" not in player_year.columns:
        return pd.DataFrame()
    df = player_year[player_year["hands"] >= min_hands].copy()
    rows = []
    for (dataset, year), sub in df.groupby(["dataset", "year"], sort=True):
        s = sub["bb100"].dropna()
        if s.empty:
            continue
        rows.append(
            {
                "dataset": dataset,
                "year": int(year),
                "n_players": int(len(s)),
                "min_hands": min_hands,
                "median_hands": float(sub["hands"].median()),
                "mean_bb100": float(s.mean()),
                "median_bb100": float(s.median()),
                "std_bb100": float(s.std(ddof=1)) if len(s) > 1 else float("nan"),
                "p25_bb100": float(s.quantile(0.25)),
                "p75_bb100": float(s.quantile(0.75)),
                "iqr_bb100": float(s.quantile(0.75) - s.quantile(0.25)),
                "hw_mean_bb100": _hand_weighted_mean(sub["bb100"], sub["hands"]),
            }
        )
    return pd.DataFrame(rows)


def two_piece_sse(y: np.ndarray, x: np.ndarray, break_idx: int) -> float:
    """SSE of two independent OLS fits split after break_idx (0-based on sorted x)."""
    # Left: indices 0..break_idx inclusive; right: break_idx..end (share the break year)
    # Require at least 3 points each side for a stable fit.
    left_x, left_y = x[: break_idx + 1], y[: break_idx + 1]
    right_x, right_y = x[break_idx:], y[break_idx:]
    if len(left_x) < 3 or len(right_x) < 3:
        return float("inf")
    sse = 0.0
    for xx, yy in ((left_x, left_y), (right_x, right_y)):
        model = LinearRegression()
        model.fit(xx.reshape(-1, 1), yy)
        pred = model.predict(xx.reshape(-1, 1))
        sse += float(np.sum((yy - pred) ** 2))
    return sse


def single_piece_fit(y: np.ndarray, x: np.ndarray) -> tuple[float, float, float]:
    model = LinearRegression()
    model.fit(x.reshape(-1, 1), y)
    pred = model.predict(x.reshape(-1, 1))
    sse = float(np.sum((y - pred) ** 2))
    return float(model.coef_[0]), float(model.intercept_), sse


def piece_slopes(y: np.ndarray, x: np.ndarray, break_idx: int) -> tuple[float, float]:
    left_x, left_y = x[: break_idx + 1], y[: break_idx + 1]
    right_x, right_y = x[break_idx:], y[break_idx:]
    m1 = LinearRegression().fit(left_x.reshape(-1, 1), left_y)
    m2 = LinearRegression().fit(right_x.reshape(-1, 1), right_y)
    return float(m1.coef_[0]), float(m2.coef_[0])


def find_breakpoints(reports_dir: Path) -> pd.DataFrame:
    """Grid-search two-piece OLS break years on population yearly metrics."""
    cache: dict[str, pd.DataFrame] = {}
    rows = []
    for fname, metric in BREAKPOINT_METRICS:
        if fname not in cache:
            path = reports_dir / fname
            if not path.exists():
                continue
            cache[fname] = pd.read_csv(path)
        df = cache[fname]
        if metric not in df.columns or "year" not in df.columns:
            continue
        for dataset, sub in df.groupby("dataset", sort=True):
            sub = sub.sort_values("year")
            years = sub["year"].to_numpy(dtype=float)
            vals = sub[metric].to_numpy(dtype=float)
            if len(years) < 6 or np.any(~np.isfinite(vals)):
                continue
            slope1, intercept, sse1 = single_piece_fit(vals, years)
            best_sse = float("inf")
            best_year = None
            best_slopes = (float("nan"), float("nan"))
            # Candidate breaks: interior years with ≥3 pts each side
            for i in range(2, len(years) - 2):
                sse = two_piece_sse(vals, years, i)
                if sse < best_sse:
                    best_sse = sse
                    best_year = int(years[i])
                    best_slopes = piece_slopes(vals, years, i)
            improvement = (sse1 - best_sse) / sse1 if sse1 > 0 else float("nan")
            rows.append(
                {
                    "source_file": fname,
                    "dataset": dataset,
                    "metric": metric,
                    "n_years": int(len(years)),
                    "year_min": int(years.min()),
                    "year_max": int(years.max()),
                    "single_slope_per_year": slope1,
                    "single_sse": sse1,
                    "best_break_year": best_year,
                    "left_slope_per_year": best_slopes[0],
                    "right_slope_per_year": best_slopes[1],
                    "two_piece_sse": best_sse,
                    "sse_improvement_frac": improvement,
                    "note": "Association only; not causal proof of external events.",
                }
            )
    return pd.DataFrame(rows)


def maybe_plot_figures(
    summary: pd.DataFrame,
    by_era: pd.DataFrame,
    out_dir: Path,
    min_survivor_years: int,
) -> list[Path]:
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    survivor = f"survivor_{min_survivor_years}plus"
    focus = summary[summary["cohort"].isin(["one_year", survivor])].copy()
    metrics_plot = [
        ("mean_vpip_pct", "VPIP %"),
        ("mean_pfr_pct", "PFR %"),
        ("mean_three_bet_pct", "3-bet %"),
        ("mean_fold_to_3bet_pct", "Fold to 3-bet %"),
        ("mean_cbet_pct", "C-bet %"),
        ("mean_bb_fold_pct", "BB fold %"),
    ]

    # Style comparison bars
    fig, axes = plt.subplots(2, 3, figsize=(11, 6.5), constrained_layout=True)
    datasets = ["NL50", "NL200", "NL5K"]
    x = np.arange(len(datasets))
    width = 0.35
    for ax, (col, label) in zip(axes.ravel(), metrics_plot):
        one = [
            focus[(focus.dataset == d) & (focus.cohort == "one_year")][col].mean()
            for d in datasets
        ]
        surv = [
            focus[(focus.dataset == d) & (focus.cohort == survivor)][col].mean()
            for d in datasets
        ]
        ax.bar(x - width / 2, one, width, label="one-year", color="#4a6fa5")
        ax.bar(x + width / 2, surv, width, label="survivor 3+", color="#c47b2b")
        ax.set_xticks(x)
        ax.set_xticklabels(datasets)
        ax.set_title(label)
        ax.grid(axis="y", alpha=0.3)
    axes[0, 0].legend(frameon=False, fontsize=8)
    fig.suptitle("RQ5: Style means — one-year vs multi-year survivors", fontsize=12)
    path = out_dir / "survivorship_style_means.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    written.append(path)

    # Era × VPIP for NL200 (largest mid-stakes sample)
    era_nl200 = by_era[
        (by_era["dataset"] == "NL200")
        & (by_era["cohort"].isin(["one_year", survivor]))
    ]
    if not era_nl200.empty:
        fig, ax = plt.subplots(figsize=(7.5, 4.2), constrained_layout=True)
        eras = [e[0] for e in ERAS]
        x = np.arange(len(eras))
        one = [
            era_nl200[(era_nl200.era == e) & (era_nl200.cohort == "one_year")][
                "mean_vpip_pct"
            ].mean()
            for e in eras
        ]
        surv = [
            era_nl200[(era_nl200.era == e) & (era_nl200.cohort == survivor)][
                "mean_vpip_pct"
            ].mean()
            for e in eras
        ]
        ax.plot(x, one, marker="o", label="one-year", color="#4a6fa5")
        ax.plot(x, surv, marker="s", label="survivor 3+", color="#c47b2b")
        ax.set_xticks(x)
        ax.set_xticklabels(eras)
        ax.set_ylabel("Mean VPIP %")
        ax.set_title("NL200 VPIP by era — survivors vs one-year players")
        ax.legend(frameon=False)
        ax.grid(alpha=0.3)
        path = out_dir / "survivorship_nl200_vpip_by_era.png"
        fig.savefig(path, dpi=140)
        plt.close(fig)
        written.append(path)

    return written


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="RQ5 survivorship + RQ4 bb100 dispersion + RQ6 breakpoints (CSV-only)."
    )
    p.add_argument(
        "--reports-dir",
        type=Path,
        default=DEFAULT_REPORTS,
        help="Directory with metrics_*.csv (default: reports/)",
    )
    p.add_argument(
        "--min-survivor-years",
        type=int,
        default=3,
        help="Minimum distinct years to count as survivor (default: 3)",
    )
    p.add_argument(
        "--min-hands",
        type=int,
        default=500,
        help="Min hands for bb100 dispersion rows (default: 500; player_year already filtered)",
    )
    p.add_argument(
        "--figures",
        action="store_true",
        help="Write small matplotlib figures under reports/survivorship_figures/",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    reports = args.reports_dir
    player_year_path = reports / "metrics_player_year.csv"
    if not player_year_path.exists():
        print(f"Missing {player_year_path}", file=sys.stderr)
        return 1

    print(f"Loading {player_year_path} ...")
    player_year = pd.read_csv(player_year_path)
    print(f"  {len(player_year):,} player-year rows")

    panel = build_player_panel(player_year, args.min_survivor_years)
    summary = cohort_summary(panel)
    by_era = survivorship_by_era(player_year, args.min_survivor_years)
    disp = bb100_dispersion(player_year, args.min_hands)
    breaks = find_breakpoints(reports)

    out_summary = reports / "metrics_survivorship_summary.csv"
    out_era = reports / "metrics_survivorship_by_era.csv"
    out_disp = reports / "metrics_bb100_dispersion.csv"
    out_breaks = reports / "rq6_breakpoints.csv"

    summary.to_csv(out_summary, index=False)
    by_era.to_csv(out_era, index=False)
    disp.to_csv(out_disp, index=False)
    breaks.to_csv(out_breaks, index=False)

    print(f"Wrote {out_summary} ({len(summary)} rows)")
    print(f"Wrote {out_era} ({len(by_era)} rows)")
    print(f"Wrote {out_disp} ({len(disp)} rows)")
    print(f"Wrote {out_breaks} ({len(breaks)} rows)")

    # Compact console contrast: survivor vs one_year
    survivor = f"survivor_{args.min_survivor_years}plus"
    contrast = summary[summary["cohort"].isin(["one_year", survivor])].copy()
    cols = [
        "dataset",
        "cohort",
        "n_players",
        "mean_vpip_pct",
        "mean_pfr_pct",
        "mean_three_bet_pct",
        "mean_fold_to_3bet_pct",
        "mean_cbet_pct",
        "mean_bb_fold_pct",
        "median_bb100",
        "hw_mean_bb100",
    ]
    cols = [c for c in cols if c in contrast.columns]
    print("\nPrimary contrast (unweighted player means; median/hw for bb100):")
    print(contrast[cols].to_string(index=False, float_format=lambda v: f"{v:.2f}"))

    if args.figures:
        fig_dir = reports / "survivorship_figures"
        paths = maybe_plot_figures(summary, by_era, fig_dir, args.min_survivor_years)
        for p in paths:
            print(f"Wrote figure {p}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
