"""Validate sample parse output against audit expectations.

Usage:
  python scripts/validate_sample_parse.py
  python scripts/validate_sample_parse.py --path data/parsed/sample
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = PROJECT_ROOT / "data" / "parsed" / "sample"


def parquet_glob(root: Path, table: str) -> str:
    return str(root / table / "**" / "*.parquet").replace("\\", "/")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()
    root: Path = args.path

    if not (root / "hands").exists():
        print(f"No parsed data at {root}. Run: python scripts/parse_sample.py --max-files 5")
        return

    con = duckdb.connect()
    hands_glob = parquet_glob(root, "hands")
    players_glob = parquet_glob(root, "players")
    actions_glob = parquet_glob(root, "actions")

    print("=== Row counts ===")
    for label, glob in [("hands", hands_glob), ("players", players_glob), ("actions", actions_glob)]:
        n = con.execute("SELECT COUNT(*) FROM read_parquet(?)", [glob]).fetchone()[0]
        print(f"  {label}: {n:,}")

    print("\n=== Hands by dataset / year ===")
    rows = con.execute(
        """
        SELECT dataset, year,
               COUNT(*) AS total,
               SUM(CASE WHEN is_included THEN 1 ELSE 0 END) AS included,
               SUM(CASE WHEN is_primary THEN 1 ELSE 0 END) AS primary
        FROM read_parquet(?)
        GROUP BY 1, 2 ORDER BY 1, 2
        """,
        [hands_glob],
    ).fetchall()
    for r in rows:
        print(f"  {r[0]}/{r[1]}: {r[2]:,} parsed, {r[3]:,} included, {r[4]:,} primary")

    print("\n=== VPIP (included hands, metric_definitions.md rules) ===")
    vpip = con.execute(
        """
        WITH preflop_voluntary AS (
            SELECT DISTINCT a.hand_id, a.player_name
            FROM read_parquet(?) a
            JOIN read_parquet(?) h ON h.hand_id = a.hand_id
            WHERE h.is_included
              AND a.street = 'preflop'
              AND a.action_type IN ('call', 'raise', 'bet')
              AND a.action_type NOT IN ('post_sb', 'post_bb', 'post_ante')
        ),
        dealt AS (
            SELECT hand_id, COUNT(*) AS n
            FROM read_parquet(?)
            GROUP BY hand_id
        )
        SELECT h.dataset,
               COUNT(DISTINCT p.hand_id || '|' || p.player_name) AS player_hands,
               COUNT(DISTINCT CASE WHEN v.hand_id IS NOT NULL
                     THEN v.hand_id || '|' || v.player_name END) AS vpip_instances,
               ROUND(100.0 * COUNT(DISTINCT CASE WHEN v.hand_id IS NOT NULL
                     THEN v.hand_id || '|' || v.player_name END)
                     / NULLIF(COUNT(DISTINCT p.hand_id || '|' || p.player_name), 0), 2) AS vpip_pct
        FROM read_parquet(?) h
        JOIN read_parquet(?) p ON p.hand_id = h.hand_id
        LEFT JOIN preflop_voluntary v ON v.hand_id = p.hand_id AND v.player_name = p.player_name
        WHERE h.is_included
        GROUP BY h.dataset
        ORDER BY h.dataset
        """,
        [actions_glob, hands_glob, players_glob, hands_glob, players_glob],
    ).fetchall()
    for r in vpip:
        print(f"  {r[0]}: VPIP {r[3]}% ({r[2]:,}/{r[1]:,} player-hands)")

    summary_path = root / "parse_summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        print(f"\n=== Parse summary ({summary_path.name}) ===")
        for slice_name, stats in summary.get("slices", {}).items():
            print(f"  {slice_name}: {stats}")

    err_path = root / "parse_errors.jsonl"
    if err_path.exists():
        err_lines = err_path.read_text(encoding="utf-8").strip().splitlines()
        print(f"\n=== Parse errors: {len(err_lines)} lines in parse_errors.jsonl ===")

    print("\nDone. Spot-check a few hands manually against raw .txt if counts look reasonable.")


if __name__ == "__main__":
    main()
