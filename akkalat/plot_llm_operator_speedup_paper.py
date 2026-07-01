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
from matplotlib.patches import Patch


DEFAULT_RESULT_DIR = Path("results/2026-06-28-15-19-26-ptcl-sweep")
DEFAULT_SUMMARY = "llm_decomposed_combined_with_previous_per_op_summary.csv"

MODEL_ORDER = [
    ("gpt", "GPT-7B"),
    ("bert", "BERT-7B"),
    ("resnet", "ResNet-50"),
]

OP_ORDER = [
    "B-Emb",
    "G-Emb",
    "Q",
    "K",
    "V",
    "Score",
    "Mask",
    "Softmax",
    "Value",
    "O",
    "ARes",
    "Norm1",
    "FC1",
    "GELU",
    "FC2",
    "MRes",
    "Norm2",
    "Pooler",
    "Tanh",
    "Cls",
    "D-Emb",
    "D-Norm1",
    "D-Knew",
    "D-Vnew",
    "D-KVupd",
    "D-Attn",
    "D-ARes",
    "D-Norm2",
    "D-FC1",
    "D-GELU",
    "D-FC2",
    "D-MRes",
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Plot all LLM operator speedups in the selected-paper style."
    )
    parser.add_argument(
        "result_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_RESULT_DIR,
        help="Directory containing the combined LLM per-op summary.",
    )
    parser.add_argument(
        "--summary",
        default=DEFAULT_SUMMARY,
        help="Combined per-op summary filename in result_dir.",
    )
    parser.add_argument(
        "--output-prefix",
        default="llm_operator_speedup_selected_paper",
        help="Output filename prefix.",
    )
    return parser.parse_args()


def clean_row(row):
    return {
        key.strip(): value.strip() if isinstance(value, str) else value
        for key, value in row.items()
    }


def operator_label(op_name):
    if op_name == "embedding_word_position_token_type":
        return "B-Emb"
    if op_name == "embedding":
        return "G-Emb"
    if op_name == "pooler_dense":
        return "Pooler"
    if op_name == "pooler_tanh":
        return "Tanh"
    if op_name == "classifier":
        return "Cls"
    if op_name == "full":
        return "R50"
    if op_name.startswith("decode"):
        if re.match(r"^decode\d+_embedding$", op_name):
            return "D-Emb"
        short = re.sub(r"^decode\d+_layer\d+_", "", op_name)
        mapping = {
            "norm1": "D-Norm1",
            "attn_k_new": "D-Knew",
            "attn_v_new": "D-Vnew",
            "kv_cache_update": "D-KVupd",
            "decode_attention": "D-Attn",
            "attn_residual": "D-ARes",
            "norm2": "D-Norm2",
            "mlp_fc1": "D-FC1",
            "mlp_gelu": "D-GELU",
            "mlp_fc2": "D-FC2",
            "mlp_residual": "D-MRes",
        }
        if short in mapping:
            return mapping[short]
    short = re.sub(r"^layer\d+_", "", op_name)
    mapping = {
        "attn_q": "Q",
        "attn_k": "K",
        "attn_v": "V",
        "attn_score": "Score",
        "causal_mask": "Mask",
        "attn_softmax": "Softmax",
        "attn_value": "Value",
        "attn_out": "O",
        "attn_residual": "ARes",
        "norm1": "Norm1",
        "mlp_fc1": "FC1",
        "mlp_gelu": "GELU",
        "mlp_fc2": "FC2",
        "mlp_residual": "MRes",
        "norm2": "Norm2",
    }
    if short in mapping:
        return mapping[short]
    raise ValueError(f"unrecognized LLM op: {op_name}")


def collect_rows(summary_path):
    totals = {}
    model_totals = {}
    with summary_path.open(newline="") as f:
        for row in csv.DictReader(f):
            row = clean_row(row)
            config = row["config"]
            if config not in {"baseline", "pasta"}:
                continue
            model_bucket = model_totals.setdefault(
                row["model"], {"ops": 0, "baseline": 0.0, "pasta": 0.0}
            )
            model_bucket[config] += float(row["driver_total_time"])
            if config == "baseline":
                model_bucket["ops"] += 1

            label = operator_label(row["op_name"])
            bucket = totals.setdefault(
                label, {"ops": 0, "baseline": 0.0, "pasta": 0.0}
            )
            bucket[config] += float(row["driver_total_time"])
            if config == "baseline":
                bucket["ops"] += 1

    operator_rows = []
    for label in OP_ORDER:
        value = totals.get(label)
        if not value:
            continue
        if value["baseline"] <= 0 or value["pasta"] <= 0:
            continue
        operator_rows.append(
            {
                "operator": label,
                "ops": value["ops"],
                "baseline_time": value["baseline"],
                "pasta_time": value["pasta"],
                "speedup": value["baseline"] / value["pasta"],
            }
        )
    model_rows = []
    for model, label in MODEL_ORDER:
        value = model_totals.get(model)
        if not value:
            continue
        if value["baseline"] <= 0 or value["pasta"] <= 0:
            continue
        model_rows.append(
            {
                "operator": label,
                "ops": value["ops"],
                "baseline_time": value["baseline"],
                "pasta_time": value["pasta"],
                "speedup": value["baseline"] / value["pasta"],
            }
        )
    if not operator_rows:
        raise SystemExit(f"no complete operator rows found in {summary_path}")
    if not model_rows:
        raise SystemExit(f"no complete model rows found in {summary_path}")
    return operator_rows, model_rows


