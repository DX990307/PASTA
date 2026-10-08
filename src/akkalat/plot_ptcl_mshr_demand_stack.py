#!/usr/bin/env python3
import argparse
import csv
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


LABELS = {
    "aes": "AES",
    "bitonicsort": "Bitonic\nSort",
    "fastwalshtransform": "Fast Walsh\nTransform",
    "fft": "FFT",
    "fir": "FIR",
    "floydwarshall": "Floyd-\nWarshall",
    "im2col": "Im2Col",
    "kmeans": "KMeans",
    "llminference": "LLM\nInference",
    "matrixmultiplication-ptw-heavy": "MatMul\nPTW\nHeavy",
    "matrixmultiplication-ptw": "MatMul\nPTW",
    "matrixmultiplication": "Matrix\nMultiply",
    "matrixtranspose": "Matrix\nTranspose",
    "pagerank": "PageRank",
    "relu": "ReLU",
    "resnet": "ResNet",
    "simpleconvolution": "Simple\nConvolution",
    "spmv": "SPMV",
}

COLORS = [
    "#4E79A7",
    "#59A14F",
    "#F28E2B",
    "#E15759",
    "#76B7B2",
    "#B07AA1",
    "#EDC948",
    "#9C755F",
]

PAPER_LABELS = {
    "aes": "AES",
    "bitonicsort": "BT",
    "fastwalshtransform": "FWT",
    "fft": "FFT",
    "fir": "FIR",
    "floydwarshall": "FWS",
    "im2col": "I2C",
    "kmeans": "KM",
    "llminference": "LLM",
    "matrixmultiplication-ptw-heavy": "MM-PTW-H",
    "matrixmultiplication-ptw": "MM",
    "matrixmultiplication": "MM",
    "matrixtranspose": "MT",
    "pagerank": "PR",
    "relu": "RELU",
    "resnet": "RESNET",
    "simpleconvolution": "SC",
    "spmv": "SPMV",
}

BASELINE_PAPER_BENCHMARKS = [
    "aes",
    "bitonicsort",
    "fastwalshtransform",
    "fft",
    "fir",
    "floydwarshall",
    "im2col",
    "kmeans",
    "matrixmultiplication-ptw",
    "matrixtranspose",
    "pagerank",
    "relu",
    "simpleconvolution",
    "spmv",
]


def read_summary(path):
    records = []
    with path.open(newline="", encoding="utf-8") as csv_file:
        for row in csv.DictReader(csv_file):
            histogram = np.array(
                [float(row[f"demand_hist_{pte}"]) for pte in range(1, 9)],
                dtype=float,
            )
            total = histogram.sum()
            if total <= 0:
                continue
            records.append(
                {
                    "benchmark": row["benchmark"],
                    "average": float(row["avg_demand_pte"]),
                    "multi": float(row["multi_demand_fraction"]),
                    "fractions": histogram / total,
                }
            )
    return records


