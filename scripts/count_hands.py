"""Exact hand counts with inclusion-rule breakdown (USD, cap, study window).

Usage:
  python scripts/count_hands.py                  # full corpus (slow: ~2-4 hours)
  python scripts/count_hands.py --dataset NL50   # one dataset
  python scripts/count_hands.py --year 2018      # one year across datasets
  python scripts/count_hands.py --quick          # one random file per year/dataset

Writes:
  reports/hand_counts.json
  reports/hand_counts.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASETS = ("NL50", "NL200", "NL5K")
STUDY_YEARS = tuple(str(y) for y in range(2011, 2026))

HAND_HEADER = re.compile(
    r"PokerStars (?:Hand|Game) #(\d+):\s+Hold'em No Limit \(([^)]+)\)\s+-\s+(\d{4}/\d{2}/\d{2})",
)


@dataclass
class CountBucket:
    total: int = 0
    usd: int = 0
    non_usd: int = 0
    cap: int = 0
    non_cap: int = 0
    included: int = 0  # USD + non-cap + year in study window

    def add(self, stake: str, year: str) -> None:
        self.total += 1
        stake_lower = stake.lower()
        is_usd = "usd" in stake_lower and "eur" not in stake_lower and "gbp" not in stake_lower
        is_cap = "cap" in stake_lower
        in_window = year in STUDY_YEARS

        if is_usd:
            self.usd += 1
        else:
            self.non_usd += 1
        if is_cap:
            self.cap += 1
        else:
            self.non_cap += 1
        if is_usd and not is_cap and in_window:
            self.included += 1

    def to_dict(self) -> dict[str, int]:
        return {
            "total": self.total,
            "usd": self.usd,
            "non_usd": self.non_usd,
            "cap": self.cap,
            "non_cap": self.non_cap,
            "included": self.included,
        }


@dataclass
class CountResult:
    by_dataset_year: dict[str, dict[str, CountBucket]] = field(default_factory=dict)
    files_processed: int = 0
    files_skipped: int = 0
    elapsed_seconds: float = 0.0


def year_from_path(path: Path, dataset: str) -> str:
    rel = path.relative_to(PROJECT_ROOT / dataset)
    if rel.parts and rel.parts[0].isdigit() and len(rel.parts[0]) == 4:
        return rel.parts[0]
    return "unknown"


def iter_files(dataset: str | None = None, year: str | None = None) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    datasets = [dataset] if dataset else list(DATASETS)
    for ds in datasets:
        root = PROJECT_ROOT / ds
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.txt")):
            y = year_from_path(path, ds)
            if year and y != year:
                continue
            if y != "unknown" and y not in STUDY_YEARS and year is None:
                # still count 2010 for reporting, but mark outside window via year check in add()
                pass
            files.append((ds, path))
    return files


def quick_files(seed: int = 42) -> list[tuple[str, Path]]:
    rng = random.Random(seed)
    picked: list[tuple[str, Path]] = []
    for ds in DATASETS:
        by_year: dict[str, list[Path]] = defaultdict(list)
        for path in (PROJECT_ROOT / ds).rglob("*.txt"):
            by_year[year_from_path(path, ds)].append(path)
        for year_paths in by_year.values():
            if year_paths:
                picked.append((ds, rng.choice(year_paths)))
    return picked


def count_file(path: Path, dataset: str) -> CountBucket:
    bucket = CountBucket()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return bucket
    for _, stake, date in HAND_HEADER.findall(text):
        year = date.split("/")[0]
        bucket.add(stake.strip(), year)
    return bucket


def merge_bucket(target: CountBucket, source: CountBucket) -> None:
    for field_name in ("total", "usd", "non_usd", "cap", "non_cap", "included"):
        setattr(target, field_name, getattr(target, field_name) + getattr(source, field_name))


def run_count(
    files: list[tuple[str, Path]],
    progress_every: int = 500,
) -> CountResult:
    result = CountResult()
    start = time.time()

    for i, (dataset, path) in enumerate(files, start=1):
        year = year_from_path(path, dataset)
        bucket = count_file(path, dataset)
        if bucket.total == 0:
            result.files_skipped += 1
        result.files_processed += 1

        result.by_dataset_year.setdefault(dataset, {}).setdefault(year, CountBucket())
        merge_bucket(result.by_dataset_year[dataset][year], bucket)

        if i % progress_every == 0 or i == len(files):
            elapsed = time.time() - start
            rate = i / elapsed if elapsed else 0
            print(
                f"  {i:,}/{len(files):,} files "
                f"({rate:.1f} files/s, {elapsed/60:.1f} min elapsed)",
                flush=True,
            )

    result.elapsed_seconds = time.time() - start
    return result


def summarize(result: CountResult) -> dict:
    rows = []
    totals = CountBucket()

    for ds in DATASETS:
        for year in sorted(result.by_dataset_year.get(ds, {})):
            b = result.by_dataset_year[ds][year]
            row = {"dataset": ds, "year": year, **b.to_dict()}
            rows.append(row)
            merge_bucket(totals, b)

    included_by_ds_year = {
        ds: {
            year: result.by_dataset_year[ds][year].included
            for year in sorted(result.by_dataset_year.get(ds, {}))
        }
        for ds in DATASETS
    }

    return {
        "study_years": list(STUDY_YEARS),
        "inclusion_rules": {
            "currency": "USD only",
            "cap_games": "excluded",
            "table_size": "6-max (to verify in parser)",
            "study_window": "2011-2025",
        },
        "files_processed": result.files_processed,
        "files_skipped_empty": result.files_skipped,
        "elapsed_minutes": round(result.elapsed_seconds / 60, 1),
        "totals": totals.to_dict(),
        "by_dataset_year": rows,
        "included_hands_matrix": included_by_ds_year,
    }


def write_outputs(summary: dict) -> None:
    reports = PROJECT_ROOT / "reports"
    reports.mkdir(exist_ok=True)

    json_path = reports / "hand_counts.json"
    csv_path = reports / "hand_counts.csv"

    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if summary["by_dataset_year"]:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=summary["by_dataset_year"][0].keys())
            writer.writeheader()
            writer.writerows(summary["by_dataset_year"])

    print(f"Wrote {json_path}")
    print(f"Wrote {csv_path}")


def print_summary(summary: dict) -> None:
    print("\n=== Hand count summary ===")
    print(f"Files processed: {summary['files_processed']:,}")
    print(f"Elapsed: {summary['elapsed_minutes']} min")
    t = summary["totals"]
    print(f"Total hands scanned: {t['total']:,}")
    print(f"  USD: {t['usd']:,} | non-USD: {t['non_usd']:,}")
    print(f"  Cap: {t['cap']:,} | non-cap: {t['non_cap']:,}")
    print(f"  Included (USD, non-cap, 2011-2025): {t['included']:,}")
    print("\nIncluded hands by dataset × year:")
    for ds in DATASETS:
        parts = summary["included_hands_matrix"].get(ds, {})
        line = "  " + ds + ": " + ", ".join(f"{y}={parts.get(y, 0):,}" for y in STUDY_YEARS if y in parts)
        print(line)


def main() -> None:
    parser = argparse.ArgumentParser(description="Exact hand counts with inclusion breakdown")
    parser.add_argument("--dataset", choices=DATASETS, help="Limit to one dataset")
    parser.add_argument("--year", help="Limit to one year folder (e.g. 2018)")
    parser.add_argument("--quick", action="store_true", help="One random file per year per dataset")
    args = parser.parse_args()

    if args.quick:
        files = quick_files()
        print(f"Quick mode: {len(files)} files")
    else:
        files = iter_files(dataset=args.dataset, year=args.year)
        print(f"Counting {len(files):,} files...")
        if len(files) > 50_000 and not args.dataset and not args.year:
            print("  Full corpus — expect roughly 2-4 hours depending on disk speed.")

    result = run_count(files)
    summary = summarize(result)
    write_outputs(summary)
    print_summary(summary)


if __name__ == "__main__":
    main()
