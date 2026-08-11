"""Exploratory data audit on raw PokerStars hand history files."""

from __future__ import annotations

import json
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASETS = ("NL50", "NL200", "NL5K")

HAND_START = re.compile(r"^PokerStars (?:Hand|Game) #", re.MULTILINE)
HEADER = re.compile(
    r"PokerStars (?:Hand|Game) #(\d+):\s+Hold'em No Limit \(([^)]+)\)\s+-\s+(\d{4}/\d{2}/\d{2} \d+:\d+:\d+)",
)
TABLE = re.compile(r"^Table '([^']+)'\s+(\d+)-max", re.MULTILINE)
CAP = re.compile(r"Cap", re.IGNORECASE)


@dataclass
class FileSample:
    path: str
    dataset: str
    year: str
    hand_count: int
    parse_errors: int
    table_sizes: Counter
    stakes: Counter
    cap_games: int
    first_date: str | None = None
    last_date: str | None = None


@dataclass
class AuditResult:
    files_by_dataset_year: dict[str, dict[str, int]] = field(default_factory=dict)
    samples: list[FileSample] = field(default_factory=list)
    estimated_hands_by_dataset: dict[str, int] = field(default_factory=dict)
    estimated_hands_by_dataset_year: dict[str, dict[str, int]] = field(default_factory=dict)
    total_txt_files: int = 0
    total_estimated_hands: int = 0


def iter_txt_files(dataset: str) -> list[Path]:
    root = PROJECT_ROOT / dataset
    return sorted(root.rglob("*.txt"))


def year_from_path(path: Path, dataset: str) -> str:
    rel = path.relative_to(PROJECT_ROOT / dataset)
    if rel.parts and rel.parts[0].isdigit() and len(rel.parts[0]) == 4:
        return rel.parts[0]
    return "unknown"


def analyze_file(path: Path, dataset: str) -> FileSample:
    text = path.read_text(encoding="utf-8", errors="replace")
    year = year_from_path(path, dataset)
    hand_count = len(HAND_START.findall(text))
    headers = HEADER.findall(text)
    table_sizes = Counter(int(m.group(2)) for m in TABLE.finditer(text))
    stakes = Counter(s.strip() for _, s, _ in headers)
    cap_games = sum(1 for _, s, _ in headers if CAP.search(s))
    dates = [datetime.strptime(d, "%Y/%m/%d %H:%M:%S") for _, _, d in headers]
    return FileSample(
        path=str(path.relative_to(PROJECT_ROOT)),
        dataset=dataset,
        year=year,
        hand_count=hand_count,
        parse_errors=max(0, hand_count - len(headers)),
        table_sizes=table_sizes,
        stakes=stakes,
        cap_games=cap_games,
        first_date=min(dates).strftime("%Y-%m-%d") if dates else None,
        last_date=max(dates).strftime("%Y-%m-%d") if dates else None,
    )


def sample_files(all_files: list[Path], n: int, seed: int = 42) -> list[Path]:
    if len(all_files) <= n:
        return all_files
    rng = random.Random(seed)
    return rng.sample(all_files, n)


def run_audit(sample_per_dataset: int = 200) -> AuditResult:
    result = AuditResult()
    hands_per_file_by_dataset: dict[str, list[int]] = defaultdict(list)

    for dataset in DATASETS:
        files = iter_txt_files(dataset)
        result.total_txt_files += len(files)
        by_year: dict[str, int] = defaultdict(int)
        for f in files:
            year = year_from_path(f, dataset)
            by_year[year] += 1
        result.files_by_dataset_year[dataset] = dict(sorted(by_year.items()))

        sampled = sample_files(files, sample_per_dataset)
        for path in sampled:
            sample = analyze_file(path, dataset)
            result.samples.append(sample)
            hands_per_file_by_dataset[dataset].append(sample.hand_count)

        avg_hands = (
            sum(hands_per_file_by_dataset[dataset]) / len(hands_per_file_by_dataset[dataset])
            if hands_per_file_by_dataset[dataset]
            else 0
        )
        est_by_year = {
            year: int(count * avg_hands)
            for year, count in result.files_by_dataset_year[dataset].items()
        }
        result.estimated_hands_by_dataset_year[dataset] = est_by_year
        result.estimated_hands_by_dataset[dataset] = sum(est_by_year.values())

    result.total_estimated_hands = sum(result.estimated_hands_by_dataset.values())
    return result


