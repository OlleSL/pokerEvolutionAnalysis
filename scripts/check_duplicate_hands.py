"""Find duplicate Hand # IDs within and across datasets.

Memory-conscious: SQLite counts all IDs; detail stored only for duplicates.

Usage:
  python scripts/check_duplicate_hands.py
  python scripts/check_duplicate_hands.py --dataset NL50

Writes:
  reports/duplicate_hands.json
  reports/duplicate_hands.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASETS = ("NL50", "NL200", "NL5K")

HAND_HEADER = re.compile(
    r"PokerStars (?:Hand|Game) #(\d+):\s+Hold'em No Limit \(([^)]+)\)\s+-\s+(\d{4}/\d{2}/\d{2})",
)


def init_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE hand_counts (
            hand_id TEXT PRIMARY KEY,
            count INTEGER NOT NULL DEFAULT 0,
            first_file TEXT,
            first_dataset TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE duplicate_occurrences (
            hand_id TEXT NOT NULL,
            file_path TEXT NOT NULL,
            dataset TEXT NOT NULL,
            occurrence_num INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX idx_dup_hand_id ON duplicate_occurrences(hand_id)"
    )
    return conn


def record_hand(
    conn: sqlite3.Connection,
    hand_id: str,
    file_path: str,
    dataset: str,
) -> None:
    cur = conn.execute(
        "SELECT count, first_file, first_dataset FROM hand_counts WHERE hand_id = ?",
        (hand_id,),
    )
    row = cur.fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO hand_counts (hand_id, count, first_file, first_dataset) VALUES (?, 1, ?, ?)",
            (hand_id, file_path, dataset),
        )
        return

    new_count = row[0] + 1
    conn.execute(
        "UPDATE hand_counts SET count = ? WHERE hand_id = ?",
        (new_count, hand_id),
    )
    if new_count == 2:
        conn.execute(
            "INSERT INTO duplicate_occurrences (hand_id, file_path, dataset, occurrence_num) VALUES (?, ?, ?, 1)",
            (hand_id, row[1], row[2]),
        )
    if new_count >= 2:
        conn.execute(
            "INSERT INTO duplicate_occurrences (hand_id, file_path, dataset, occurrence_num) VALUES (?, ?, ?, ?)",
            (hand_id, file_path, dataset, new_count),
        )


def run(dataset: str | None = None) -> dict:
    datasets = [dataset] if dataset else list(DATASETS)
    files_processed = 0
    hands_scanned = 0
    start = time.time()

    db_path = PROJECT_ROOT / "reports" / ".duplicate_hands_tmp.db"
    if db_path.exists():
        db_path.unlink()
    conn = init_db(db_path)

    try:
        for ds in datasets:
            files = sorted((PROJECT_ROOT / ds).rglob("*.txt"))
            print(f"\n=== {ds}: {len(files):,} files ===", flush=True)
            batch: list[tuple[str, str, str]] = []
            for i, path in enumerate(files, 1):
                rel = str(path.relative_to(PROJECT_ROOT))
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    files_processed += 1
                    continue

                for hand_id, _, _ in HAND_HEADER.findall(text):
                    batch.append((hand_id, rel, ds))
                    hands_scanned += 1

                if len(batch) >= 50_000:
                    for hid, fp, d in batch:
                        record_hand(conn, hid, fp, d)
                    conn.commit()
                    batch.clear()

                files_processed += 1
                if i % 500 == 0 or i == len(files):
                    elapsed = time.time() - start
                    print(f"  {i:,}/{len(files):,} ({i/elapsed:.1f} files/s)", flush=True)

            for hid, fp, d in batch:
                record_hand(conn, hid, fp, d)
            conn.commit()
            batch.clear()

        dup_rows = conn.execute(
            """
            SELECT hand_id, count, first_file, first_dataset
            FROM hand_counts
            WHERE count > 1
            ORDER BY count DESC
            LIMIT 100
            """
        ).fetchall()

        total_dup_ids = conn.execute(
            "SELECT COUNT(*) FROM hand_counts WHERE count > 1"
        ).fetchone()[0]

        total_extra = conn.execute(
            "SELECT COALESCE(SUM(count - 1), 0) FROM hand_counts WHERE count > 1"
        ).fetchone()[0]

        within_dataset = 0
        cross_dataset = 0
        dup_by_dataset: dict[str, int] = defaultdict(int)

        all_dup_ids = conn.execute(
            "SELECT hand_id FROM hand_counts WHERE count > 1"
        ).fetchall()
        for (hand_id,) in all_dup_ids:
            ds_set = {
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT dataset FROM duplicate_occurrences WHERE hand_id = ?",
                    (hand_id,),
                ).fetchall()
            }
            if len(ds_set) > 1:
                cross_dataset += 1
            else:
                within_dataset += 1
            for ds in ds_set:
                dup_by_dataset[ds] += 1

        sample = []
        for hand_id, count, first_file, first_dataset in dup_rows:
            occ = conn.execute(
                """
                SELECT file_path, dataset, occurrence_num
                FROM duplicate_occurrences
                WHERE hand_id = ?
                ORDER BY occurrence_num
                LIMIT 5
                """,
                (hand_id,),
            ).fetchall()
            datasets_seen = sorted({o[1] for o in occ})
            sample.append(
                {
                    "hand_id": hand_id,
                    "count": count,
                    "first_file": first_file,
                    "first_dataset": first_dataset,
                    "datasets": datasets_seen,
                    "cross_dataset": len(datasets_seen) > 1,
                    "occurrences_sample": [
                        {"file": o[0], "dataset": o[1], "n": o[2]} for o in occ
                    ],
                }
            )

        summary = {
            "files_processed": files_processed,
            "hands_scanned": hands_scanned,
            "elapsed_minutes": round((time.time() - start) / 60, 1),
            "unique_hand_ids": conn.execute(
                "SELECT COUNT(*) FROM hand_counts"
            ).fetchone()[0],
            "duplicate_hand_ids": total_dup_ids,
            "duplicate_extra_occurrences": total_extra,
            "within_dataset_duplicates": within_dataset,
            "cross_dataset_duplicates": cross_dataset,
            "duplicate_ids_by_dataset_involved": dict(dup_by_dataset),
            "top_duplicates_sample": sample,
        }
    finally:
        conn.close()
        if db_path.exists():
            db_path.unlink()

    return summary


def write_outputs(summary: dict) -> None:
    reports = PROJECT_ROOT / "reports"
    reports.mkdir(exist_ok=True)
    json_path = reports / "duplicate_hands.json"
    csv_path = reports / "duplicate_hands.csv"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    csv_rows = [
        {
            "hand_id": d["hand_id"],
            "count": d["count"],
            "first_dataset": d["first_dataset"],
            "cross_dataset": d["cross_dataset"],
            "datasets": ";".join(d["datasets"]),
        }
        for d in summary["top_duplicates_sample"]
    ]
    if csv_rows:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "hand_id",
                    "count",
                    "first_dataset",
                    "cross_dataset",
                    "datasets",
                ],
            )
            w.writeheader()
            w.writerows(csv_rows)
    print(f"Wrote {json_path}")
    print(f"Wrote {csv_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=DATASETS)
    args = parser.parse_args()
    print("Checking duplicate hand IDs (full corpus, SQLite-backed)...", flush=True)
    summary = run(args.dataset)
    write_outputs(summary)
    print(f"\nHands scanned: {summary['hands_scanned']:,}")
    print(f"Duplicate hand IDs: {summary['duplicate_hand_ids']:,}")
    print(f"  Within dataset: {summary['within_dataset_duplicates']:,}")
    print(f"  Cross dataset: {summary['cross_dataset_duplicates']:,}")


if __name__ == "__main__":
    main()
