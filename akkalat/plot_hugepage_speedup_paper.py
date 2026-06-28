#!/usr/bin/env python3
"""Plot the paper-style 64KB/2MB huge-page speedup figure.

This reproduces the style used by speedup_hugepage_2mb_64kb_paper.png:
thin paired bars, y=1 reference line, clipped tall bars annotated at the top,
and a shaded GMEAN column.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


BENCHMARK_ORDER = [
    ("aes", "AES"),
    ("bitonicsort", "BT"),
    ("fastwalshtransform", "FWT"),
    ("fft", "FFT"),
    ("fir", "FIR"),
    ("floydwarshall", "FWS"),
    ("im2col", "I2C"),
    ("kmeans", "KM"),
    ("matrixmultiplication-ptw", "MM"),
    ("pagerank", "PR"),
    ("relu", "RELU"),
    ("simpleconvolution", "SC"),
    ("spmv", "SPMV"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the paper-style huge-page speedup bar chart."
    )
    parser.add_argument(
        "--result-dir",
        type=Path,
        required=True,
        help="Hugepage sweep directory containing 64kb/ and 2mb/ metrics.",
    )
    parser.add_argument(
        "--output-prefix",
        type=Path,
        required=True,
        help="Output prefix. The script writes .png, .pdf, and _summary.csv.",
    )
    parser.add_argument(
        "--force-one",
        default="",
        help="Comma-separated benchmark names or labels to force to 1.0.",
    )
    parser.add_argument(
        "--ylim",
        type=float,
        default=2.0,
        help="Y-axis upper limit. Tall bars are clipped and annotated.",
    )
    return parser.parse_args()


def normalized_force_set(raw: str) -> set[str]:
    aliases = {bench.lower(): bench for bench, _ in BENCHMARK_ORDER}
    aliases.update({label.lower(): bench for bench, label in BENCHMARK_ORDER})
    forced = set()
    for token in raw.split(","):
        key = token.strip().lower()
        if not key:
            continue
        forced.add(aliases.get(key, key))
    return forced


def driver_total_time(path: Path) -> float:
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            values = {k.strip(): v.strip() for k, v in row.items()}
            if values.get("where") == "Driver" and values.get("what") == "total_time":
                return float(values["value"])
    raise ValueError(f"Driver total_time not found in {path}")


def read_page_speedups(result_dir: Path, page: str, forced: set[str]) -> dict[str, float]:
    data: dict[str, dict[str, float]] = {}
    page_dir = result_dir / page
    for path in sorted(page_dir.glob("400latency_*_*_metrics.csv")):
        match = re.match(r"400latency_(.*)_(baseline|pasta)_metrics\.csv", path.name)
        if not match:
            continue
        benchmark, config = match.groups()
        data.setdefault(benchmark, {})[config] = driver_total_time(path)

    speedups = {}
    for benchmark, label in BENCHMARK_ORDER:
        if benchmark in forced:
            speedups[benchmark] = 1.0
            continue

        config_data = data.get(benchmark, {})
        if "baseline" not in config_data or "pasta" not in config_data:
            raise ValueError(f"Missing {page} metrics for {label}")
        speedups[benchmark] = config_data["baseline"] / config_data["pasta"]

    return speedups


def geomean(values: list[float]) -> float:
    return math.exp(sum(math.log(value) for value in values) / len(values))


def write_summary(
    output_prefix: Path,
    speedups_2mb: dict[str, float],
    speedups_64kb: dict[str, float],
    gmean_2mb: float,
    gmean_64kb: float,
) -> Path:
    summary_path = output_prefix.with_name(output_prefix.name + "_summary.csv")
    with summary_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["benchmark", "2mb_speedup", "64kb_speedup"])
        for benchmark, label in BENCHMARK_ORDER:
            writer.writerow(
                [
                    label,
                    f"{speedups_2mb[benchmark]:.6f}",
                    f"{speedups_64kb[benchmark]:.6f}",
                ]
            )
        writer.writerow(["GMEAN", f"{gmean_2mb:.6f}", f"{gmean_64kb:.6f}"])
    return summary_path


def plot_paper_figure(
    output_prefix: Path,
    speedups_2mb: dict[str, float],
    speedups_64kb: dict[str, float],
    gmean_2mb: float,
    gmean_64kb: float,
    ylim: float,
) -> None:
    labels = [label for _, label in BENCHMARK_ORDER] + ["GMEAN"]
    values_2mb = [speedups_2mb[benchmark] for benchmark, _ in BENCHMARK_ORDER]
    values_64kb = [speedups_64kb[benchmark] for benchmark, _ in BENCHMARK_ORDER]
    values_2mb.append(gmean_2mb)
    values_64kb.append(gmean_64kb)

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 6,
            "axes.labelsize": 8,
            "xtick.labelsize": 6,
            "ytick.labelsize": 7,
            "legend.fontsize": 7,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )

    x = np.arange(len(labels))
    width = 0.22

    fig, ax = plt.subplots(figsize=(1067 / 300, 497 / 300), dpi=300)
    fig.subplots_adjust(left=0.105, right=0.985, bottom=0.225, top=0.800)
    color_2mb = "#58bfd7"
    color_64kb = "#ff6b12"

    ax.axvspan(len(labels) - 1.5, len(labels) - 0.5, color="0.93", zorder=0)
    ax.bar(x - width / 2, values_2mb, width, label="2MB", color=color_2mb, zorder=3)
    ax.bar(x + width / 2, values_64kb, width, label="64KB", color=color_64kb, zorder=3)

    ax.axhline(1.0, color="0.25", linewidth=1.0, linestyle=(0, (4, 3)), zorder=4)
    ax.axvline(len(labels) - 1.5, color="0.55", linewidth=0.9, linestyle=":", zorder=5)

    ax.set_ylabel("Speedup")
    ax.set_ylim(0, ylim)
    ax.set_yticks([0, 1, 2])
    ax.set_xlim(-0.55, len(labels) - 0.45)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=35, ha="right", rotation_mode="anchor")

    for spine in ax.spines.values():
        spine.set_linewidth(1.0)
    ax.tick_params(axis="both", width=1.0, length=4)

    ax.legend(
        frameon=False,
        loc="lower left",
        bbox_to_anchor=(0.0, 1.06, 1.0, 0.2),
        mode="expand",
        ncol=2,
        borderaxespad=0.0,
        handlelength=0.9,
        columnspacing=1.0,
    )

    for idx, value in enumerate(values_64kb):
        if value > ylim:
            ax.text(
                x[idx] + width / 2,
                ylim + 0.03,
                f"{value:.1f}",
                ha="center",
                va="bottom",
                color="#cc8400",
                fontsize=5,
                fontweight="bold",
                clip_on=False,
            )

    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_prefix.with_suffix(".png"))
    fig.savefig(output_prefix.with_suffix(".pdf"))
    plt.close(fig)


def main() -> None:
    args = parse_args()
    forced = normalized_force_set(args.force_one)
    result_dir = args.result_dir.resolve()
    output_prefix = args.output_prefix.resolve()

    speedups_64kb = read_page_speedups(result_dir, "64kb", forced)
    speedups_2mb = read_page_speedups(result_dir, "2mb", forced)
    values_64kb = [speedups_64kb[benchmark] for benchmark, _ in BENCHMARK_ORDER]
    values_2mb = [speedups_2mb[benchmark] for benchmark, _ in BENCHMARK_ORDER]
    gmean_64kb = geomean(values_64kb)
    gmean_2mb = geomean(values_2mb)

    summary_path = write_summary(
        output_prefix, speedups_2mb, speedups_64kb, gmean_2mb, gmean_64kb
    )
    plot_paper_figure(
        output_prefix, speedups_2mb, speedups_64kb, gmean_2mb, gmean_64kb, args.ylim
    )

    print(summary_path)
    print(output_prefix.with_suffix(".png"))
    print(output_prefix.with_suffix(".pdf"))
    print(f"2MB GMEAN {gmean_2mb:.6f}")
    print(f"64KB GMEAN {gmean_64kb:.6f}")


if __name__ == "__main__":
    main()
