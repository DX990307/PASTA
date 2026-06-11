#!/usr/bin/env python3
import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt


def read_csv(path):
    rows = []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                {
                    "k": int(row["k"]),
                    "total": float(row["total_median"]),
                    "per_load": float(row["cycles_per_load_median"]),
                    "p05": float(row["total_p05"]),
                    "p95": float(row["total_p95"]),
                }
            )
    rows.sort(key=lambda r: r["k"])
    return rows


def plot_series(ax, rows, key, label, color, marker):
    xs = [r["k"] for r in rows]
    ys = [r[key] for r in rows]
    ax.plot(xs, ys, color=color, marker=marker, linewidth=2.4, label=label)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--result-dir",
        default="results/2026-06-09-ptw-capacity",
        help="directory containing timing_ptw_capacity_{warm,cold}.csv",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    result_dir = Path(args.result_dir)
    if not result_dir.is_absolute():
        result_dir = script_dir / result_dir

    warm = read_csv(result_dir / "timing_ptw_capacity_warm.csv")
    cold = read_csv(result_dir / "timing_ptw_capacity_cold.csv")

    fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.2), constrained_layout=True)
    fig.suptitle("PTW capacity probe: sweep independent TLB misses per batch", fontsize=16)

    ax = axes[0]
    plot_series(ax, warm, "total", "warm", "#1f77b4", "o")
    plot_series(ax, cold, "total", "cold-cache", "#d62728", "s")
    ax.axvline(16, color="#2ca02c", linestyle="--", linewidth=1.8, label="K=16")
    ax.set_title("Batch latency")
    ax.set_xlabel("Independent TLB misses K")
    ax.set_ylabel("Median total latency (cycles)")
    ax.grid(True, alpha=0.25)
    ax.legend()

    ax = axes[1]
    plot_series(ax, warm, "per_load", "warm", "#1f77b4", "o")
    plot_series(ax, cold, "per_load", "cold-cache", "#d62728", "s")
    ax.axvline(16, color="#2ca02c", linestyle="--", linewidth=1.8, label="K=16")
    ax.set_title("Amortized latency")
    ax.set_xlabel("Independent TLB misses K")
    ax.set_ylabel("Median cycles/load")
    ax.grid(True, alpha=0.25)
    ax.legend()

    out_png = result_dir / "ptw_capacity.png"
    out_pdf = result_dir / "ptw_capacity.pdf"
    fig.savefig(out_png, dpi=180)
    fig.savefig(out_pdf)
    print(out_png)
    print(out_pdf)


if __name__ == "__main__":
    main()
