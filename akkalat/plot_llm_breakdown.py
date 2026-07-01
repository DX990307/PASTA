#!/usr/bin/env python3
import argparse
import csv
import math
import os
import re
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


LLM_RE = re.compile(
    r"^400latency_llmop_(?P<model>[^_]+)_(?P<profile>[^_]+)_"
    r"(?P<index>\d+)_(?P<op>.+)_(?P<config>baseline|pasta)_metrics\.csv$"
)

OP_ORDER = [
    "Embedding",
    "Attn Q/K/V/O",
    "Attn Score",
    "Softmax",
    "Causal Mask",
    "Attn Value",
    "LayerNorm",
    "MLP FC1",
    "GELU",
    "MLP FC2",
    "Residual",
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Plot LLM decomposed op breakdown and combined speedup."
    )
    parser.add_argument(
        "result_dir",
        nargs="?",
        type=Path,
        default=Path("results/2026-06-28-05-48-43-ptcl-sweep"),
        help="Directory containing decomposed LLM metric CSVs.",
    )
    parser.add_argument(
        "--output-prefix",
        default="llm_breakdown_combined",
        help="Output filename prefix.",
    )
    parser.add_argument(
        "--include-no-fc2",
        action="store_true",
        default=True,
        help="Include an overall LLM bar that excludes MLP FC2.",
    )
    return parser.parse_args()


def read_total_time(path):
    with path.open(newline="") as f:
        reader = csv.DictReader(f, skipinitialspace=True)
        for row in reader:
            row = {
                key.strip(): value.strip() if isinstance(value, str) else value
                for key, value in row.items()
            }
            if row.get("where") == "Driver" and row.get("what") == "total_time":
                return float(row["value"])
    raise ValueError(f"Driver total_time not found in {path}")


def op_group(op_name):
    short = re.sub(r"^layer\d+_", "", op_name)
    if short == "embedding":
        return "Embedding"
    if short in {"attn_q", "attn_k", "attn_v", "attn_out"}:
        return "Attn Q/K/V/O"
    if short == "attn_score":
        return "Attn Score"
    if short == "attn_softmax":
        return "Softmax"
    if short == "causal_mask":
        return "Causal Mask"
    if short == "attn_value":
        return "Attn Value"
    if short in {"norm1", "norm2"}:
        return "LayerNorm"
    if short == "mlp_fc1":
        return "MLP FC1"
    if short == "mlp_gelu":
        return "GELU"
    if short == "mlp_fc2":
        return "MLP FC2"
    if short in {"attn_residual", "mlp_residual"}:
        return "Residual"
    return short


def collect_pairs(result_dir):
    records = {}
    for path in sorted(result_dir.glob("400latency_llmop_*_metrics.csv")):
        match = LLM_RE.match(path.name)
        if not match:
            continue
        key = (
            match.group("model"),
            match.group("profile"),
            int(match.group("index")),
            match.group("op"),
        )
        records.setdefault(key, {})[match.group("config")] = read_total_time(path)

    pairs = []
    for key, values in records.items():
        if "baseline" not in values or "pasta" not in values:
            continue
        model, profile, index, op_name = key
        pairs.append(
            {
                "model": model,
                "profile": profile,
                "index": index,
                "op_name": op_name,
                "group": op_group(op_name),
                "baseline": values["baseline"],
                "pasta": values["pasta"],
            }
        )
    if not pairs:
        raise SystemExit(f"no paired LLM baseline/pasta metrics found in {result_dir}")
    return pairs


def speedup(baseline, pasta):
    if pasta <= 0:
        return math.nan
    return baseline / pasta


def grouped_breakdown(pairs):
    groups = {}
    for record in pairs:
        group = groups.setdefault(
            record["group"], {"count": 0, "baseline": 0.0, "pasta": 0.0}
        )
        group["count"] += 1
        group["baseline"] += record["baseline"]
        group["pasta"] += record["pasta"]

    ordered = []
    for name in OP_ORDER:
        if name in groups:
            value = groups[name]
            ordered.append(
                {
                    "name": name,
                    "count": value["count"],
                    "baseline": value["baseline"],
                    "pasta": value["pasta"],
                    "speedup": speedup(value["baseline"], value["pasta"]),
                }
            )
    for name in sorted(set(groups) - set(OP_ORDER)):
        value = groups[name]
        ordered.append(
            {
                "name": name,
                "count": value["count"],
                "baseline": value["baseline"],
                "pasta": value["pasta"],
                "speedup": speedup(value["baseline"], value["pasta"]),
            }
        )
    return ordered


def combined_bars(pairs, include_no_fc2=True):
    bars = []
    for label, predicate in [
        ("BERT", lambda r: r["model"] == "bert"),
        ("GPT", lambda r: r["model"] == "gpt"),
        ("LLM", lambda r: r["model"] in {"bert", "gpt"}),
    ]:
        selected = [record for record in pairs if predicate(record)]
        baseline = sum(record["baseline"] for record in selected)
        pasta = sum(record["pasta"] for record in selected)
        bars.append(
            {
                "name": label,
                "count": len(selected),
                "baseline": baseline,
                "pasta": pasta,
                "speedup": speedup(baseline, pasta),
            }
        )
    if include_no_fc2:
        selected = [
            record
            for record in pairs
            if record["model"] in {"bert", "gpt"} and record["group"] != "MLP FC2"
        ]
        baseline = sum(record["baseline"] for record in selected)
        pasta = sum(record["pasta"] for record in selected)
        bars.append(
            {
                "name": "LLM\nno FC2",
                "count": len(selected),
                "baseline": baseline,
                "pasta": pasta,
                "speedup": speedup(baseline, pasta),
            }
        )
    return bars


