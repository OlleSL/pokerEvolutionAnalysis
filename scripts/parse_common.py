"""Shared hand-history -> Parquet parsing helpers."""

from __future__ import annotations

import sys
import time
from collections import defaultdict
from pathlib import Path

import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from poker_parser.hand_parser import ParseError, SkipHand, parse_hand, split_hands  # noqa: E402

DATASETS = ("NL50", "NL200", "NL5K")
STUDY_YEARS = tuple(str(y) for y in range(2011, 2026))


def iter_txt_files(dataset: str, year: str) -> list[Path]:
    root = PROJECT_ROOT / dataset / year
    if not root.exists():
        return []
    return sorted(root.rglob("*.txt"))


def discover_slices(
    datasets: tuple[str, ...] = DATASETS,
    years: tuple[str, ...] = STUDY_YEARS,
) -> list[tuple[str, str]]:
    slices: list[tuple[str, str]] = []
    for dataset in datasets:
        ds_root = PROJECT_ROOT / dataset
        if not ds_root.exists():
            continue
        for year in years:
            if (ds_root / year).exists():
                slices.append((dataset, year))
    return slices


def partition_path(out_root: Path, table: str, dataset: str, year: str) -> Path:
    return out_root / table / f"dataset={dataset}" / f"year={year}" / "part-000.parquet"


def slice_complete(out_root: Path, dataset: str, year: str) -> bool:
    """Slice is complete only when hands, players, and actions partitions exist."""
    return all(
        partition_path(out_root, table, dataset, year).exists()
        for table in ("hands", "players", "actions")
    )


def write_partition(table: str, dataset: str, year: str, rows: list[dict], out_root: Path) -> None:
    if not rows:
        return
    path = partition_path(out_root, table, dataset, year)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def load_seen_hand_ids(out_root: Path) -> set[str]:
    hands_glob = str(out_root / "hands" / "**" / "*.parquet").replace("\\", "/")
    if not any(out_root.glob("hands/**/*.parquet")):
        return set()
    con = duckdb.connect()
    rows = con.execute("SELECT hand_id FROM read_parquet(?)", [hands_glob]).fetchall()
    return {str(r[0]) for r in rows}


def parse_slice(
    dataset: str,
    year: str,
    *,
    max_files: int | None,
    seen_hand_ids: set[str],
    out_root: Path,
) -> tuple[dict, list[dict]]:
    files = iter_txt_files(dataset, year)
    if max_files:
        files = files[:max_files]

    hands: list[dict] = []
    players: list[dict] = []
    actions: list[dict] = []
    errors: list[dict] = []
    stats: defaultdict[str, int] = defaultdict(int)

    t0 = time.time()
    for fi, path in enumerate(files, 1):
        rel = str(path.relative_to(PROJECT_ROOT))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            errors.append({"source_file": rel, "hand_id": None, "error_type": "read_error", "message": str(exc)})
            stats["files_read_error"] += 1
            continue

        for block in split_hands(text):
            try:
                parsed = parse_hand(block, dataset=dataset, source_file=rel)
            except SkipHand as exc:
                stats[f"skipped_{exc.reason}"] += 1
                continue
            except ParseError as exc:
                errors.append({"source_file": rel, "hand_id": None, "error_type": "parse_error", "message": str(exc)})
                stats["hands_parse_error"] += 1
                continue

            hid = parsed.hand["hand_id"]
            if hid in seen_hand_ids:
                errors.append(
                    {"source_file": rel, "hand_id": hid, "error_type": "duplicate", "message": "duplicate hand_id skipped"}
                )
                stats["hands_duplicate"] += 1
                continue
            seen_hand_ids.add(hid)

            hands.append(parsed.hand)
            players.extend(parsed.players)
            actions.extend(parsed.actions)
            stats["hands_parsed"] += 1
            if parsed.hand["is_included"]:
                stats["hands_included"] += 1
            elif parsed.hand.get("is_ante"):
                stats["hands_ante_excluded"] += 1
            if parsed.hand["is_primary"]:
                stats["hands_primary"] += 1

        if fi % 50 == 0 or fi == len(files):
            elapsed = time.time() - t0
            rate = fi / elapsed if elapsed > 0 else 0
            print(f"  {dataset}/{year}: {fi:,}/{len(files):,} files ({rate:.1f} files/s)", flush=True)

    write_partition("hands", dataset, year, hands, out_root)
    write_partition("players", dataset, year, players, out_root)
    write_partition("actions", dataset, year, actions, out_root)

    stats["files_processed"] = len(files)
    stats["elapsed_seconds"] = round(time.time() - t0, 1)
    return dict(stats), errors
