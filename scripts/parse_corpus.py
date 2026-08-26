"""Parse full hand history corpus to Parquet (resumable).

Usage:
  python scripts/parse_corpus.py                    # all datasets, 2011-2025
  python scripts/parse_corpus.py --dataset NL5K
  python scripts/parse_corpus.py --dataset NL50 --year 2018
  python scripts/parse_corpus.py --force            # re-parse existing slices

Output:
  data/parsed/hands/dataset=NL50/year=2018/part-000.parquet
  data/parsed/players/...
  data/parsed/actions/...
  data/parsed/parse_errors.jsonl
  data/parsed/parse_summary.json

Existing partitions are skipped unless --force. Hand IDs already in Parquet are
deduplicated on resume (loads seen IDs from output).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from parse_common import (  # noqa: E402
    DATASETS,
    STUDY_YEARS,
    discover_slices,
    load_seen_hand_ids,
    parse_slice,
    slice_complete,
)

OUTPUT_ROOT = PROJECT_ROOT / "data" / "parsed"


def append_errors(out_root: Path, errors: list[dict]) -> None:
    if not errors:
        return
    err_path = out_root / "parse_errors.jsonl"
    with err_path.open("a", encoding="utf-8") as f:
        for err in errors:
            f.write(json.dumps(err) + "\n")


def load_summary(out_root: Path) -> dict:
    path = out_root / "parse_summary.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"slices": {}, "generated_at": None}


def save_summary(out_root: Path, summary: dict) -> None:
    summary["updated_at"] = datetime.now().isoformat(timespec="seconds")
    path = out_root / "parse_summary.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse full corpus to Parquet")
    parser.add_argument("--dataset", choices=list(DATASETS))
    parser.add_argument("--year", help="Single year (e.g. 2018)")
    parser.add_argument("--output", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--force", action="store_true", help="Re-parse slices that already exist")
    parser.add_argument("--max-files", type=int, help="Limit files per slice (smoke test)")
    parser.add_argument("--clear", action="store_true", help="Delete output dir before starting")
    args = parser.parse_args()

    out_root: Path = args.output
    if args.clear and out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    years = (args.year,) if args.year else STUDY_YEARS
    datasets = (args.dataset,) if args.dataset else DATASETS
    slices = discover_slices(datasets, years)

    if not slices:
        print("No slices found. Check NL50/NL200/NL5K folders exist.")
        return

    print(f"Corpus parse: {len(slices)} slice(s) -> {out_root}", flush=True)
    seen_hand_ids = set() if args.force else load_seen_hand_ids(out_root)
    if seen_hand_ids:
        print(f"Resume: {len(seen_hand_ids):,} hand IDs already parsed", flush=True)

    summary = load_summary(out_root)
    if not summary.get("generated_at"):
        summary["generated_at"] = datetime.now().isoformat(timespec="seconds")

    parsed_count = skipped_count = 0
    for dataset, year in slices:
        key = f"{dataset}/{year}"
        if slice_complete(out_root, dataset, year) and not args.force:
            print(f"\n=== {key} (skip — partition exists, use --force to re-parse) ===", flush=True)
            skipped_count += 1
            continue

        print(f"\n=== {key} ===", flush=True)
        stats, slice_errors = parse_slice(
            dataset,
            year,
            max_files=args.max_files,
            seen_hand_ids=seen_hand_ids,
            out_root=out_root,
        )
        append_errors(out_root, slice_errors)
        summary["slices"][key] = stats
        save_summary(out_root, summary)
        parsed_count += 1
        print(
            f"  parsed={stats.get('hands_parsed', 0):,} included={stats.get('hands_included', 0):,} "
            f"primary={stats.get('hands_primary', 0):,} errors={stats.get('hands_parse_error', 0):,} "
            f"dupes={stats.get('hands_duplicate', 0):,}",
            flush=True,
        )

    save_summary(out_root, summary)
    print(f"\nDone. Parsed {parsed_count} slice(s), skipped {skipped_count}.")
    print(f"Summary: {out_root / 'parse_summary.json'}")
    print(f"Parquet: {out_root / 'hands'}")


if __name__ == "__main__":
    main()
