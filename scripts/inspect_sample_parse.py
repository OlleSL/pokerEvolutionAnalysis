"""Export parsed sample hands to readable CSV/text for manual review.

Parquet is binary — use this script to inspect parsed output in Cursor/Excel.

Usage:
  python scripts/inspect_sample_parse.py
  python scripts/inspect_sample_parse.py --hand-id 128268505992
  python scripts/inspect_sample_parse.py --limit 20 --primary-only

Writes:
  data/parsed/sample/review/hands.csv
  data/parsed/sample/review/hand_<id>.txt   (when --hand-id set, or first --limit hands)
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT_ROOT / "data" / "parsed" / "sample"
REVIEW_DIR = DEFAULT_ROOT / "review"


def parquet_glob(root: Path, table: str) -> str:
    return str(root / table / "**" / "*.parquet").replace("\\", "/")


def export_hand_detail(con: duckdb.DuckDBPyConnection, root: Path, hand_id: str, out_dir: Path) -> None:
    hands_glob = parquet_glob(root, "hands")
    players_glob = parquet_glob(root, "players")
    actions_glob = parquet_glob(root, "actions")

    hand = con.execute(
        "SELECT * FROM read_parquet(?) WHERE hand_id = ?",
        [hands_glob, hand_id],
    ).df()
    players = con.execute(
        "SELECT * FROM read_parquet(?) WHERE hand_id = ? ORDER BY seat",
        [players_glob, hand_id],
    ).df()
    actions = con.execute(
        "SELECT action_index, street, player_name, action_type, amount, is_all_in, raw_line "
        "FROM read_parquet(?) WHERE hand_id = ? ORDER BY action_index",
        [actions_glob, hand_id],
    ).df()

    if hand.empty:
        print(f"Hand {hand_id} not found")
        return

    h = hand.iloc[0]
    lines = [
        f"Hand #{hand_id}",
        f"Source: {h['source_file']}",
        f"Stakes: {h['stakes_raw']}  (sb={h['sb']}, bb={h['bb']})",
        f"Time: {h['timestamp']}  Table: {h['table_name']}  Button seat: {h['button_seat']}",
        f"Dealt: {h['num_players_dealt']}  Included: {h['is_included']}  Primary: {h['is_primary']}  Ante: {h.get('is_ante', False)}",
        f"Board: flop={h['board_flop']} turn={h['board_turn']} river={h['board_river']}",
        f"Pot: {h['pot_total']}  Rake: {h['rake']}  Last street: {h['last_street']}",
        "",
        "Players:",
    ]
    for _, p in players.iterrows():
        lines.append(
            f"  Seat {p['seat']} {p['position']:>3} {p['player_name']:<20} "
            f"stack=${p['stack_start']:.2f} net=${p['net_won']:.2f} "
            f"cards={p['hole_card_1']} {p['hole_card_2']}"
        )
    lines.append("")
    lines.append("Actions:")
    for _, a in actions.iterrows():
        lines.append(f"  [{a['action_index']:2}] {a['street']:<7} {a['player_name']:<20} {a['raw_line']}")

    out_path = out_dir / f"hand_{hand_id}.txt"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--hand-id")
    parser.add_argument("--limit", type=int, default=10, help="Export this many hand detail .txt files")
    parser.add_argument("--primary-only", action="store_true")
    parser.add_argument("--csv-limit", type=int, default=500, help="Rows in hands.csv")
    args = parser.parse_args()

    root: Path = args.path
    if not (root / "hands").exists():
        print(f"No parsed data at {root}. Run parse_sample.py first.")
        return

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    hands_glob = parquet_glob(root, "hands")

    where = "WHERE is_primary" if args.primary_only else ""
    hands_df = con.execute(
        f"""
        SELECT hand_id, dataset, year, timestamp, stakes_raw, num_players_dealt,
               is_included, is_primary, pot_total, rake, last_street, source_file
        FROM read_parquet(?)
        {where}
        ORDER BY timestamp
        LIMIT ?
        """,
        [hands_glob, args.csv_limit],
    ).df()

    csv_path = REVIEW_DIR / "hands.csv"
    hands_df.to_csv(csv_path, index=False)
    print(f"Wrote {csv_path} ({len(hands_df)} rows) — open in Cursor or Excel")

    if args.hand_id:
        export_hand_detail(con, root, args.hand_id, REVIEW_DIR)
        return

    ids = hands_df["hand_id"].head(args.limit).tolist()
    for hid in ids:
        export_hand_detail(con, root, str(hid), REVIEW_DIR)

    print(f"\nOpen review files under: {REVIEW_DIR}")
    print("  hands.csv       — overview table")
    print("  hand_<id>.txt   — full player/action detail per hand")


if __name__ == "__main__":
    main()
