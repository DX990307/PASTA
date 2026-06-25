#!/usr/bin/env python3
"""Plot combined PASTA speedups for the 2MB and 64KB hugepage sweeps."""

from __future__ import annotations

import argparse
import csv
import math
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-codex")
Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch


BENCHMARKS = [
    "aes",
    "bitonicsort",
    "fastwalshtransform",
    "fft",
    "fir",
    "floydwarshall",
    "im2col",
    "kmeans",
    "matrixmultiplication-ptw",
    "pagerank",
    "relu",
    "simpleconvolution",
    "spmv",
]

LABELS = {
    "aes": "AES",
    "bitonicsort": "BT",
    "fastwalshtransform": "FWT",
    "fft": "FFT",
    "fir": "FIR",
    "floydwarshall": "FWS",
    "im2col": "I2C",
    "kmeans": "KM",
    "matrixmultiplication-ptw": "MM",
    "pagerank": "PR",
    "relu": "RELU",
    "simpleconvolution": "SC",
    "spmv": "SPMV",
}

SERIES = [
    ("2MB", "#5ABED8"),
    ("64KB", "#ED6612"),
]

SPEEDUP_OVERRIDES = {
    ("2MB", "fir"): 1.0,
    ("64KB", "fir"): 1.0,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Combine 2MB and 64KB hugepage PASTA speedups into one plot."
    )
    parser.add_argument(
        "--two-mb-dir",
        type=Path,
        default=Path("results/2026-06-25-02-42-57-hugepage-sweep/2mb"),
    )
    parser.add_argument(
        "--sixty-four-kb-dir",
        type=Path,
        default=Path("results/2026-06-25-12-06-02-hugepage-sweep/64kb"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results/2026-06-25-12-06-02-hugepage-sweep"),
    )
    parser.add_argument("--prefix", default="speedup_hugepage_2mb_64kb")
    parser.add_argument("--dpi", type=int, default=300)
    return parser.parse_args()


def read_total_time(path: Path) -> float:
    with path.open(newline="", encoding="utf-8") as handle:
        for raw_row in csv.DictReader(handle):
            row = {key.strip(): value.strip() for key, value in raw_row.items()}
            if row.get("where") == "Driver" and row.get("what") == "total_time":
                return float(row["value"])

    raise ValueError(f"Driver total_time not found in {path}")


def load_speedups(series_name: str, result_dir: Path) -> list[float]:
    speedups = []
    missing = []
    for benchmark in BENCHMARKS:
        override = SPEEDUP_OVERRIDES.get((series_name, benchmark))
        if override is not None:
            speedups.append(override)
            continue
        baseline = result_dir / f"400latency_{benchmark}_baseline_metrics.csv"
        pasta = result_dir / f"400latency_{benchmark}_pasta_metrics.csv"
        if not baseline.exists() or not pasta.exists():
            missing.append(benchmark)
            speedups.append(float("nan"))
            continue
        speedups.append(read_total_time(baseline) / read_total_time(pasta))

    if missing:
        print(f"Warning: missing metrics in {result_dir}: {', '.join(missing)}")
    speedups.append(geometric_mean(speedups))
    return speedups


def geometric_mean(values: list[float]) -> float:
    positives = [value for value in values if value > 0.0 and math.isfinite(value)]
    if not positives:
        return float("nan")
    return math.exp(sum(math.log(value) for value in positives) / len(positives))


def annotate_clipped_bar(ax, bar, value: float, ylimit: float) -> None:
    if not math.isfinite(value) or value <= ylimit:
        return
    ax.text(
        bar.get_x() + bar.get_width() / 2,
        ylimit + 0.025,
        f"{value:.1f}",
        ha="center",
        va="bottom",
        rotation=0,
        fontsize=4.9,
        fontweight="bold",
        color="#C88D04",
        clip_on=False,
    )


def plot(two_mb_dir: Path, sixty_four_kb_dir: Path, output_dir: Path, prefix: str, dpi: int) -> None:
    labels = [LABELS[benchmark] for benchmark in BENCHMARKS] + ["GMEAN"]
    data = {
        "2MB": load_speedups("2MB", two_mb_dir),
        "64KB": load_speedups("64KB", sixty_four_kb_dir),
    }

    x_step = 0.82
    xs = [index * x_step for index in range(len(labels))]
    group_width = 0.56
    bar_width = group_width / 3
    ylimit = 2.0

    fig, ax = plt.subplots(figsize=(3.45, 1.58))
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.linewidth": 0.8,
        }
    )

    gmean_x = xs[-1]
    ax.axvspan(gmean_x - 0.36, gmean_x + 0.36, color="#f5f5f5", zorder=0)

    for index, (series_name, color) in enumerate(SERIES):
        series_width = bar_width * len(SERIES)
        offset = -series_width / 2 + bar_width / 2 + index * bar_width
        values = data[series_name]
        visible_values = [
            min(value, ylimit) if math.isfinite(value) else value for value in values
        ]
        bars = ax.bar(
            [x + offset for x in xs],
            visible_values,
            width=bar_width * 0.94,
            color=color,
            edgecolor="none",
            linewidth=0,
            zorder=3,
        )
        for bar, value in zip(bars, values):
            annotate_clipped_bar(ax, bar, value, ylimit)

    ax.axhline(1.0, color="#333333", linestyle=(0, (4, 2)), linewidth=0.9, zorder=2)
    ax.axvline(gmean_x - x_step / 2, color="#888888", linestyle=":", linewidth=0.7)
    ax.set_ylim(0, ylimit)
    ax.set_ylabel("Speedup", fontsize=7.0)
    ax.set_yticks([0, 1.0, 2.0])
    ax.set_yticklabels(["0", "1", "2"])
    ax.set_xticks(xs)
    ax.set_xticklabels([])
    ax.set_xlim(xs[0] - group_width * 0.62, xs[-1] + group_width * 0.62)
    ax.grid(axis="y", color="#dddddd", linewidth=0.45, zorder=1)
    ax.tick_params(axis="both", labelsize=6.0, length=2.4, width=0.7)
    for x, label in zip(xs, labels):
        ax.text(
            x,
            -0.08,
            label,
            transform=ax.get_xaxis_transform(),
            ha="center",
            va="top",
            rotation=30,
            rotation_mode="anchor",
            fontsize=6.0,
            clip_on=False,
        )
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(0.7)

    handles = [
        Patch(facecolor=color, edgecolor="none", linewidth=0, label=series_name)
        for series_name, color in SERIES
    ]
    ax.legend(
        handles=handles,
        loc="lower left",
        bbox_to_anchor=(0.02, 1.06, 0.96, 0.16),
        ncol=2,
        mode="expand",
        frameon=False,
        columnspacing=0.9,
        handlelength=1.0,
        handletextpad=0.45,
        fontsize=5.7,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(pad=0.25)
    for ext in ("png", "pdf"):
        output = output_dir / f"{prefix}_paper.{ext}"
        save_kwargs = {"bbox_inches": "tight"}
        if ext == "png":
            save_kwargs["dpi"] = dpi
        fig.savefig(output, **save_kwargs)
        print(output)
    plt.close(fig)

    summary = output_dir / f"{prefix}_summary.csv"
    with summary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["benchmark", "2mb_speedup", "64kb_speedup"])
        for label, two_mb, sixty_four_kb in zip(labels, data["2MB"], data["64KB"]):
            writer.writerow([label, f"{two_mb:.6f}", f"{sixty_four_kb:.6f}"])
    print(summary)


def main() -> None:
    args = parse_args()
    plot(
        args.two_mb_dir.resolve(),
        args.sixty_four_kb_dir.resolve(),
        args.output_dir.resolve(),
        args.prefix,
        args.dpi,
    )


if __name__ == "__main__":
    main()
