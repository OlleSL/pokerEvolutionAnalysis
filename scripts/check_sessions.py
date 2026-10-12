"""Check whether players' sessions can be rebuilt from the parsed hands.

Every hand has a timestamp and a table name, so a player's hands can be put
in time order. Whether that order looks like real sessions depends on how
complete the corpus is: if only some tables were captured, a player's
timeline is full of holes.

Two views per dataset × year (included hands, any number dealt):

  Table view   — gap between consecutive hands on the same table. Complete
                 tables show gaps of ~0.5–2 minutes; sampled tables show
                 long and irregular gaps.
  Player view  — gap between a player's consecutive hands (any table), and
                 sessions rebuilt by splitting on gaps longer than
                 --gap-minutes. Real sessions have many hands, last tens of
                 minutes and often span several tables at once.

Outputs (reports/):
  sessions_table_gaps.csv   — dataset × year table-gap summary
  sessions_summary.csv      — dataset × year player-gap + session summary
                              (players with ≥ --min-hands that year)
  sessions_player_year.csv  — per player-year session stats (≥ --min-hands)

Usage:
  python scripts/check_sessions.py --path data/parsed
  python scripts/check_sessions.py --path data/parsed --dataset NL200 --year 2018
  python scripts/check_sessions.py --path data/parsed --gap-minutes 20
"""

from __future__ import annotations

import argparse
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
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = PROJECT_ROOT / "data" / "parsed"
REPORTS_DIR = PROJECT_ROOT / "reports"


def setup_views(con: duckdb.DuckDBPyConnection, hands_g: str, players_g: str) -> None:
    con.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW inc_hands AS
        SELECT hand_id, dataset, year, table_name, timestamp
        FROM read_parquet('{hands_g}')
        WHERE is_included AND timestamp IS NOT NULL;

        CREATE OR REPLACE TEMP VIEW seated AS
        SELECT h.dataset, h.year, h.hand_id, h.table_name, h.timestamp, p.player_name
        FROM read_parquet('{players_g}') p
        JOIN inc_hands h ON h.hand_id = p.hand_id;
        """
    )


SQL_TABLE_GAPS = """
    WITH g AS (
        SELECT
            dataset, year, table_name,
            epoch(timestamp) - epoch(LAG(timestamp) OVER (
                PARTITION BY table_name ORDER BY timestamp, hand_id
            )) AS gap_s
        FROM inc_hands
    )
    SELECT
        dataset, year,
        COUNT(*) AS hands,
        COUNT(DISTINCT table_name) AS tables,
        ROUND(quantile_cont(gap_s, 0.10), 1) AS gap_p10_s,
        ROUND(quantile_cont(gap_s, 0.50), 1) AS gap_p50_s,
        ROUND(quantile_cont(gap_s, 0.90), 1) AS gap_p90_s,
        ROUND(100.0 * AVG(CASE WHEN gap_s <= 120 THEN 1 ELSE 0 END), 2) AS pct_gaps_le_2min,
        ROUND(100.0 * AVG(CASE WHEN gap_s > 3600 THEN 1 ELSE 0 END), 2) AS pct_gaps_gt_1h
    FROM g
    WHERE gap_s IS NOT NULL
    GROUP BY dataset, year
