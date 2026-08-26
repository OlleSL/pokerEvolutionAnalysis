"""Parse sample hand history slices to Parquet.

Usage:
  python scripts/parse_sample.py --max-files 5          # quick smoke test
  python scripts/parse_sample.py                        # default sample slices
  python scripts/parse_sample.py --dataset NL50 --year 2018

Default slices (from proceed.md):
  NL50/2018, NL200/2018, NL5K/2014

Writes:
  data/parsed/sample/hands/dataset=.../year=.../part-000.parquet
  data/parsed/sample/players/...
  data/parsed/sample/actions/...
  data/parsed/sample/parse_errors.jsonl
  data/parsed/sample/parse_summary.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from parse_common import parse_slice  # noqa: E402

DEFAULT_SLICES = (
    ("NL50", "2018"),
    ("NL200", "2018"),
    ("NL5K", "2014"),
)
OUTPUT_ROOT = PROJECT_ROOT / "data" / "parsed" / "sample"


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse sample hand history slices to Parquet")
    parser.add_argument("--dataset", choices=["NL50", "NL200", "NL5K"])
    parser.add_argument("--year")
    parser.add_argument("--max-files", type=int, help="Limit files per slice (for quick tests)")
    parser.add_argument("--output", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--clear", action="store_true", help="Remove existing sample output first")
    args = parser.parse_args()

    if args.dataset and args.year:
        slices = [(args.dataset, args.year)]
    else:
        slices = list(DEFAULT_SLICES)

    out_root: Path = args.output
    if args.clear and out_root.exists():
        import shutil

        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    seen_hand_ids: set[str] = set()
    all_errors: list[dict] = []
    summary: dict = {"slices": {}, "generated_at": datetime.now().isoformat(timespec="seconds")}

    print(f"Parsing {len(slices)} slice(s) -> {out_root}", flush=True)
    for dataset, year in slices:
        print(f"\n=== {dataset}/{year} ===", flush=True)
        stats, slice_errors = parse_slice(
            dataset, year, max_files=args.max_files, seen_hand_ids=seen_hand_ids, out_root=out_root
        )
        all_errors.extend(slice_errors)
        summary["slices"][f"{dataset}/{year}"] = stats
        print(
            f"  parsed={stats.get('hands_parsed', 0):,} included={stats.get('hands_included', 0):,} "
            f"primary={stats.get('hands_primary', 0):,} errors={stats.get('hands_parse_error', 0):,} "
            f"dupes={stats.get('hands_duplicate', 0):,}",
            flush=True,
        )

    err_path = out_root / "parse_errors.jsonl"
    with err_path.open("w", encoding="utf-8") as f:
        for err in all_errors:
            f.write(json.dumps(err) + "\n")

    summary_path = out_root / "parse_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nWrote {summary_path}")
    print(f"Parquet under {out_root / 'hands'}")


if __name__ == "__main__":
    main()