def format_row(row):
    formatted = dict(row)
    if formatted["ops"] != "":
        formatted["ops"] = str(formatted["ops"])
    for key in ["baseline_time", "pasta_time"]:
        if formatted[key] != "":
            formatted[key] = f"{float(formatted[key]):.12f}"
    formatted["speedup"] = f"{float(formatted['speedup']):.6f}"
    return formatted


def write_csv(path, operator_rows, model_rows):
    model_speeds = [row["speedup"] for row in model_rows]
    model_geomean = math.exp(
        sum(math.log(value) for value in model_speeds) / len(model_speeds)
    )

    fieldnames = [
        "operator",
        "ops",
        "baseline_time",
        "pasta_time",
        "speedup",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in operator_rows:
            writer.writerow(format_row(row))
        for row in model_rows:
            writer.writerow(format_row(row))
        writer.writerow(
            format_row(
                {
                    "operator": "GEOMEAN",
                    "ops": "",
                    "baseline_time": "",
                    "pasta_time": "",
                    "speedup": model_geomean,
                }
            )
        )


def plot(path_prefix, operator_rows, model_rows):
    model_speeds = [row["speedup"] for row in model_rows]
    model_geomean = math.exp(
        sum(math.log(value) for value in model_speeds) / len(model_speeds)
    )
    labels = (
        [row["operator"] for row in operator_rows]
        + [row["operator"] for row in model_rows]
        + ["GEOMEAN"]
    )
    pasta_values = (
        [row["speedup"] for row in operator_rows] + model_speeds + [model_geomean]
    )
    baseline_values = [1.0 for _ in labels]

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 16,
            "axes.labelsize": 24,
            "xtick.labelsize": 15,
            "ytick.labelsize": 21,
            "legend.fontsize": 18,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )

    fig, ax = plt.subplots(figsize=(18.4, 5.15))
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
    pasta_bars = ax.bar(
        [v + width / 2 for v in x],
        pasta_values,
        width,
        color=pasta_color,
        edgecolor="none",
        label="PASTA",
    )

    ymax = 2.0
    ax.axhline(1.0, color="#3a3a3a", linestyle="--", linewidth=2.3)
    ax.grid(axis="y", color="#d6d6d6", linewidth=1.0, alpha=0.75)
    ax.set_axisbelow(True)
    ax.set_ylabel("Speedup", labelpad=18)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=32, ha="right", rotation_mode="anchor")
    ax.set_ylim(0, ymax)
    ax.set_yticks([0, 1, 2])
    ax.set_yticklabels(["0", "1", "2"])
    ax.tick_params(axis="x", length=7, width=2, pad=0)
    ax.tick_params(axis="y", width=2, length=7, pad=8)
    for spine in ax.spines.values():
        spine.set_linewidth(2.1)
        spine.set_color("black")

    ax.axvline(
        len(operator_rows) - 0.5, color="#8b8b8b", linestyle=":", linewidth=2.0
    )
    ax.axvline(
        len(operator_rows) + len(model_rows) - 0.5,
        color="#8b8b8b",
        linestyle=":",
        linewidth=2.0,
    )
    ax.set_xlim(-0.6, len(labels) - 0.2)

    for bar, value in zip(pasta_bars, pasta_values):
        if value > ymax:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                ymax + 0.02,
                f"{value:.1f}",
                ha="center",
                va="bottom",
                fontsize=13,
                color="#C88300",
                fontweight="bold",
                clip_on=False,
            )

    handles = [
        Patch(facecolor=baseline_color, edgecolor="none", label="Baseline"),
        Patch(facecolor=pasta_color, edgecolor="none", label="PASTA"),
    ]
    ax.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.21),
        ncol=2,
        frameon=False,
        handlelength=0.9,
        columnspacing=12.0,
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
    operator_rows, model_rows = collect_rows(result_dir / args.summary)
    output_prefix = result_dir / args.output_prefix
    png_path, pdf_path = plot(output_prefix, operator_rows, model_rows)
    csv_path = output_prefix.with_suffix(".csv")
    write_csv(csv_path, operator_rows, model_rows)
    print(f"Wrote {png_path}")
    print(f"Wrote {pdf_path}")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
