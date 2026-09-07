"""Measure how often named players share a cash table (co-occurrence).

This is a descriptive scan of the existing Parquet — no re-parse, no actions
table. Each primary 6-max hand has C(6,2)=15 unordered pairs; we count how
often each pair actually sits together, then how concentrated that is.

Layer 1 — seated roster:  (hand_id, player_name, table, time, net_won)
Layer 2 — pair expand:    self-join on hand_id with player_a < player_b
Layer 3 — aggregate:      shared hands / days / sessions, then merge years
Layer 4 — export:         filtered pair list, per-player top partner, summary

`net_bb_*_in_shared` is each player's own result in those hands, NOT a
chip-dump from A to B (pots have up to six players). Use it only as a
later ranking hint.

Usage (run NL200 first — best mix of volume and named regs):

  python scripts/compute_cooccurrence.py --path data/parsed --dataset NL200
  python scripts/compute_cooccurrence.py --path data/parsed --dataset NL200 --year 2015
  python scripts/compute_cooccurrence.py --path data/parsed --clear-checkpoint
"""

from __future__ import annotations

import argparse
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
    print_table,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = PROJECT_ROOT / "data" / "parsed"
REPORTS_DIR = PROJECT_ROOT / "reports"
CHECKPOINT_ROOT = PROJECT_ROOT / ".tmp" / "cooccurrence_checkpoint"


def setup_seated_views(con: duckdb.DuckDBPyConnection, hands_g: str, players_g: str) -> None:
    """Hands + players only. Skip setup_views() — we do not need actions."""
    con.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW primary_hands AS
        SELECT hand_id, dataset, year, table_name, timestamp, bb
        FROM read_parquet('{hands_g}')
        WHERE is_primary;

        CREATE OR REPLACE TEMP VIEW seated AS
        SELECT
            h.dataset,
            h.year,
            h.hand_id,
            h.table_name,
            h.timestamp,
            h.bb,
            p.player_name,
            p.net_won
        FROM read_parquet('{players_g}') p
        JOIN primary_hands h ON h.hand_id = p.hand_id
        WHERE p.position IS NOT NULL;
        """
    )


def sql_pairs(min_shared_slice: int) -> str:
    return f"""
        WITH pairs AS (
            SELECT
                a.dataset,
                a.year,
                a.table_name,
                a.timestamp,
                a.bb,
                a.player_name AS player_a,
                b.player_name AS player_b,
                a.net_won AS net_won_a,
                b.net_won AS net_won_b
            FROM seated a
            JOIN seated b
              ON a.hand_id = b.hand_id
             AND a.player_name < b.player_name
        )
        SELECT
            dataset,
            year,
            player_a,
            player_b,
            COUNT(*) AS shared_hands,
            COUNT(DISTINCT CAST(timestamp AS DATE)) AS shared_days,
            COUNT(DISTINCT table_name || '|' || CAST(CAST(timestamp AS DATE) AS VARCHAR))
                AS shared_sessions,
            MIN(timestamp) AS first_seen,
            MAX(timestamp) AS last_seen,
            ROUND(SUM(net_won_a / NULLIF(bb, 0)), 2) AS net_bb_a_in_shared,
            ROUND(SUM(net_won_b / NULLIF(bb, 0)), 2) AS net_bb_b_in_shared
        FROM pairs
        GROUP BY dataset, year, player_a, player_b
        HAVING COUNT(*) >= {int(min_shared_slice)}
    """


SQL_VOLUME = """
    SELECT dataset, year, player_name, COUNT(*) AS hands
    FROM seated
    GROUP BY dataset, year, player_name
