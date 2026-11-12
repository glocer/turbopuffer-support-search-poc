"""Chart: warm client-side latency p50/p99 per search mode, against the README targets.

    .venv/bin/python bench/chart.py     (after bench/latency.py)

Output: results/<date>-latency.png
"""

import csv

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from common import DATE, RESULTS  # noqa: E402

BLUE, ORANGE = "#2a78d6", "#eb6834"  # categorical slots 1-2
INK, MUTED, GRID, SURFACE = "#1f1f1e", "#6b6a63", "#e6e5e0", "#fcfcfb"
TARGET_P50, TARGET_P99 = 100, 300


def main():
    rows = [r for r in csv.DictReader((RESULTS / f"{DATE}-latency.csv").open()) if r["temperature"] == "warm"]
    labels = [f"{r['mode']}\n{'no filter' if r['filter'] == 'none' else 'state=open'}" for r in rows]
    p50 = [float(r["client_p50_ms"]) for r in rows]
    p99 = [float(r["client_p99_ms"]) for r in rows]
    n = rows[0]["n"]

    fig, ax = plt.subplots(figsize=(9, 4.8), dpi=160, facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    x, w = np.arange(len(rows)), 0.36
    b1 = ax.bar(x - w / 2 - 0.01, p50, w, color=BLUE, label="p50", zorder=3)
    b2 = ax.bar(x + w / 2 + 0.01, p99, w, color=ORANGE, label="p99", zorder=3)
    for bars in (b1, b2):
        for b in bars:
            ax.annotate(f"{b.get_height():.0f}", (b.get_x() + b.get_width() / 2, b.get_height()),
                        xytext=(0, 3), textcoords="offset points", ha="center", fontsize=8, color=INK)

    top = max(max(p99) * 1.15, TARGET_P99 * 1.12)
    for y, name in ((TARGET_P50, "p50 target 100 ms"), (TARGET_P99, "p99 target 300 ms")):
        ax.axhline(y, color=MUTED, lw=1, ls=(0, (4, 3)), zorder=2)
        ax.text(len(rows) - 0.5, y + top * 0.012, name, ha="right", va="bottom", fontsize=8, color=MUTED)

    ax.set_ylim(0, top)
    ax.set_xticks(x, labels, fontsize=9, color=INK)
    ax.set_ylabel("ms, measured on laptop (network included)", fontsize=9, color=MUTED)
    ax.set_title(f"Warm query latency, 100k docs, {n} queries per bar", fontsize=12, color=INK, loc="left", pad=12)
    ax.grid(axis="y", color=GRID, lw=0.8, zorder=0)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(axis="y", colors=MUTED, labelsize=8, length=0)
    ax.tick_params(axis="x", length=0)
    ax.legend(frameon=False, fontsize=9, loc="upper left", ncols=2)
    fig.text(0.01, 0.01, "Toronto laptop (Apple M3 Pro) -> turbopuffer gcp-northamerica-northeast2. Top 10, sequential requests.",
             fontsize=7.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    out = RESULTS / f"{DATE}-latency.png"
    fig.savefig(out, facecolor=SURFACE)
    print(out)


if __name__ == "__main__":
    main()
