"""Collect and classify all stake strings from hand headers.

Usage:
  python scripts/audit_stake_strings.py
  python scripts/audit_stake_strings.py --dataset NL50

Writes:
  reports/stake_strings.json
  reports/stake_strings.csv
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

HAND_HEADER = re.compile(
    r"PokerStars (?:Hand|Game) #(\d+):\s+Hold'em No Limit \(([^)]+)\)\s+-\s+(\d{4}/\d{2}/\d{2})",
)


def year_from_path(path: Path, dataset: str) -> str:
    rel = path.relative_to(PROJECT_ROOT / dataset)
    if rel.parts and rel.parts[0].isdigit() and len(rel.parts[0]) == 4:
        return rel.parts[0]
    return "unknown"


def classify_stake(stake: str) -> list[str]:
    s = stake.lower()
    flags: list[str] = []
    if "cap" in s:
        flags.append("cap")
    if "eur" in s or "€" in stake or "\u20ac" in stake:
        flags.append("eur")
    if "gbp" in s or "£" in stake:
        flags.append("gbp")
    if "usd" not in s and "eur" not in s and "gbp" not in s:
        flags.append("no_currency")
    if "\n" in stake or "PokerStars" in stake:
        flags.append("malformed")
    if len(stake) > 80:
        flags.append("unusually_long")
    if not re.search(r"\$[\d.]+/\$[\d.]+", stake) and "usd" in s and "cap" not in s:
        flags.append("unusual_format")
    return flags


def is_included(stake: str, year: str) -> bool:
    s = stake.lower()
    is_usd = "usd" in s and "eur" not in s and "gbp" not in s
    is_cap = "cap" in s
    return is_usd and not is_cap and year in STUDY_YEARS


@dataclass
class StakeStats:
    count: int = 0
    included_count: int = 0
    excluded_count: int = 0
    by_dataset: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_dataset_year: dict[str, dict[str, int]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(int))
    )
    flags: set[str] = field(default_factory=set)


def run(dataset: str | None = None) -> dict:
    datasets = [dataset] if dataset else list(DATASETS)
    stakes: dict[str, StakeStats] = {}
    files_processed = 0
    start = time.time()

    for ds in datasets:
        files = sorted((PROJECT_ROOT / ds).rglob("*.txt"))
        print(f"\n=== {ds}: {len(files):,} files ===", flush=True)
        for i, path in enumerate(files, 1):
            file_year = year_from_path(path, ds)
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                files_processed += 1
                continue

            for _, stake_raw, date in HAND_HEADER.findall(text):
                stake = stake_raw.strip()
                hand_year = date.split("/")[0]
                inc = is_included(stake, hand_year)
                stats = stakes.setdefault(stake, StakeStats())
                stats.count += 1
                stats.by_dataset[ds] += 1
                stats.by_dataset_year[ds][hand_year] += 1
                stats.flags.update(classify_stake(stake))
                if inc:
                    stats.included_count += 1
                else:
                    stats.excluded_count += 1

            files_processed += 1
            if i % 500 == 0 or i == len(files):
                elapsed = time.time() - start
                print(f"  {i:,}/{len(files):,} ({i/elapsed:.1f} files/s)", flush=True)

    rows = []
    flag_totals: dict[str, int] = defaultdict(int)
    for stake, stats in sorted(stakes.items(), key=lambda x: -x[1].count):
        for flag in stats.flags:
            flag_totals[flag] += stats.count
        rows.append(
            {
                "stake_string": stake,
                "count": stats.count,
                "included_count": stats.included_count,
                "excluded_count": stats.excluded_count,
                "flags": sorted(stats.flags),
                "by_dataset": dict(stats.by_dataset),
                "by_dataset_year": {
                    ds: dict(years) for ds, years in stats.by_dataset_year.items()
                },
            }
        )

    summary = {
        "files_processed": files_processed,
        "elapsed_minutes": round((time.time() - start) / 60, 1),
        "unique_stake_strings": len(stakes),
        "flag_totals": dict(sorted(flag_totals.items(), key=lambda x: -x[1])),
        "inclusion_rules": {
            "currency": "USD only",
            "cap_games": "excluded",
            "study_window": "2011-2025",
        },
        "stake_strings": rows,
    }
    return summary


def write_outputs(summary: dict) -> None:
    reports = PROJECT_ROOT / "reports"
    reports.mkdir(exist_ok=True)
    json_path = reports / "stake_strings.json"
    csv_path = reports / "stake_strings.csv"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    csv_rows = [
        {
            "stake_string": r["stake_string"],
            "count": r["count"],
            "included_count": r["included_count"],
            "excluded_count": r["excluded_count"],
            "flags": ";".join(r["flags"]),
        }
        for r in summary["stake_strings"]
    ]
    if csv_rows:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "stake_string",
                    "count",
                    "included_count",
                    "excluded_count",
                    "flags",
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
    print("Auditing stake strings (full corpus)...", flush=True)
    summary = run(args.dataset)
    write_outputs(summary)
    print(f"\nUnique stake strings: {summary['unique_stake_strings']:,}")
    print("Flag totals:", summary["flag_totals"])


if __name__ == "__main__":
    main()
