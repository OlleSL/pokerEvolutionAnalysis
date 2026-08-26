"""Count hands by players dealt (6-max tables), with inclusion filters.

Usage:
  python scripts/count_players_dealt.py
  python scripts/count_players_dealt.py --dataset NL50

Writes:
  reports/players_dealt_counts.json
  reports/players_dealt_counts.csv
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
SEAT = re.compile(r"^Seat \d+: .+ \(\$[\d.]+ in chips\)", re.MULTILINE)
TABLE_MAX = re.compile(r"Table '[^']+' (\d+)-max")


@dataclass
class Bucket:
    hands: int = 0
    hands_6_dealt: int = 0
    included: int = 0
    included_6_dealt: int = 0
    by_dealt_count: dict[int, int] = field(default_factory=lambda: defaultdict(int))

    def add(self, players_dealt: int, included: bool) -> None:
        self.hands += 1
        self.by_dealt_count[players_dealt] += 1
        if players_dealt == 6:
            self.hands_6_dealt += 1
        if included:
            self.included += 1
            if players_dealt == 6:
                self.included_6_dealt += 1


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
    year = year_from_path(path, dataset)
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
        hole_idx = block.find("*** HOLE CARDS ***")
        preflop = block[:hole_idx] if hole_idx >= 0 else block
        players_dealt = len(SEAT.findall(preflop))
        inc = is_included(stake.strip(), hand_year)
        out[year].add(players_dealt, inc)

    return out


def merge_buckets(target: dict[str, Bucket], source: dict[str, Bucket]) -> None:
    for year, b in source.items():
        t = target.setdefault(year, Bucket())
        t.hands += b.hands
        t.hands_6_dealt += b.hands_6_dealt
        t.included += b.included
        t.included_6_dealt += b.included_6_dealt
        for k, v in b.by_dealt_count.items():
            t.by_dealt_count[k] += v


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

    totals = Bucket()
    rows = []
    for ds in datasets:
        for year in sorted(by_dataset_year[ds]):
            b = by_dataset_year[ds][year]
            rows.append(
                {
                    "dataset": ds,
                    "year": year,
                    "hands": b.hands,
                    "hands_6_dealt": b.hands_6_dealt,
                    "pct_6_dealt": round(100 * b.hands_6_dealt / b.hands, 2) if b.hands else 0,
                    "included": b.included,
                    "included_6_dealt": b.included_6_dealt,
                    "pct_included_6": round(100 * b.included_6_dealt / b.included, 2) if b.included else 0,
                    "dealt_distribution": dict(sorted(b.by_dealt_count.items())),
                }
            )
            totals.hands += b.hands
            totals.hands_6_dealt += b.hands_6_dealt
            totals.included += b.included
            totals.included_6_dealt += b.included_6_dealt
            for k, v in b.by_dealt_count.items():
                totals.by_dealt_count[k] += v

    summary = {
        "files_processed": files_processed,
        "elapsed_minutes": round((time.time() - start) / 60, 1),
        "totals": {
            "hands": totals.hands,
            "hands_6_dealt": totals.hands_6_dealt,
            "pct_6_dealt": round(100 * totals.hands_6_dealt / totals.hands, 2) if totals.hands else 0,
            "included": totals.included,
            "included_6_dealt": totals.included_6_dealt,
            "pct_included_6": round(100 * totals.included_6_dealt / totals.included, 2) if totals.included else 0,
            "dealt_distribution": dict(sorted(totals.by_dealt_count.items())),
        },
        "by_dataset": {},
        "by_dataset_year": rows,
    }

    for ds in datasets:
        ds_b = Bucket()
        for year in by_dataset_year[ds]:
            b = by_dataset_year[ds][year]
            ds_b.hands += b.hands
            ds_b.hands_6_dealt += b.hands_6_dealt
            ds_b.included += b.included
            ds_b.included_6_dealt += b.included_6_dealt
        summary["by_dataset"][ds] = {
            "hands": ds_b.hands,
            "hands_6_dealt": ds_b.hands_6_dealt,
            "pct_6_dealt": round(100 * ds_b.hands_6_dealt / ds_b.hands, 2) if ds_b.hands else 0,
            "included": ds_b.included,
            "included_6_dealt": ds_b.included_6_dealt,
            "pct_included_6": round(100 * ds_b.included_6_dealt / ds_b.included, 2) if ds_b.included else 0,
        }

    return summary


def write_outputs(summary: dict) -> None:
    reports = PROJECT_ROOT / "reports"
    reports.mkdir(exist_ok=True)
    json_path = reports / "players_dealt_counts.json"
    csv_path = reports / "players_dealt_counts.csv"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if summary["by_dataset_year"]:
        fields = ["dataset", "year", "hands", "hands_6_dealt", "pct_6_dealt", "included", "included_6_dealt", "pct_included_6"]
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for row in summary["by_dataset_year"]:
                w.writerow({k: row[k] for k in fields})
    print(f"Wrote {json_path}")
    print(f"Wrote {csv_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=DATASETS)
    args = parser.parse_args()
    print("Counting players dealt per hand (full corpus — expect ~1.5–3 hours)...", flush=True)
    summary = run(args.dataset)
    write_outputs(summary)
    t = summary["totals"]
    print(f"\nTotal hands: {t['hands']:,}")
    print(f"  6 dealt: {t['hands_6_dealt']:,} ({t['pct_6_dealt']}%)")
    print(f"Included (USD, non-cap, 2011-2025): {t['included']:,}")
    print(f"  Included + 6 dealt: {t['included_6_dealt']:,} ({t['pct_included_6']}%)")


if __name__ == "__main__":
    main()
