"""Export player-year panels and population dispersion for RQ3 / RQ4.

Outputs (dataset × year grain for summaries; player × dataset × year for panel):
  - metrics_player_year.csv       — per-player rates (min hands filter)
  - metrics_player_dispersion.csv — mean/SD/IQR of player rates by stake×year
  - metrics_player_stayers.csv    — multi-year players with YoY deltas (RQ4)

Usage:
  python scripts/compute_player_year_metrics.py --path data/parsed
  python scripts/compute_player_year_metrics.py --min-hands 500 --clear-checkpoint
"""

from __future__ import annotations

import argparse
import pickle
import shutil
import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_metrics import (  # noqa: E402
    DATASETS,
    clear_duckdb_temp,
    configure_duckdb,
    list_dataset_years,
    parquet_glob_slice,
    setup_views,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = PROJECT_ROOT / "data" / "parsed"
REPORTS_DIR = PROJECT_ROOT / "reports"
CHECKPOINT_ROOT = PROJECT_ROOT / ".tmp" / "player_year_checkpoint"

# Core player×year features for homogeneity + individual change
SQL_PLAYER_YEAR = """
WITH vpip AS (
    SELECT DISTINCT hand_id, player_name FROM preflop
    WHERE action_type IN ('call', 'raise', 'bet')
),
pfr AS (
    SELECT DISTINCT hand_id, player_name FROM preflop WHERE action_type = 'raise'
),
three_bet AS (
    SELECT pf.hand_id, pf.player_name, pf.action_type
    FROM player_first pf
    WHERE pf.raises_before = 1
      AND pf.player_name <> COALESCE((
          SELECT player_name FROM preflop_raises r
          WHERE r.hand_id = pf.hand_id AND r.raise_n = 1
      ), '')
),
opener AS (
    SELECT hand_id, player_name AS opener, action_index AS open_idx
    FROM preflop_raises WHERE raise_n = 1
),
three_bettor AS (
    SELECT hand_id, player_name AS three_bettor, action_index AS three_idx
    FROM preflop_raises WHERE raise_n = 2
),
opener_vs_3bet AS (
    SELECT o.hand_id, o.opener AS player_name, a.action_type AS response
    FROM opener o
    JOIN three_bettor t USING (hand_id)
    JOIN preflop a ON a.hand_id = o.hand_id AND a.player_name = o.opener
        AND a.action_index > t.three_idx
        AND a.action_index = (
            SELECT MIN(a2.action_index) FROM preflop a2
            WHERE a2.hand_id = o.hand_id AND a2.player_name = o.opener
              AND a2.action_index > t.three_idx
              AND a2.action_type NOT IN ('post_sb', 'post_bb', 'post_ante')
        )
),
any_opens AS (
    SELECT r.hand_id, r.action_index AS open_idx
    FROM preflop_raises r
    JOIN primary_players pp ON pp.hand_id = r.hand_id AND pp.player_name = r.player_name
    WHERE r.raise_n = 1 AND pp.position IN ('LJ', 'HJ', 'CO', 'BTN', 'SB')
),
bb_defense AS (
    SELECT o.hand_id, bb.player_name, a.action_type AS response
    FROM any_opens o
    JOIN primary_players bb ON bb.hand_id = o.hand_id AND bb.position = 'BB'
    JOIN preflop a ON a.hand_id = o.hand_id AND a.player_name = bb.player_name
        AND a.action_index > o.open_idx
        AND a.action_type NOT IN ('post_sb', 'post_bb', 'post_ante')
        AND a.action_index = (
            SELECT MIN(a2.action_index) FROM preflop a2
            WHERE a2.hand_id = o.hand_id AND a2.player_name = bb.player_name
              AND a2.action_index > o.open_idx
              AND a2.action_type NOT IN ('post_sb', 'post_bb', 'post_ante')
        )
),
cbet_opps AS (
    SELECT h.hand_id, p.pfa AS player_name
    FROM primary_hands h
    JOIN pfa p USING (hand_id)
    WHERE h.board_flop IS NOT NULL AND h.num_players_flop >= 2
),
cbet_hits AS (
    SELECT DISTINCT a.hand_id, a.player_name
    FROM primary_actions a
    JOIN pfa p ON p.hand_id = a.hand_id AND p.pfa = a.player_name
    WHERE a.street = 'flop' AND a.action_type = 'bet'
)
SELECT
    h.dataset,
    h.year,
    pp.player_name,
    COUNT(*) AS hands,
    COUNT(*) FILTER (WHERE v.hand_id IS NOT NULL) AS vpip_count,
    ROUND(100.0 * COUNT(*) FILTER (WHERE v.hand_id IS NOT NULL) / COUNT(*), 2) AS vpip_pct,
    COUNT(*) FILTER (WHERE f.hand_id IS NOT NULL) AS pfr_count,
    ROUND(100.0 * COUNT(*) FILTER (WHERE f.hand_id IS NOT NULL) / COUNT(*), 2) AS pfr_pct,
    COUNT(*) FILTER (WHERE tb.hand_id IS NOT NULL) AS three_bet_opps,
    COUNT(*) FILTER (WHERE tb.action_type = 'raise') AS three_bets,
    ROUND(100.0 * COUNT(*) FILTER (WHERE tb.action_type = 'raise')
          / NULLIF(COUNT(*) FILTER (WHERE tb.hand_id IS NOT NULL), 0), 2) AS three_bet_pct,
    COUNT(*) FILTER (WHERE ov.hand_id IS NOT NULL) AS fold_to_3bet_opps,
    COUNT(*) FILTER (WHERE ov.response = 'fold') AS fold_to_3bet_count,
    ROUND(100.0 * COUNT(*) FILTER (WHERE ov.response = 'fold')
          / NULLIF(COUNT(*) FILTER (WHERE ov.hand_id IS NOT NULL), 0), 2) AS fold_to_3bet_pct,
    COUNT(*) FILTER (WHERE bd.hand_id IS NOT NULL) AS bb_defense_opps,
    COUNT(*) FILTER (WHERE bd.response = 'fold') AS bb_fold_count,
    COUNT(*) FILTER (WHERE bd.response = 'call') AS bb_call_count,
    COUNT(*) FILTER (WHERE bd.response = 'raise') AS bb_three_bet_count,
    ROUND(100.0 * COUNT(*) FILTER (WHERE bd.response = 'fold')
          / NULLIF(COUNT(*) FILTER (WHERE bd.hand_id IS NOT NULL), 0), 2) AS bb_fold_pct,
    ROUND(100.0 * COUNT(*) FILTER (WHERE bd.response = 'call')
          / NULLIF(COUNT(*) FILTER (WHERE bd.hand_id IS NOT NULL), 0), 2) AS bb_call_pct,
    ROUND(100.0 * COUNT(*) FILTER (WHERE bd.response = 'raise')
          / NULLIF(COUNT(*) FILTER (WHERE bd.hand_id IS NOT NULL), 0), 2) AS bb_three_bet_pct,
    COUNT(*) FILTER (WHERE co.hand_id IS NOT NULL) AS cbet_opps,
    COUNT(*) FILTER (WHERE ch.hand_id IS NOT NULL) AS cbets,
    ROUND(100.0 * COUNT(*) FILTER (WHERE ch.hand_id IS NOT NULL)
          / NULLIF(COUNT(*) FILTER (WHERE co.hand_id IS NOT NULL), 0), 2) AS cbet_pct,
    ROUND(100.0 * SUM(pp.net_won / h.bb) / COUNT(*), 2) AS bb100
FROM primary_hands h
JOIN primary_players pp ON pp.hand_id = h.hand_id
LEFT JOIN vpip v ON v.hand_id = pp.hand_id AND v.player_name = pp.player_name
LEFT JOIN pfr f ON f.hand_id = pp.hand_id AND f.player_name = pp.player_name
LEFT JOIN three_bet tb ON tb.hand_id = pp.hand_id AND tb.player_name = pp.player_name
LEFT JOIN opener_vs_3bet ov ON ov.hand_id = pp.hand_id AND ov.player_name = pp.player_name
LEFT JOIN bb_defense bd ON bd.hand_id = pp.hand_id AND bd.player_name = pp.player_name
LEFT JOIN cbet_opps co ON co.hand_id = pp.hand_id AND co.player_name = pp.player_name
LEFT JOIN cbet_hits ch ON ch.hand_id = pp.hand_id AND ch.player_name = pp.player_name
GROUP BY h.dataset, h.year, pp.player_name
ORDER BY h.dataset, h.year, hands DESC
"""

RATE_COLS = (
    "vpip_pct",
    "pfr_pct",
    "three_bet_pct",
    "fold_to_3bet_pct",
    "bb_fold_pct",
    "bb_three_bet_pct",
    "cbet_pct",
)


def checkpoint_dir(root: Path) -> Path:
    return CHECKPOINT_ROOT / root.name


def slice_ckpt(ckpt: Path, dataset: str, year: str) -> Path:
    return ckpt / f"dataset={dataset}_year={year}.pkl"


def _run_player_year(root: Path, hands_g: str, players_g: str, actions_g: str) -> pd.DataFrame:
    con = duckdb.connect()
    configure_duckdb(con, root)
    setup_views(con, hands_g, players_g, actions_g)
    df = con.execute(SQL_PLAYER_YEAR).df()
    con.close()
    clear_duckdb_temp()
    return df


def dispersion_table(player_year: pd.DataFrame, min_hands: int) -> pd.DataFrame:
    """Population spread of player-level rates (RQ3 homogeneity)."""
    df = player_year[player_year["hands"] >= min_hands].copy()
    rows: list[dict] = []
    for (dataset, year), g in df.groupby(["dataset", "year"]):
        base = {
            "dataset": dataset,
            "year": int(year),
            "n_players": int(len(g)),
            "min_hands": min_hands,
            "median_hands": float(g["hands"].median()),
        }
        for col in RATE_COLS:
            s = g[col].dropna()
            if s.empty:
                continue
            rows.append({
                **base,
                "metric": col.replace("_pct", ""),
                "mean": round(float(s.mean()), 2),
                "std": round(float(s.std(ddof=1)), 2) if len(s) > 1 else 0.0,
                "p25": round(float(s.quantile(0.25)), 2),
                "p50": round(float(s.quantile(0.50)), 2),
                "p75": round(float(s.quantile(0.75)), 2),
                "iqr": round(float(s.quantile(0.75) - s.quantile(0.25)), 2),
            })
    return pd.DataFrame(rows).sort_values(["dataset", "year", "metric"])


def stayers_table(player_year: pd.DataFrame, min_hands: int, min_years: int) -> pd.DataFrame:
    """Multi-year players with YoY deltas on key rates (RQ4)."""
    df = player_year[player_year["hands"] >= min_hands].copy()
    df = df.sort_values(["dataset", "player_name", "year"])
    year_counts = df.groupby(["dataset", "player_name"])["year"].nunique()
    keep = year_counts[year_counts >= min_years].index
    df = df.set_index(["dataset", "player_name"]).loc[keep].reset_index()

    delta_cols = ["vpip_pct", "pfr_pct", "three_bet_pct", "bb_fold_pct", "cbet_pct", "bb100"]
    parts: list[pd.DataFrame] = []
    for (dataset, player), g in df.groupby(["dataset", "player_name"]):
        g = g.sort_values("year").copy()
        g["years_active"] = len(g)
        g["hands_total"] = g["hands"].sum()
        for col in delta_cols:
            g[f"d_{col}"] = g[col].diff()
        g["year_index"] = range(1, len(g) + 1)
        parts.append(g)
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True)
    # Compact export for Power BI
    keep_cols = [
        "dataset", "player_name", "year", "year_index", "years_active",
        "hands", "hands_total",
        "vpip_pct", "pfr_pct", "three_bet_pct", "fold_to_3bet_pct",
        "bb_fold_pct", "bb_call_pct", "bb_three_bet_pct", "cbet_pct", "bb100",
        "d_vpip_pct", "d_pfr_pct", "d_three_bet_pct", "d_bb_fold_pct", "d_cbet_pct", "d_bb100",
    ]
    return out[keep_cols].sort_values(["dataset", "player_name", "year"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Player-year metrics for RQ3/RQ4")
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--output-dir", type=Path, default=REPORTS_DIR)
    parser.add_argument("--min-hands", type=int, default=500,
                        help="Min hands/year to include in panel & dispersion (default 500)")
    parser.add_argument("--min-years", type=int, default=3,
                        help="Min distinct years for stayers export (default 3)")
    parser.add_argument("--clear-checkpoint", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    root: Path = args.path
    out_dir: Path = args.output_dir

    if not (root / "hands").exists():
        print(f"No parsed data at {root}")
        sys.exit(1)

    ckpt = checkpoint_dir(root)
    if args.clear_checkpoint and ckpt.exists():
        shutil.rmtree(ckpt)
        print(f"Cleared checkpoint: {ckpt}\n")

    frames: list[pd.DataFrame] = []
    resume = not args.no_resume
    skipped = 0

    print("=== Player-year metrics ===")
    print(f"Path: {root.resolve()}")
    print(f"Min hands/year (export filter): {args.min_hands}")
    print(f"Stayers min years: {args.min_years}\n")

    for ds in DATASETS:
        years = list_dataset_years(root, ds)
        if not years:
            print(f"  Skip {ds}")
            continue
        for year in years:
            path = slice_ckpt(ckpt, ds, year)
            if resume and path.exists():
                with path.open("rb") as f:
                    df = pickle.load(f)
                print(f"  {ds}/{year} (cached, {len(df):,} players)", flush=True)
                frames.append(df)
                skipped += 1
                continue
            print(f"  {ds}/{year}...", flush=True)
            df = _run_player_year(
                root,
                parquet_glob_slice(root, "hands", ds, year),
                parquet_glob_slice(root, "players", ds, year),
                parquet_glob_slice(root, "actions", ds, year),
            )
            ckpt.mkdir(parents=True, exist_ok=True)
            with path.open("wb") as f:
                pickle.dump(df, f)
            print(f"    -> {len(df):,} players", flush=True)
            frames.append(df)

    if skipped:
        print(f"\n  Resumed {skipped} cached slice(s)")

    all_py = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    filtered = all_py[all_py["hands"] >= args.min_hands].copy() if not all_py.empty else all_py

    out_dir.mkdir(parents=True, exist_ok=True)
    py_path = out_dir / "metrics_player_year.csv"
    filtered.sort_values(["dataset", "year", "hands"], ascending=[True, True, False]).to_csv(
        py_path, index=False
    )
    print(f"\nWrote {py_path} ({len(filtered):,} rows, min_hands>={args.min_hands})")

    disp = dispersion_table(all_py, args.min_hands)
    disp_path = out_dir / "metrics_player_dispersion.csv"
    disp.to_csv(disp_path, index=False)
    print(f"Wrote {disp_path} ({len(disp):,} rows)")

    stayers = stayers_table(all_py, args.min_hands, args.min_years)
    stay_path = out_dir / "metrics_player_stayers.csv"
    stayers.to_csv(stay_path, index=False)
    print(f"Wrote {stay_path} ({len(stayers):,} rows)")

    print("\nDone.")


if __name__ == "__main__":
    main()
