#!/usr/bin/env python3
import argparse
import csv
import math
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch


DEFAULT_RESULT_DIR = Path("results/2026-06-28-15-19-26-ptcl-sweep")
DEFAULT_SUMMARY = "llm_decomposed_combined_with_previous_summary.csv"

MODEL_ORDER = [
    ("gpt", "GPT-7B"),
    ("bert", "BERT-7B"),
    ("resnet", "ResNet-50"),
]


def parse_args():
    parser = argparse.ArgumentParser(description="Plot workload-level LLM speedup.")
    parser.add_argument(
        "result_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_RESULT_DIR,
        help="Directory containing the combined LLM summary.",
    )
    parser.add_argument(
        "--summary",
        default=DEFAULT_SUMMARY,
        help="Combined summary filename in result_dir.",
    )
    parser.add_argument(
        "--output-prefix",
        default="llm_workload_speedup_selected_paper",
        help="Output filename prefix.",
    )
    return parser.parse_args()


def clean_row(row):
    return {
        key.strip(): value.strip() if isinstance(value, str) else value
        for key, value in row.items()
    }


def collect_rows(summary_path):
    by_model = {}
    with summary_path.open(newline="") as f:
        for row in csv.DictReader(f):
            row = clean_row(row)
            if row["config"] in {"baseline", "pasta"}:
                by_model.setdefault(row["model"], {})[row["config"]] = row

    rows = []
    for model, label in MODEL_ORDER:
        pair = by_model.get(model, {})
        if "baseline" not in pair or "pasta" not in pair:
            continue
        baseline_time = float(pair["baseline"]["driver_total_time_sum"])
        pasta_time = float(pair["pasta"]["driver_total_time_sum"])
        rows.append(
            {
                "workload": label,
                "ops": int(pair["baseline"]["ops"]),
                "baseline_time": baseline_time,
                "pasta_time": pasta_time,
                "speedup": baseline_time / pasta_time,
            }
        )
    if len(rows) != len(MODEL_ORDER):
        raise SystemExit(f"missing model rows in {summary_path}")
    return rows


def write_csv(path, rows):
    geomean = math.exp(sum(math.log(row["speedup"]) for row in rows) / len(rows))
    fieldnames = ["workload", "ops", "baseline_time", "pasta_time", "speedup"]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "workload": row["workload"],
                    "ops": row["ops"],
                    "baseline_time": f"{row['baseline_time']:.12f}",
                    "pasta_time": f"{row['pasta_time']:.12f}",
                    "speedup": f"{row['speedup']:.6f}",
                }
            )
        writer.writerow(
            {
                "workload": "GEOMEAN",
                "ops": "",
                "baseline_time": "",
                "pasta_time": "",
                "speedup": f"{geomean:.6f}",
            }
        )


def plot(path_prefix, rows):
    geomean = math.exp(sum(math.log(row["speedup"]) for row in rows) / len(rows))
    labels = [row["workload"] for row in rows] + ["GEOMEAN"]
    pasta_values = [row["speedup"] for row in rows] + [geomean]
    baseline_values = [1.0 for _ in labels]

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 18,
            "axes.labelsize": 24,
            "xtick.labelsize": 21,
            "ytick.labelsize": 21,
            "legend.fontsize": 20,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )

    fig, ax = plt.subplots(figsize=(8.6, 4.7))
    x = list(range(len(labels)))
    width = 0.26
    baseline_color = "#8DD0E3"
    pasta_color = "#F26413"

    ax.bar(
        [v - width / 2 for v in x],
        baseline_values,
        width,
        color=baseline_color,
        edgecolor="none",
        label="Baseline",
    )
    ax.bar(
        [v + width / 2 for v in x],
        pasta_values,
        width,
        color=pasta_color,
        edgecolor="none",
        label="PASTA",
    )

    ax.axhline(1.0, color="#3a3a3a", linestyle="--", linewidth=2.3)
    ax.axvline(len(rows) - 0.5, color="#8b8b8b", linestyle=":", linewidth=2.0)
    ax.grid(axis="y", color="#d6d6d6", linewidth=1.0, alpha=0.75)
    ax.set_axisbelow(True)
    ax.set_ylabel("Speedup", labelpad=18)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=27, ha="center", rotation_mode="anchor")
    ax.set_ylim(0, 2.0)
    ax.set_yticks([0, 1, 2])
    ax.set_yticklabels(["0", "1", "2"])
    ax.tick_params(axis="x", length=7, width=2, pad=18)
    ax.tick_params(axis="y", width=2, length=7, pad=8)
    for spine in ax.spines.values():
        spine.set_linewidth(2.1)
        spine.set_color("black")
    ax.set_xlim(-0.55, len(labels) - 0.25)

    handles = [
        Patch(facecolor=baseline_color, edgecolor="none", label="Baseline"),
        Patch(facecolor=pasta_color, edgecolor="none", label="PASTA"),
    ]
    ax.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.22),
        ncol=2,
        frameon=False,
        handlelength=0.9,
        columnspacing=9.0,
        borderaxespad=0.0,
    )

    fig.tight_layout(pad=0.65)
    png_path = path_prefix.with_suffix(".png")
    pdf_path = path_prefix.with_suffix(".pdf")
    fig.savefig(png_path, dpi=100)
    fig.savefig(pdf_path)
    return png_path, pdf_path


def main():
    args = parse_args()
    result_dir = args.result_dir.resolve()
    rows = collect_rows(result_dir / args.summary)
    output_prefix = result_dir / args.output_prefix
    png_path, pdf_path = plot(output_prefix, rows)
    csv_path = output_prefix.with_suffix(".csv")
    write_csv(csv_path, rows)
    print(f"Wrote {png_path}")
    print(f"Wrote {pdf_path}")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