def plot(records, output_base):
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.labelsize": 11,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "legend.fontsize": 8.5,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )

    x = np.arange(len(records))
    fig, ax = plt.subplots(figsize=(14.2, 4.55))
    bottom = np.zeros(len(records))

    for pte in range(1, 9):
        values = np.array([record["fractions"][pte - 1] for record in records])
        ax.bar(
            x,
            values,
            width=0.78,
            bottom=bottom,
            color=COLORS[pte - 1],
            edgecolor="white",
            linewidth=0.35,
            label=str(pte),
        )
        bottom += values

    for index, record in enumerate(records):
        ax.text(
            index,
            1.018,
            f'{record["average"]:.2f}',
            ha="center",
            va="bottom",
            fontsize=8.3,
            color="#202020",
        )

    ax.set_ylabel("PTCL MSHR entries (%)")
    ax.set_ylim(0, 1.105)
    ax.set_xlim(-0.65, len(records) - 0.35)
    ax.set_xticks(x)
    ax.set_xticklabels(
        [LABELS.get(record["benchmark"], record["benchmark"]) for record in records]
    )
    ax.set_yticks(np.linspace(0, 1, 6))
    ax.set_yticklabels([f"{int(value * 100)}" for value in np.linspace(0, 1, 6)])
    ax.grid(axis="y", color="#D9D9D9", linewidth=0.65, alpha=0.85)
    ax.set_axisbelow(True)

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#707070")
    ax.spines["bottom"].set_color("#707070")
    ax.tick_params(axis="x", length=0, pad=7)
    ax.tick_params(axis="y", colors="#303030")

    ax.legend(
        title="Distinct demanded PTEs per PTCL MSHR",
        ncol=8,
        loc="lower center",
        bbox_to_anchor=(0.5, 1.015),
        frameon=False,
        columnspacing=1.2,
        handlelength=1.25,
        handletextpad=0.4,
        borderaxespad=0,
        title_fontsize=9,
    )
    ax.text(
        -0.58,
        1.025,
        "Avg.",
        ha="right",
        va="bottom",
        fontsize=8.3,
        fontweight="bold",
        color="#202020",
    )

    fig.subplots_adjust(left=0.06, right=0.995, bottom=0.22, top=0.85)
    output_base.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_base.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(output_base.with_suffix(".png"), dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_multi(records, output_base):
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.labelsize": 11,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )

    x = np.arange(len(records))
    values = np.array([record["multi"] * 100 for record in records])
    fig, ax = plt.subplots(figsize=(14.2, 3.9))
    bars = ax.bar(
        x,
        values,
        width=0.72,
        color="#4E79A7",
        edgecolor="white",
        linewidth=0.45,
    )

    for bar, value in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + 1.35,
            f"{value:.1f}",
            ha="center",
            va="bottom",
            fontsize=8.5,
            color="#202020",
        )

    ax.set_ylabel("Multi-PTE PTCL MSHRs (%)")
    ax.set_ylim(0, 86)
    ax.set_xlim(-0.65, len(records) - 0.35)
    ax.set_xticks(x)
    ax.set_xticklabels(
        [LABELS.get(record["benchmark"], record["benchmark"]) for record in records]
    )
    ax.set_yticks(np.arange(0, 81, 20))
    ax.grid(axis="y", color="#D9D9D9", linewidth=0.65, alpha=0.85)
    ax.set_axisbelow(True)

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#707070")
    ax.spines["bottom"].set_color("#707070")
    ax.tick_params(axis="x", length=0, pad=7)
    ax.tick_params(axis="y", colors="#303030")

    fig.subplots_adjust(left=0.06, right=0.995, bottom=0.25, top=0.96)
    output_base.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_base.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(output_base.with_suffix(".png"), dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_multi_paper(records, output_base):
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.linewidth": 0.8,
        }
    )

    x_step = 0.82
    x = np.arange(len(records)) * x_step
    group_width = 0.56
    bar_width = group_width * 0.56
    values = np.array([record["multi"] * 100 for record in records])
    fig, ax = plt.subplots(figsize=(3.45, 0.99))
    bars = ax.bar(
        x,
        values,
        width=bar_width,
        color="#ED6612",
        edgecolor="none",
        linewidth=0,
        label="Multi-PTE MSHR",
        zorder=3,
    )

    for bar, value in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + 1.15,
            f"{value:.1f}",
            ha="center",
            va="bottom",
            fontsize=4.9,
            fontweight="bold",
            color="#C88D04",
            clip_on=False,
        )

    ax.set_ylabel("Multi-PTE\nMSHRs (%)", fontsize=8.0)
    ax.set_ylim(0, 86)
    ax.set_xlim(x[0] - group_width * 0.62, x[-1] + group_width * 0.62)
    ax.set_xticks(x)
    ax.set_xticklabels([])
    ax.set_yticks(np.arange(0, 81, 20))
    ax.grid(axis="y", color="#dddddd", linewidth=0.45, zorder=1)
    ax.tick_params(axis="y", labelsize=6.0, length=2.4, width=0.7)
    ax.tick_params(axis="x", length=0)
    for position, record in zip(x, records):
        ax.text(
            position,
            -0.08,
            PAPER_LABELS.get(record["benchmark"], record["benchmark"]),
            transform=ax.get_xaxis_transform(),
            ha="center",
            va="top",
            rotation=30,
            rotation_mode="anchor",
            fontsize=7.0,
            clip_on=False,
        )

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(0.7)

    fig.tight_layout(pad=0.25)
    output_base.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_base.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(output_base.with_suffix(".png"), dpi=300, bbox_inches="tight")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(
        description="Plot the per-MSHR demanded-PTE distribution as stacked bars."
    )
    parser.add_argument("summary", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        help="Output path without an extension. Defaults beside the summary file.",
    )
    parser.add_argument(
        "--multi-only",
        action="store_true",
        help="Plot only the percentage of MSHRs containing multiple demanded PTEs.",
    )
    parser.add_argument(
        "--paper-style",
        action="store_true",
        help="Use the ablation-study paper figure style for the multi-PTE plot.",
    )
    parser.add_argument(
        "--baseline-paper-benchmarks",
        action="store_true",
        help="Keep only the benchmarks shown in speedup_baseline_pasta_paper.",
    )
    args = parser.parse_args()

    records = read_summary(args.summary)
    if not records:
        raise SystemExit("summary contains no MSHR demand histograms")

    if args.baseline_paper_benchmarks:
        by_name = {record["benchmark"]: record for record in records}
        records = [
            by_name[benchmark]
            for benchmark in BASELINE_PAPER_BENCHMARKS
            if benchmark in by_name
        ]
        if not records:
            raise SystemExit("none of the baseline-paper benchmarks are present")

    if args.paper_style and not args.multi_only:
        parser.error("--paper-style requires --multi-only")

    if args.paper_style and args.baseline_paper_benchmarks:
        default_name = "ptcl_mshr_multi_fraction_selected_paper"
    elif args.paper_style:
        default_name = "ptcl_mshr_multi_fraction_paper"
    elif args.multi_only:
        default_name = "ptcl_mshr_multi_fraction"
    else:
        default_name = "ptcl_mshr_demand_stack"
    output = args.output or args.summary.with_name(default_name)
    if args.paper_style:
        plot_multi_paper(records, output)
    elif args.multi_only:
        plot_multi(records, output)
    else:
        plot(records, output)
    print(output.with_suffix(".pdf"))
    print(output.with_suffix(".png"))


if __name__ == "__main__":
    main()