"""


def sql_player_sessions(gap_s: int) -> str:
    return f"""
        CREATE OR REPLACE TEMP TABLE player_gaps AS
        WITH g AS (
            SELECT
                dataset, year, player_name, table_name, timestamp,
                epoch(timestamp) - epoch(LAG(timestamp) OVER (
                    PARTITION BY player_name ORDER BY timestamp, hand_id
                )) AS gap_s
            FROM seated
        )
        SELECT
            *,
            SUM(CASE WHEN gap_s IS NULL OR gap_s > {gap_s} THEN 1 ELSE 0 END) OVER (
                PARTITION BY player_name ORDER BY timestamp
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS session_no
        FROM g;

        CREATE OR REPLACE TEMP TABLE sessions AS
        SELECT
            dataset, year, player_name, session_no,
            COUNT(*) AS hands,
            (epoch(MAX(timestamp)) - epoch(MIN(timestamp))) / 60.0 AS minutes,
            COUNT(DISTINCT table_name) AS tables,
            HOUR(MIN(timestamp)) AS start_hour
        FROM player_gaps
        GROUP BY dataset, year, player_name, session_no;
    """


def sql_summary(min_hands: int) -> str:
    # Only players with real volume, so one-off visitors don't swamp the medians
    return f"""
    WITH regulars AS (
        SELECT player_name FROM sessions
        GROUP BY player_name HAVING SUM(hands) >= {int(min_hands)}
    ),
    gaps AS (
        SELECT
            dataset, year,
            COUNT(DISTINCT player_name) AS regulars,
            ROUND(quantile_cont(gap_s, 0.50), 1) AS player_gap_p50_s,
            ROUND(100.0 * AVG(CASE WHEN gap_s <= 120 THEN 1 ELSE 0 END), 2) AS pct_player_gaps_le_2min,
            ROUND(100.0 * AVG(CASE WHEN gap_s > 86400 THEN 1 ELSE 0 END), 2) AS pct_player_gaps_gt_1day
        FROM player_gaps
        WHERE gap_s IS NOT NULL AND player_name IN (SELECT player_name FROM regulars)
        GROUP BY dataset, year
    ),
    sess AS (
        SELECT
            dataset, year,
            COUNT(*) AS sessions,
            quantile_cont(hands, 0.50) AS session_hands_p50,
            ROUND(quantile_cont(minutes, 0.50), 1) AS session_minutes_p50,
            quantile_cont(tables, 0.50) AS session_tables_p50,
            ROUND(100.0 * AVG(CASE WHEN hands = 1 THEN 1 ELSE 0 END), 2) AS pct_single_hand_sessions
        FROM sessions
        WHERE player_name IN (SELECT player_name FROM regulars)
        GROUP BY dataset, year
    )
    SELECT * FROM gaps JOIN sess USING (dataset, year)
    """


def sql_player_year(min_hands: int) -> str:
    return f"""
        SELECT
            dataset, year, player_name,
            CAST(SUM(hands) AS BIGINT) AS hands,
            COUNT(*) AS sessions,
            ROUND(MEDIAN(hands), 1) AS session_hands_median,
            ROUND(MEDIAN(minutes), 1) AS session_minutes_median,
            ROUND(MAX(minutes), 1) AS session_minutes_max,
            ROUND(SUM(minutes) / 60.0, 1) AS hours_played,
            MAX(tables) AS max_tables,
            ROUND(100.0 * AVG(CASE WHEN hands = 1 THEN 1 ELSE 0 END), 2) AS pct_single_hand_sessions,
            ROUND(MEDIAN(start_hour), 1) AS start_hour_median
        FROM sessions
        GROUP BY dataset, year, player_name
        HAVING SUM(hands) >= {int(min_hands)}
    """


def run_slice(hands_g: str, players_g: str, gap_s: int, min_hands: int) -> tuple[pd.DataFrame, ...]:
    con = duckdb.connect()
    configure_duckdb(con)
    setup_views(con, hands_g, players_g)
    tables = con.execute(SQL_TABLE_GAPS).df()
    con.execute(sql_player_sessions(gap_s))
    summary = con.execute(sql_summary(min_hands)).df()
    player_year = con.execute(sql_player_year(min_hands)).df()
    con.close()
    clear_duckdb_temp()
    return tables, summary, player_year


def main() -> None:
    parser = argparse.ArgumentParser(description="Check whether player sessions can be rebuilt")
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--output-dir", type=Path, default=REPORTS_DIR)
    parser.add_argument("--dataset", choices=list(DATASETS))
    parser.add_argument("--year", help="Single year (e.g. 2018)")
    parser.add_argument("--gap-minutes", type=int, default=30,
                        help="A pause longer than this starts a new session (default 30)")
    parser.add_argument("--min-hands", type=int, default=500,
                        help="Min hands/year for a player to count in the summary and "
                             "sessions_player_year.csv (default 500)")
    args = parser.parse_args()
    root: Path = args.path

    if not (root / "hands").exists():
        print(f"No parsed data at {root}. Run parse_corpus.py first.")
        sys.exit(1)

    frames: dict[str, list[pd.DataFrame]] = {"tables": [], "summary": [], "player_year": []}
    datasets = [args.dataset] if args.dataset else list(DATASETS)
    print(f"=== Session check (new session after {args.gap_minutes} min pause) ===")
    for ds in datasets:
        years = [args.year] if args.year else list_dataset_years(root, ds)
        for year in years:
            print(f"  {ds}/{year}...", flush=True)
            tables, summary, player_year = run_slice(
                parquet_glob_slice(root, "hands", ds, year),
                parquet_glob_slice(root, "players", ds, year),
                args.gap_minutes * 60,
                args.min_hands,
            )
            frames["tables"].append(tables)
            frames["summary"].append(summary)
            frames["player_year"].append(player_year)

    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "sessions_table_gaps.csv": ("tables", ["dataset", "year"]),
        "sessions_summary.csv": ("summary", ["dataset", "year"]),
        "sessions_player_year.csv": ("player_year", ["dataset", "year", "player_name"]),
    }
    for filename, (key, order) in outputs.items():
        df = pd.concat(frames[key], ignore_index=True).sort_values(order)
        df.to_csv(out_dir / filename, index=False)
        print(f"Wrote {out_dir / filename} ({len(df):,} rows)")

    summary = pd.concat(frames["summary"], ignore_index=True)
    tables = pd.concat(frames["tables"], ignore_index=True)
    print("\nHow to read this:")
    print("  Table gap p50 ~30-120 s and most gaps <= 2 min  -> tables are captured hand by hand")
    print("  Player sessions with dozens of hands over 30+ min -> sessions can be rebuilt")
    print("  Mostly single-hand sessions / day-long gaps      -> hands are scattered samples\n")
    print(tables[["dataset", "year", "gap_p50_s", "pct_gaps_le_2min"]].to_string(index=False))
    print()
    print(summary[["dataset", "year", "session_hands_p50", "session_minutes_p50",
                   "session_tables_p50", "pct_single_hand_sessions"]].to_string(index=False))


if __name__ == "__main__":
    main()
