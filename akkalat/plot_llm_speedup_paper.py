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

MODEL_LABELS = {
    "bert": "BERT",
    "gpt": "GPT",
    "resnet": "R50",
}
MODEL_ORDER = ["bert", "gpt", "resnet"]


def parse_args():
    parser = argparse.ArgumentParser(description="Plot LLM PASTA speedup in paper style.")
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
        default="llm_speedup_selected_paper",
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
            model = row["model"]
            config = row["config"]
            if config not in {"baseline", "pasta"}:
                continue
            by_model.setdefault(model, {})[config] = row

    rows = []
    for model in MODEL_ORDER:
        pair = by_model.get(model, {})
        if "baseline" not in pair or "pasta" not in pair:
            continue
        baseline_time = float(pair["baseline"]["driver_total_time_sum"])
        pasta_time = float(pair["pasta"]["driver_total_time_sum"])
        if baseline_time <= 0 or pasta_time <= 0:
            continue
        rows.append(
            {
                "model": model,
                "label": MODEL_LABELS.get(model, model.upper()),
                "ops": int(pair["baseline"]["ops"]),
                "baseline_time": baseline_time,
                "pasta_time": pasta_time,
                "speedup": baseline_time / pasta_time,
            }
        )
    if not rows:
        raise SystemExit(f"no complete baseline/pasta rows found in {summary_path}")
    return rows


def format_row(row):
    formatted = dict(row)
    if formatted["ops"] != "":
        formatted["ops"] = str(formatted["ops"])
    for key in ["baseline_time", "pasta_time"]:
        if formatted[key] != "":
            formatted[key] = f"{float(formatted[key]):.12f}"
    formatted["speedup"] = f"{float(formatted['speedup']):.6f}"
    return formatted


def write_csv(path, rows):
    speeds = [row["speedup"] for row in rows]
    avg = sum(speeds) / len(speeds)
    geomean = math.exp(sum(math.log(value) for value in speeds) / len(speeds))
    total_baseline = sum(row["baseline_time"] for row in rows)
    total_pasta = sum(row["pasta_time"] for row in rows)

    fieldnames = [
        "model",
        "label",
        "ops",
        "baseline_time",
        "pasta_time",
        "speedup",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(format_row(row))
        writer.writerow(
            format_row(
                {
                    "model": "TOTAL",
                    "label": "TOTAL",
                    "ops": sum(row["ops"] for row in rows),
                    "baseline_time": total_baseline,
                    "pasta_time": total_pasta,
                    "speedup": total_baseline / total_pasta,
                }
            )
        )
        writer.writerow(
            format_row(
                {
                    "model": "AVG",
                    "label": "AVG",
                    "ops": "",
                    "baseline_time": "",
                    "pasta_time": "",
                    "speedup": avg,
                }
            )
        )
        writer.writerow(
            format_row(
                {
                    "model": "GEOMEAN",
                    "label": "GEOMEAN",
                    "ops": "",
                    "baseline_time": "",
                    "pasta_time": "",
                    "speedup": geomean,
                }
            )
        )


def plot(path_prefix, rows):
    speeds = [row["speedup"] for row in rows]
    avg = sum(speeds) / len(speeds)
    geomean = math.exp(sum(math.log(value) for value in speeds) / len(speeds))
    labels = [row["label"] for row in rows] + ["AVG", "GEOMEAN"]
    values = speeds + [avg, geomean]

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 18,
            "axes.labelsize": 24,
            "xtick.labelsize": 21,
            "ytick.labelsize": 21,
            "legend.fontsize": 18,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )

    fig, ax = plt.subplots(figsize=(10.67, 4.89))
    x = list(range(len(labels)))
    pasta_color = "#F26413"
    bars = ax.bar(x, values, 0.26, color=pasta_color, edgecolor="none", label="PASTA")

    ymax = 2.0
    ax.axhline(1.0, color="#3a3a3a", linestyle="--", linewidth=2.3)
    ax.grid(axis="y", color="#d6d6d6", linewidth=1.0, alpha=0.75)
    ax.set_axisbelow(True)
    ax.set_ylabel("Speedup", labelpad=18)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=27, ha="center", rotation_mode="anchor")
    ax.set_ylim(0, ymax)
    ax.set_yticks([0, 1, 2])
    ax.set_yticklabels(["0", "1", "2"])
    ax.tick_params(axis="x", length=7, width=2, pad=0)
    ax.tick_params(axis="y", width=2, length=7, pad=8)
    for spine in ax.spines.values():
        spine.set_linewidth(2.1)
        spine.set_color("black")

    separator_x = len(rows) - 0.5
    ax.axvline(separator_x, color="#8b8b8b", linestyle=":", linewidth=2.0)
    ax.set_xlim(-0.55, len(labels) - 0.05)

    for bar, value in zip(bars, values):
        if value > ymax:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                ymax + 0.02,
                f"{value:.1f}",
                ha="center",
                va="bottom",
                fontsize=15,
                color="#C88300",
                fontweight="bold",
                clip_on=False,
            )

    ax.legend(
        handles=[Patch(facecolor=pasta_color, edgecolor="none", label="PASTA")],
        loc="upper center",
        bbox_to_anchor=(0.5, 1.23),
        frameon=False,
        handlelength=0.9,
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
    summary_path = result_dir / args.summary
    rows = collect_rows(summary_path)
    output_prefix = result_dir / args.output_prefix
    png_path, pdf_path = plot(output_prefix, rows)
    csv_path = output_prefix.with_suffix(".csv")
    write_csv(csv_path, rows)
    print(f"Wrote {png_path}")
    print(f"Wrote {pdf_path}")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