def write_csv(path, breakdown, combined):
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "section",
                "name",
                "count",
                "baseline",
                "pasta",
                "speedup",
            ],
        )
        writer.writeheader()
        for section, rows in [("breakdown", breakdown), ("combined", combined)]:
            for row in rows:
                writer.writerow(
                    {
                        "section": section,
                        "name": row["name"].replace("\n", " "),
                        "count": row["count"],
                        "baseline": f"{row['baseline']:.12g}",
                        "pasta": f"{row['pasta']:.12g}",
                        "speedup": f"{row['speedup']:.6f}",
                    }
                )


def annotate_bars(ax, bars, values, dy=0.035, fontsize=8):
    for bar, value in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + dy,
            f"{value:.2f}x",
            ha="center",
            va="bottom",
            fontsize=fontsize,
            color="#222222",
        )


def plot(result_dir, output_prefix, include_no_fc2):
    pairs = collect_pairs(result_dir)
    breakdown = grouped_breakdown(pairs)
    combined = combined_bars(pairs, include_no_fc2)

    plt.rcParams.update(
        {
            "font.size": 9,
            "axes.titlesize": 11,
            "axes.labelsize": 10,
            "xtick.labelsize": 8,
            "ytick.labelsize": 9,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )

    fig = plt.figure(figsize=(12.2, 4.1))
    grid = fig.add_gridspec(1, 2, width_ratios=[3.15, 1.15], wspace=0.22)
    ax_breakdown = fig.add_subplot(grid[0, 0])
    ax_combined = fig.add_subplot(grid[0, 1])

    breakdown_names = [row["name"] for row in breakdown]
    breakdown_values = [row["speedup"] for row in breakdown]
    breakdown_colors = [
        "#f58518" if row["name"] == "MLP FC2" else "#4c78a8"
        for row in breakdown
    ]
    x = range(len(breakdown))
    bars = ax_breakdown.bar(
        x,
        breakdown_values,
        color=breakdown_colors,
        edgecolor="#222222",
        linewidth=0.6,
        width=0.72,
    )
    ax_breakdown.axhline(1.0, color="#555555", linewidth=0.9, linestyle="--")
    ax_breakdown.set_title("LLM Operator Breakdown")
    ax_breakdown.set_ylabel("Speedup over baseline")
    ax_breakdown.set_xticks(list(x))
    ax_breakdown.set_xticklabels(breakdown_names, rotation=34, ha="right")
    ax_breakdown.grid(axis="y", color="#d9d9d9", linewidth=0.6, alpha=0.7)
    ax_breakdown.set_axisbelow(True)
    annotate_bars(ax_breakdown, bars, breakdown_values)

    combined_names = [row["name"] for row in combined]
    combined_values = [row["speedup"] for row in combined]
    combined_colors = [
        "#59a14f" if "no FC2" not in row["name"] else "#8c8c8c"
        for row in combined
    ]
    x2 = range(len(combined))
    bars2 = ax_combined.bar(
        x2,
        combined_values,
        color=combined_colors,
        edgecolor="#222222",
        linewidth=0.6,
        width=0.68,
    )
    ax_combined.axhline(1.0, color="#555555", linewidth=0.9, linestyle="--")
    ax_combined.set_title("Combined")
    ax_combined.set_xticks(list(x2))
    ax_combined.set_xticklabels(combined_names)
    ax_combined.grid(axis="y", color="#d9d9d9", linewidth=0.6, alpha=0.7)
    ax_combined.set_axisbelow(True)
    annotate_bars(ax_combined, bars2, combined_values)

    ymax = max(max(breakdown_values), max(combined_values)) * 1.14
    ax_breakdown.set_ylim(0, ymax)
    ax_combined.set_ylim(0, ymax)

    for ax in [ax_breakdown, ax_combined]:
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    fig.suptitle("Decomposed LLM Workloads: PASTA Speedup", y=0.995, fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.96))

    png_path = result_dir / f"{output_prefix}.png"
    pdf_path = result_dir / f"{output_prefix}.pdf"
    csv_path = result_dir / f"{output_prefix}.csv"
    fig.savefig(png_path, dpi=300, bbox_inches="tight")
    fig.savefig(pdf_path, bbox_inches="tight")
    write_csv(csv_path, breakdown, combined)
    return png_path, pdf_path, csv_path


def main():
    args = parse_args()
    result_dir = args.result_dir.resolve()
    png_path, pdf_path, csv_path = plot(
        result_dir,
        args.output_prefix,
        args.include_no_fc2,
    )
    print(f"Wrote {png_path}")
    print(f"Wrote {pdf_path}")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
