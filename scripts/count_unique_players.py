"""Count unique player names in included hands (USD, non-cap, 2011-2025).

Usage:
  python scripts/count_unique_players.py
  python scripts/count_unique_players.py --dataset NL50

Writes:
  reports/player_counts.json
  reports/player_counts.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASETS = ("NL50", "NL200", "NL5K")
STUDY_YEARS = tuple(str(y) for y in range(2011, 2026))

HAND_START = re.compile(r"^PokerStars (?:Hand|Game) #", re.MULTILINE)
HEADER = re.compile(
    r"PokerStars (?:Hand|Game) #(\d+):\s+Hold'em No Limit \(([^)]+)\)\s+-\s+(\d{4}/\d{2}/\d{2})",
)
SEAT = re.compile(r"^Seat \d+: (.+?) \(\$[\d.]+ in chips\)", re.MULTILINE)


@dataclass
class Bucket:
    unique_players: set[str] = field(default_factory=set)
    included_hands: int = 0
    player_hand_instances: int = 0

    def add_hand(self, players: list[str]) -> None:
        self.included_hands += 1
        self.player_hand_instances += len(players)
        self.unique_players.update(players)


def year_from_path(path: Path, dataset: str) -> str:
    rel = path.relative_to(PROJECT_ROOT / dataset)
    if rel.parts and rel.parts[0].isdigit() and len(rel.parts[0]) == 4:
        return rel.parts[0]
    return "unknown"


def is_included(stake: str, year: str) -> bool:
    s = stake.lower()
    is_usd = "usd" in s and "eur" not in s and "gbp" not in s
    is_cap = "cap" in s
    return is_usd and not is_cap and year in STUDY_YEARS


def count_file(path: Path, dataset: str) -> dict[str, Bucket]:
    file_year = year_from_path(path, dataset)
    out: dict[str, Bucket] = defaultdict(Bucket)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out

    starts = [m.start() for m in HAND_START.finditer(text)]
    if not starts:
        return out

    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        block = text[start:end]
        header_match = HEADER.search(block)
        if not header_match:
            continue
        _, stake, date = header_match.groups()
        hand_year = date.split("/")[0]
        if not is_included(stake.strip(), hand_year):
            continue
        hole_idx = block.find("*** HOLE CARDS ***")
        preflop = block[:hole_idx] if hole_idx >= 0 else block
        players = SEAT.findall(preflop)
        if not players:
            continue
        out[file_year].add_hand(players)

    return out


def merge_buckets(target: dict[str, Bucket], source: dict[str, Bucket]) -> None:
    for year, b in source.items():
        t = target.setdefault(year, Bucket())
        t.included_hands += b.included_hands
        t.player_hand_instances += b.player_hand_instances
        t.unique_players.update(b.unique_players)


def bucket_row(dataset: str, year: str, b: Bucket) -> dict:
    unique = len(b.unique_players)
    avg = round(b.player_hand_instances / unique, 2) if unique else 0.0
    return {
        "dataset": dataset,
        "year": year,
        "unique_players": unique,
        "included_hands": b.included_hands,
        "player_hand_instances": b.player_hand_instances,
        "avg_hands_per_player": avg,
    }


def run(dataset: str | None = None) -> dict:
    datasets = [dataset] if dataset else list(DATASETS)
    by_dataset_year: dict[str, dict[str, Bucket]] = {ds: {} for ds in datasets}
    files_processed = 0
    start = time.time()

    for ds in datasets:
        files = sorted((PROJECT_ROOT / ds).rglob("*.txt"))
        print(f"\n=== {ds}: {len(files):,} files ===", flush=True)
        for i, path in enumerate(files, 1):
            partial = count_file(path, ds)
            merge_buckets(by_dataset_year[ds], partial)
            files_processed += 1
            if i % 500 == 0 or i == len(files):
                elapsed = time.time() - start
                print(f"  {i:,}/{len(files):,} ({i/elapsed:.1f} files/s)", flush=True)

    rows = []
    totals = Bucket()
    by_dataset: dict[str, dict] = {}

    for ds in datasets:
        ds_bucket = Bucket()
        for year in sorted(by_dataset_year[ds]):
            b = by_dataset_year[ds][year]
            rows.append(bucket_row(ds, year, b))
            ds_bucket.included_hands += b.included_hands
            ds_bucket.player_hand_instances += b.player_hand_instances
            ds_bucket.unique_players.update(b.unique_players)

        ds_unique = len(ds_bucket.unique_players)
        by_dataset[ds] = {
            "unique_players": ds_unique,
            "included_hands": ds_bucket.included_hands,
            "player_hand_instances": ds_bucket.player_hand_instances,
            "avg_hands_per_player": round(
                ds_bucket.player_hand_instances / ds_unique, 2
            )
            if ds_unique
            else 0.0,
        }
        totals.included_hands += ds_bucket.included_hands
        totals.player_hand_instances += ds_bucket.player_hand_instances
        totals.unique_players.update(ds_bucket.unique_players)

    total_unique = len(totals.unique_players)
    summary = {
        "files_processed": files_processed,
        "elapsed_minutes": round((time.time() - start) / 60, 1),
        "inclusion_rules": {
            "currency": "USD only",
            "cap_games": "excluded",
            "study_window": "2011-2025",
        },
        "totals": {
            "unique_players": total_unique,
            "included_hands": totals.included_hands,
            "player_hand_instances": totals.player_hand_instances,
            "avg_hands_per_player": round(
                totals.player_hand_instances / total_unique, 2
            )
            if total_unique
            else 0.0,
        },
        "by_dataset": by_dataset,
        "by_dataset_year": rows,
    }
    return summary


def write_outputs(summary: dict) -> None:
    reports = PROJECT_ROOT / "reports"
    reports.mkdir(exist_ok=True)
    json_path = reports / "player_counts.json"
    csv_path = reports / "player_counts.csv"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    fields = [
        "dataset",
        "year",
        "unique_players",
        "included_hands",
        "player_hand_instances",
        "avg_hands_per_player",
    ]
    if summary["by_dataset_year"]:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(summary["by_dataset_year"])
    print(f"Wrote {json_path}")
    print(f"Wrote {csv_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=DATASETS)
    args = parser.parse_args()
    print(
        "Counting unique players in included hands (full corpus — expect several hours)...",
        flush=True,
    )
    summary = run(args.dataset)
    write_outputs(summary)
    t = summary["totals"]
    print(f"\nUnique players: {t['unique_players']:,}")
    print(f"Included hands: {t['included_hands']:,}")
    print(f"Avg hands per player: {t['avg_hands_per_player']}")


if __name__ == "__main__":
    main()
