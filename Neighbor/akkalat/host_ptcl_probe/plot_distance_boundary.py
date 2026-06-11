#!/usr/bin/env python3
import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt


def read_csv(path):
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append(
                {
                    "d": int(row["d"]),
                    "b1": float(row["b1_after_a_median"]),
                    "hit": float(row["b2_hit_median"]),
                    "cold": float(row["cold_b_median"]),
                }
            )
    return rows


def series(rows, key):
    rows = sorted(rows, key=lambda r: r["d"])
    return [r["d"] for r in rows], [r[key] for r in rows]


def add_boundary_lines(ax):
    ax.axvline(8, color="#d62728", linestyle="--", linewidth=1.4, label="8-PTE line")
    ax.axvline(16, color="#2ca02c", linestyle="--", linewidth=1.4, label="observed step")


def plot_panel(ax, rows, title):
    x, b1 = series(rows, "b1")
    _, hit = series(rows, "hit")
    _, cold = series(rows, "cold")

    ax.plot(x, b1, marker="o", linewidth=2.2, color="#1f77b4", label="B after A")
    ax.plot(x, hit, linestyle=":", linewidth=1.6, color="#6b7280", label="B hit lower bound")
    ax.plot(x, cold, linestyle="-.", linewidth=1.6, color="#9ca3af", label="cold B")
    add_boundary_lines(ax)
    ax.set_title(title)
    ax.set_xlabel("VPN distance d")
    ax.set_ylabel("Median load latency (cycles)")
    ax.grid(True, alpha=0.25)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--broad",
        default="results/2026-06-08-distance-boundary/timing_distance_cold.csv",
        help="Cold-cache broad distance CSV",
    )
    parser.add_argument(
        "--dense",
        default="results/2026-06-08-distance-boundary-dense/timing_distance_cold.csv",
        help="Cold-cache dense distance CSV",
    )
    parser.add_argument(
        "--out",
        default="results/2026-06-08-distance-boundary/distance_boundary.png",
        help="Output image path",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    broad_path = (script_dir / args.broad).resolve()
    dense_path = (script_dir / args.dense).resolve()
    out_path = (script_dir / args.out).resolve()

    broad = read_csv(broad_path)
    dense = read_csv(dense_path)

    plt.rcParams.update(
        {
            "font.size": 11,
            "axes.titlesize": 13,
            "axes.labelsize": 11,
            "legend.fontsize": 9,
        }
    )

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    plot_panel(axes[0], broad, "Cold-cache distance sweep")
    plot_panel(axes[1], dense, "Zoom around d=8..32")

    axes[0].set_xscale("log", base=2)
    axes[0].set_xticks([1, 4, 8, 16, 64, 512, 2048, 8192])
    axes[0].set_xticklabels(["1", "4", "8", "16", "64", "512", "2048", "8192"])
    axes[1].set_xticks([8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 24, 32])
    axes[1].tick_params(axis="x", rotation=45)

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=5, frameon=False)
    fig.suptitle("A-to-B page-walk locality: no d=8 step, clear d=16 step", y=0.98)
    fig.subplots_adjust(top=0.82, bottom=0.25, left=0.07, right=0.98, wspace=0.28)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=220, bbox_inches="tight")
    fig.savefig(out_path.with_suffix(".pdf"), bbox_inches="tight")
    print(out_path)


if __name__ == "__main__":
    main()
