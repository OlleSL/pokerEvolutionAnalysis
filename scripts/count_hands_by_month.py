"""Count included hands by dataset and year-month.

Usage:
  python scripts/count_hands_by_month.py
  python scripts/count_hands_by_month.py --dataset NL50
  python scripts/count_hands_by_month.py --plot

Writes:
  reports/hand_counts_monthly.json
  reports/hand_counts_monthly.csv
  reports/hand_counts_monthly.png (with --plot)
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


def is_included(stake: str, year: str) -> bool:
    s = stake.lower()
    is_usd = "usd" in s and "eur" not in s and "gbp" not in s
    is_cap = "cap" in s
    return is_usd and not is_cap and year in STUDY_YEARS


@dataclass
class MonthBucket:
    included: int = 0
    total: int = 0


def run(dataset: str | None = None) -> dict:
    datasets = [dataset] if dataset else list(DATASETS)
    by_key: dict[tuple[str, str], MonthBucket] = defaultdict(MonthBucket)
    files_processed = 0
    start = time.time()

    for ds in datasets:
        files = sorted((PROJECT_ROOT / ds).rglob("*.txt"))
        print(f"\n=== {ds}: {len(files):,} files ===", flush=True)
        for i, path in enumerate(files, 1):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                files_processed += 1
                continue

            for _, stake, date in HAND_HEADER.findall(text):
                year, month, _ = date.split("/")
                ym = f"{year}-{month}"
                key = (ds, ym)
                by_key[key].total += 1
                if is_included(stake.strip(), year):
                    by_key[key].included += 1

            files_processed += 1
            if i % 500 == 0 or i == len(files):
                elapsed = time.time() - start
                print(f"  {i:,}/{len(files):,} ({i/elapsed:.1f} files/s)", flush=True)

    rows = []
    totals_included = 0
    for (ds, ym), b in sorted(by_key.items()):
        rows.append(
            {
                "dataset": ds,
                "year_month": ym,
                "included_hands": b.included,
                "total_hands": b.total,
            }
        )
        totals_included += b.included

    by_dataset: dict[str, int] = defaultdict(int)
    for row in rows:
        by_dataset[row["dataset"]] += row["included_hands"]

    summary = {
        "files_processed": files_processed,
        "elapsed_minutes": round((time.time() - start) / 60, 1),
        "inclusion_rules": {
            "currency": "USD only",
            "cap_games": "excluded",
            "study_window": "2011-2025",
        },
        "totals": {"included_hands": totals_included},
        "by_dataset": dict(by_dataset),
        "by_dataset_month": rows,
    }
    return summary


def write_outputs(summary: dict) -> None:
    reports = PROJECT_ROOT / "reports"
    reports.mkdir(exist_ok=True)
    json_path = reports / "hand_counts_monthly.json"
    csv_path = reports / "hand_counts_monthly.csv"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    fields = ["dataset", "year_month", "included_hands", "total_hands"]
    if summary["by_dataset_month"]:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(summary["by_dataset_month"])
    print(f"Wrote {json_path}")
    print(f"Wrote {csv_path}")


def plot_monthly(summary: dict) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed — skipping plot")
        return

    reports = PROJECT_ROOT / "reports"
    by_ds: dict[str, dict[str, int]] = defaultdict(dict)
    for row in summary["by_dataset_month"]:
        by_ds[row["dataset"]][row["year_month"]] = row["included_hands"]

    fig, axes = plt.subplots(len(DATASETS), 1, figsize=(14, 4 * len(DATASETS)), sharex=True)
    if len(DATASETS) == 1:
        axes = [axes]

    for ax, ds in zip(axes, DATASETS):
        months = sorted(by_ds.get(ds, {}))
        counts = [by_ds[ds].get(m, 0) for m in months]
        ax.plot(months, counts, linewidth=0.8)
        ax.set_title(f"{ds} — included hands by month")
        ax.set_ylabel("Hands")
        ax.tick_params(axis="x", rotation=90, labelsize=6)
        every = max(1, len(months) // 12)
        ax.set_xticks(months[::every])

    axes[-1].set_xlabel("Year-month")
    fig.tight_layout()
    out = reports / "hand_counts_monthly.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"Wrote {out}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=DATASETS)
    parser.add_argument("--plot", action="store_true", help="Write monthly line plot")
    args = parser.parse_args()
    print("Counting included hands by month (full corpus)...", flush=True)
    summary = run(args.dataset)
    write_outputs(summary)
    if args.plot:
        plot_monthly(summary)
    print(f"\nIncluded hands: {summary['totals']['included_hands']:,}")


if __name__ == "__main__":
    main()