"""


def checkpoint_dir(root: Path) -> Path:
    return CHECKPOINT_ROOT / root.name


def pairs_ckpt(ckpt: Path, dataset: str, year: str) -> Path:
    return ckpt / "pairs" / f"dataset={dataset}" / f"year={year}" / "part.parquet"


def volume_ckpt(ckpt: Path, dataset: str, year: str) -> Path:
    return ckpt / "volume" / f"dataset={dataset}" / f"year={year}" / "part.parquet"


def _copy_parquet(con: duckdb.DuckDBPyConnection, sql: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    con.execute(f"COPY ({sql}) TO '{dest.as_posix()}' (FORMAT PARQUET)")


def _run_slice(
    root: Path,
    hands_g: str,
    players_g: str,
    pairs_path: Path,
    volume_path: Path,
    min_shared_slice: int,
) -> tuple[int, int]:
    con = duckdb.connect()
    configure_duckdb(con, root)
    setup_seated_views(con, hands_g, players_g)
    _copy_parquet(con, sql_pairs(min_shared_slice), pairs_path)
    _copy_parquet(con, SQL_VOLUME, volume_path)
    n_pairs = int(con.execute(f"SELECT COUNT(*) FROM read_parquet('{pairs_path.as_posix()}')").fetchone()[0])
    n_players = int(con.execute(f"SELECT COUNT(*) FROM read_parquet('{volume_path.as_posix()}')").fetchone()[0])
    con.close()
    clear_duckdb_temp()
    return n_pairs, n_players


def _parquet_glob(files: list[Path]) -> str:
    if not files:
        raise FileNotFoundError("no checkpoint parquet files to merge")
    if len(files) == 1:
        return f"'{files[0].as_posix()}'"
    inner = ", ".join(f"'{p.as_posix()}'" for p in files)
    return f"[{inner}]"


def _collect(ckpt: Path, kind: str, datasets: tuple[str, ...], years: set[str] | None) -> list[Path]:
    out: list[Path] = []
    root = ckpt / kind
    if not root.is_dir():
        return out
    for ds in datasets:
        ds_dir = root / f"dataset={ds}"
        if not ds_dir.is_dir():
            continue
        for year_dir in sorted(ds_dir.iterdir()):
            if not year_dir.is_dir() or not year_dir.name.startswith("year="):
                continue
            year = year_dir.name.replace("year=", "")
            if years is not None and year not in years:
                continue
            part = year_dir / "part.parquet"
            if part.exists():
                out.append(part)
    return out


def _build_outputs(
    con: duckdb.DuckDBPyConnection,
    pair_files: list[Path],
    volume_files: list[Path],
    min_shared: int,
    min_player_hands: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    pairs_src = _parquet_glob(pair_files)
    vol_src = _parquet_glob(volume_files)

    con.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW pair_years AS
        SELECT * FROM read_parquet({pairs_src});

        CREATE OR REPLACE TEMP VIEW pairs_all AS
        SELECT
            dataset,
            player_a,
            player_b,
            SUM(shared_hands) AS shared_hands,
            SUM(shared_days) AS shared_days,
            SUM(shared_sessions) AS shared_sessions,
            COUNT(DISTINCT year) AS years_together,
            MIN(first_seen) AS first_seen,
            MAX(last_seen) AS last_seen,
            SUM(net_bb_a_in_shared) AS net_bb_a_in_shared,
            SUM(net_bb_b_in_shared) AS net_bb_b_in_shared
        FROM pair_years
        GROUP BY dataset, player_a, player_b;

        CREATE OR REPLACE TEMP VIEW player_volume AS
        SELECT dataset, player_name, SUM(hands) AS hands
        FROM read_parquet({vol_src})
        GROUP BY dataset, player_name;

        CREATE OR REPLACE TEMP VIEW pairs_long AS
        SELECT dataset, player_a AS player_name, player_b AS partner, shared_hands
        FROM pairs_all
        UNION ALL
        SELECT dataset, player_b, player_a, shared_hands
        FROM pairs_all;
        """
    )

    pairs = con.execute(
        f"""
        SELECT
            p.dataset,
            p.player_a,
            p.player_b,
            CAST(p.shared_hands AS BIGINT) AS shared_hands,
            CAST(p.shared_days AS BIGINT) AS shared_days,
            CAST(p.shared_sessions AS BIGINT) AS shared_sessions,
            CAST(p.years_together AS BIGINT) AS years_together,
            p.first_seen,
            p.last_seen,
            CAST(va.hands AS BIGINT) AS hands_a,
            CAST(vb.hands AS BIGINT) AS hands_b,
            ROUND(100.0 * p.shared_hands / NULLIF(va.hands, 0), 2) AS share_of_a_pct,
            ROUND(100.0 * p.shared_hands / NULLIF(vb.hands, 0), 2) AS share_of_b_pct,
            ROUND(p.net_bb_a_in_shared, 2) AS net_bb_a_in_shared,
            ROUND(p.net_bb_b_in_shared, 2) AS net_bb_b_in_shared
        FROM pairs_all p
        LEFT JOIN player_volume va
          ON va.dataset = p.dataset AND va.player_name = p.player_a
        LEFT JOIN player_volume vb
          ON vb.dataset = p.dataset AND vb.player_name = p.player_b
        WHERE p.shared_hands >= {int(min_shared)}
        ORDER BY p.dataset, p.shared_hands DESC
        """
    ).df()

    players = con.execute(
        f"""
        WITH ranked AS (
            SELECT
                dataset,
                player_name,
                partner AS top_partner,
                shared_hands AS top_partner_hands,
                ROW_NUMBER() OVER (
                    PARTITION BY dataset, player_name
                    ORDER BY shared_hands DESC, partner
                ) AS rn
            FROM pairs_long
        ),
        opp AS (
            SELECT
                dataset,
                player_name,
                COUNT(*) AS unique_opponents,
                COUNT(*) FILTER (WHERE shared_hands >= {int(min_shared)}) AS unique_opponents_min_shared,
                COUNT(*) FILTER (WHERE shared_hands >= 500) AS n_partners_ge_500,
                COUNT(*) FILTER (WHERE shared_hands >= 1000) AS n_partners_ge_1000
            FROM pairs_long
            GROUP BY dataset, player_name
        )
        SELECT
            v.dataset,
            v.player_name,
            CAST(v.hands AS BIGINT) AS hands,
            CAST(COALESCE(o.unique_opponents, 0) AS BIGINT) AS unique_opponents,
            CAST(COALESCE(o.unique_opponents_min_shared, 0) AS BIGINT) AS unique_opponents_min_shared,
            r.top_partner,
            CAST(r.top_partner_hands AS BIGINT) AS top_partner_hands,
            ROUND(100.0 * r.top_partner_hands / NULLIF(v.hands, 0), 2) AS top_partner_share_pct,
            CAST(COALESCE(o.n_partners_ge_500, 0) AS BIGINT) AS n_partners_ge_500,
            CAST(COALESCE(o.n_partners_ge_1000, 0) AS BIGINT) AS n_partners_ge_1000
        FROM player_volume v
        LEFT JOIN opp o USING (dataset, player_name)
        LEFT JOIN ranked r
          ON r.dataset = v.dataset AND r.player_name = v.player_name AND r.rn = 1
        WHERE v.hands >= {int(min_player_hands)}
        ORDER BY v.dataset, r.top_partner_hands DESC NULLS LAST
        """
    ).df()

    summary = con.execute(
        f"""
        WITH n AS (
            SELECT
                dataset,
                COUNT(*) AS n_pairs_slice_min,
                SUM(shared_hands) AS pair_hand_rows,
                MAX(shared_hands) AS max_shared_hands,
                ROUND(quantile_cont(shared_hands, 0.50), 1) AS p50_shared,
                ROUND(quantile_cont(shared_hands, 0.90), 1) AS p90_shared,
                ROUND(quantile_cont(shared_hands, 0.99), 1) AS p99_shared,
                COUNT(*) FILTER (WHERE shared_hands >= 100) AS n_pairs_ge_100,
                COUNT(*) FILTER (WHERE shared_hands >= 200) AS n_pairs_ge_200,
                COUNT(*) FILTER (WHERE shared_hands >= 500) AS n_pairs_ge_500,
                COUNT(*) FILTER (WHERE shared_hands >= 1000) AS n_pairs_ge_1000,
                COUNT(*) FILTER (WHERE shared_hands >= 5000) AS n_pairs_ge_5000
            FROM pairs_all
            GROUP BY dataset
        ),
        ranked AS (
            SELECT
                dataset,
                shared_hands,
                ROW_NUMBER() OVER (PARTITION BY dataset ORDER BY shared_hands DESC) AS rn,
                COUNT(*) OVER (PARTITION BY dataset) AS n_pairs
            FROM pairs_all
        ),
        conc AS (
            SELECT
                dataset,
                ROUND(
                    100.0 * SUM(shared_hands) FILTER (WHERE rn <= 100)
                    / NULLIF(SUM(shared_hands), 0),
                    2
                ) AS top100_pair_hands_pct,
                ROUND(
                    100.0 * SUM(shared_hands) FILTER (
                        WHERE rn <= GREATEST(1, CAST(ROUND(n_pairs * 0.01) AS INTEGER))
                    ) / NULLIF(SUM(shared_hands), 0),
                    2
                ) AS top1pct_pairs_hands_pct
            FROM ranked
            GROUP BY dataset
        ),
        vol AS (
            SELECT
                dataset,
                SUM(hands) AS player_hands,
                COUNT(*) AS n_players,
                COUNT(*) FILTER (WHERE hands >= {int(min_player_hands)}) AS n_players_min_hands
            FROM player_volume
            GROUP BY dataset
        )
        SELECT
            n.dataset,
            CAST(ROUND(vol.player_hands / 6.0) AS BIGINT) AS primary_hands,
            vol.n_players,
            vol.n_players_min_hands,
            n.n_pairs_slice_min,
            n.n_pairs_ge_100,
            n.n_pairs_ge_200,
            n.n_pairs_ge_500,
            n.n_pairs_ge_1000,
            n.n_pairs_ge_5000,
            n.max_shared_hands,
            n.p50_shared,
            n.p90_shared,
            n.p99_shared,
            c.top100_pair_hands_pct,
            c.top1pct_pairs_hands_pct
        FROM n
        JOIN conc c USING (dataset)
        JOIN vol USING (dataset)
        ORDER BY n.dataset
        """
    ).df()

    return pairs, players, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Cash-game player table co-occurrence")
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--output-dir", type=Path, default=REPORTS_DIR)
    parser.add_argument(
        "--dataset",
        action="append",
        choices=list(DATASETS),
        help="Repeatable. Default: all three stakes. Start with NL200.",
    )
    parser.add_argument("--year", action="append", help="Optional year filter, e.g. --year 2015")
    parser.add_argument(
        "--min-shared-slice",
        type=int,
        default=5,
        help="Min shared hands in a single year to checkpoint a pair (default 5)",
    )
    parser.add_argument(
        "--min-shared",
        type=int,
        default=200,
        help="Min career shared hands to export a pair (default 200)",
    )
    parser.add_argument(
        "--min-player-hands",
        type=int,
        default=500,
        help="Min career hands to export a player row (default 500)",
    )
    parser.add_argument("--clear-checkpoint", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    root: Path = args.path
    out_dir: Path = args.output_dir
    datasets = tuple(args.dataset) if args.dataset else DATASETS
    year_filter = set(args.year) if args.year else None

    if not (root / "hands").exists():
        print(f"No parsed data at {root}")
        sys.exit(1)

    ckpt = checkpoint_dir(root)
    if args.clear_checkpoint and ckpt.exists():
        shutil.rmtree(ckpt)
        print(f"Cleared checkpoint: {ckpt}\n")

    print("=== Table co-occurrence ===")
    print(f"Path: {root.resolve()}")
    print(f"Datasets: {', '.join(datasets)}")
    print(f"Min shared/year (checkpoint): {args.min_shared_slice}")
    print(f"Min shared career (export):   {args.min_shared}")
    print(f"Min player hands (export):    {args.min_player_hands}\n")

    resume = not args.no_resume
    skipped = 0

    for ds in datasets:
        years = list_dataset_years(root, ds)
        if year_filter is not None:
            years = [y for y in years if y in year_filter]
        if not years:
            print(f"  Skip {ds} (no years)")
            continue
        for year in years:
            p_path = pairs_ckpt(ckpt, ds, year)
            v_path = volume_ckpt(ckpt, ds, year)
            if resume and p_path.exists() and v_path.exists():
                print(f"  {ds}/{year} (cached)", flush=True)
                skipped += 1
                continue
            print(f"  {ds}/{year}...", flush=True)
            n_pairs, n_players = _run_slice(
                root,
                parquet_glob_slice(root, "hands", ds, year),
                parquet_glob_slice(root, "players", ds, year),
                p_path,
                v_path,
                args.min_shared_slice,
            )
            print(f"    -> {n_pairs:,} pairs, {n_players:,} players", flush=True)

    if skipped:
        print(f"\n  Resumed {skipped} cached slice(s)")

    pair_files = _collect(ckpt, "pairs", datasets, year_filter)
    volume_files = _collect(ckpt, "volume", datasets, year_filter)
    if not pair_files or not volume_files:
        print("No checkpoint slices to merge. Check --dataset / --year / parsed path.")
        sys.exit(1)

    print("\nMerging slices...", flush=True)
    con = duckdb.connect()
    configure_duckdb(con, root)
    pairs, players, summary = _build_outputs(
        con, pair_files, volume_files, args.min_shared, args.min_player_hands
    )
    con.close()
    clear_duckdb_temp()

    out_dir.mkdir(parents=True, exist_ok=True)
    pair_path = out_dir / "cooccurrence_pairs.csv"
    player_path = out_dir / "cooccurrence_players.csv"
    summary_path = out_dir / "cooccurrence_summary.csv"
    pairs.to_csv(pair_path, index=False)
    players.to_csv(player_path, index=False)
    summary.to_csv(summary_path, index=False)

    print(f"\nWrote {pair_path} ({len(pairs):,} pairs, shared_hands>={args.min_shared})")
    print(f"Wrote {player_path} ({len(players):,} players, hands>={args.min_player_hands})")
    print(f"Wrote {summary_path} ({len(summary):,} rows)\n")

    if not summary.empty:
        cols = [
            "dataset",
            "primary_hands",
            "n_players",
            "n_pairs_ge_200",
            "n_pairs_ge_500",
            "n_pairs_ge_1000",
            "max_shared_hands",
            "p99_shared",
            "top100_pair_hands_pct",
        ]
        headers = cols
        rows = []
        for rec in summary.to_dict(orient="records"):
            rows.append(tuple(rec[c] for c in cols))
        print_table(rows, headers)

    print("\nDone.")
    print("Read: high n_pairs_ge_1000 and high top_partner_share_pct => sticky regs.")
    print("Next: only those pairs are worth a chip-dump / soft-play pass.")


if __name__ == "__main__":
    main()