def summarize(result: AuditResult) -> dict:
    agg_table_sizes: Counter = Counter()
    agg_stakes: Counter = Counter()
    cap_total = 0
    hand_total_sample = 0
    for s in result.samples:
        agg_table_sizes.update(s.table_sizes)
        agg_stakes.update(s.stakes)
        cap_total += s.cap_games
        hand_total_sample += s.hand_count

    hands_per_file = [s.hand_count for s in result.samples]
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_txt_files": result.total_txt_files,
        "sampled_files": len(result.samples),
        "files_by_dataset_year": result.files_by_dataset_year,
        "estimated_hands_by_dataset": result.estimated_hands_by_dataset,
        "estimated_hands_by_dataset_year": result.estimated_hands_by_dataset_year,
        "total_estimated_hands": result.total_estimated_hands,
        "sample_hand_count": hand_total_sample,
        "hands_per_file": {
            "min": min(hands_per_file) if hands_per_file else 0,
            "median": sorted(hands_per_file)[len(hands_per_file) // 2] if hands_per_file else 0,
            "mean": round(sum(hands_per_file) / len(hands_per_file), 1) if hands_per_file else 0,
            "max": max(hands_per_file) if hands_per_file else 0,
        },
        "table_sizes_in_sample": dict(agg_table_sizes.most_common()),
        "stakes_in_sample": dict(agg_stakes.most_common(15)),
        "cap_games_in_sample": cap_total,
        "sample_files_with_zero_hands": sum(1 for s in result.samples if s.hand_count == 0),
        "sample_files_with_parse_gaps": sum(1 for s in result.samples if s.parse_errors > 0),
    }


def main() -> None:
    reports_dir = PROJECT_ROOT / "reports"
    reports_dir.mkdir(exist_ok=True)

    result = run_audit(sample_per_dataset=200)
    summary = summarize(result)

    out_json = reports_dir / "data_exploration_summary.json"
    out_md = reports_dir / "data_exploration_summary.md"

    out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = [
        "# Data Exploration Summary",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "## Corpus size (estimated)",
        "",
        f"- **Total `.txt` files:** {summary['total_txt_files']:,}",
        f"- **Estimated total hands:** {summary['total_estimated_hands']:,}",
        f"- **Sampled files:** {summary['sampled_files']} ({summary['sample_hand_count']:,} hands in sample)",
        "",
        "### Estimated hands by dataset",
        "",
        "| Dataset | Est. hands | Txt files |",
        "|---------|------------|-----------|",
    ]
    for ds in DATASETS:
        lines.append(
            f"| {ds} | {summary['estimated_hands_by_dataset'][ds]:,} | "
            f"{sum(summary['files_by_dataset_year'][ds].values()):,} |"
        )

    lines.extend(["", "### Files by year", ""])
    for ds in DATASETS:
        lines.append(f"**{ds}**")
        lines.append("")
        lines.append("| Year | Files | Est. hands |")
        lines.append("|------|-------|------------|")
        for year, count in summary["files_by_dataset_year"][ds].items():
            est = summary["estimated_hands_by_dataset_year"][ds][year]
            lines.append(f"| {year} | {count:,} | {est:,} |")
        lines.append("")

    lines.extend(
        [
            "## Sample characteristics",
            "",
            f"- **Hands per file:** min {summary['hands_per_file']['min']}, "
            f"median {summary['hands_per_file']['median']}, "
            f"mean {summary['hands_per_file']['mean']}, "
            f"max {summary['hands_per_file']['max']}",
            f"- **Table sizes (from sample headers):** {summary['table_sizes_in_sample']}",
            f"- **Cap games in sample:** {summary['cap_games_in_sample']:,}",
            f"- **Empty sample files:** {summary['sample_files_with_zero_hands']}",
            f"- **Sample files with header parse gaps:** {summary['sample_files_with_parse_gaps']}",
            "",
            "### Stakes observed in sample (top 15)",
            "",
        ]
    )
    for stake, count in summary["stakes_in_sample"].items():
        lines.append(f"- `{stake}` — {count:,} hands")

    lines.extend(
        [
            "",
            "> Note: hand totals are **estimated** from average hands/file in a random sample of 200 files per dataset.",
            "> Run a full hand count before finalizing scope.",
        ]
    )

    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_md}")


if __name__ == "__main__":
    main()
