"""Plot dataset × year coverage heatmaps from hand_counts.json.

Usage:
  python scripts/plot_coverage_heatmap.py

Requires: matplotlib (pip install matplotlib)
Reads:    reports/hand_counts.json
Writes:   reports/coverage_heatmap_included.png
          reports/coverage_heatmap_total.png
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT = PROJECT_ROOT / "reports" / "hand_counts.json"


def load_matrix(field: str) -> tuple[list[str], list[str], list[list[int]]]:
    data = json.loads(REPORT.read_text(encoding="utf-8"))
    datasets = ["NL50", "NL200", "NL5K"]
    years = [str(y) for y in range(2011, 2026)]

    if field == "included":
        source = data.get("included_hands_matrix", {})
    else:
        source = {}
        for row in data.get("by_dataset_year", []):
            source.setdefault(row["dataset"], {})[row["year"]] = row[field]

    matrix = []
    for ds in datasets:
        row = [source.get(ds, {}).get(y, 0) for y in years]
        matrix.append(row)
    return datasets, years, matrix


def plot_heatmap(title: str, datasets: list[str], years: list[str], matrix: list[list[int]], out_path: Path) -> None:
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("matplotlib not installed. Run: pip install matplotlib")
        return

    arr = np.array(matrix, dtype=float)
    # log scale for readability when values span orders of magnitude
    display = np.log10(arr + 1)

    fig, ax = plt.subplots(figsize=(14, 4))
    im = ax.imshow(display, aspect="auto", cmap="YlOrRd")
    ax.set_xticks(range(len(years)))
    ax.set_xticklabels(years, rotation=45, ha="right")
    ax.set_yticks(range(len(datasets)))
    ax.set_yticklabels(datasets)
    ax.set_title(title)

    for i in range(len(datasets)):
        for j in range(len(years)):
            val = int(matrix[i][j])
            label = f"{val/1e6:.1f}M" if val >= 1_000_000 else f"{val/1e3:.0f}k" if val >= 1000 else str(val)
            color = "white" if display[i, j] > display.max() * 0.6 else "black"
            ax.text(j, i, label, ha="center", va="center", fontsize=7, color=color)

    cbar = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
    cbar.set_label("log10(hands + 1)")

    fig.tight_layout()
    out_path.parent.mkdir(exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"Wrote {out_path}")


def main() -> None:
    if not REPORT.exists():
        print(f"Missing {REPORT}. Run scripts/count_hands.py first.")
        return

    reports = PROJECT_ROOT / "reports"
    ds, years, included = load_matrix("included")
    plot_heatmap(
        "Included hands (USD, non-cap, 2011-2025)",
        ds,
        years,
        included,
        reports / "coverage_heatmap_included.png",
    )

    _, _, total = load_matrix("total")
    plot_heatmap(
        "Total hands (all currencies/types)",
        ds,
        years,
        total,
        reports / "coverage_heatmap_total.png",
    )


if __name__ == "__main__":
    main()
