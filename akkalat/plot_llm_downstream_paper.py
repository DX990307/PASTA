#!/usr/bin/env python3
import argparse
import csv
import math
import os
from functools import lru_cache
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch


DEFAULT_RESULT_DIR = Path("results/2026-06-28-15-19-26-ptcl-sweep")
DEFAULT_PREVIOUS_RESULT_DIR = Path("results/2026-06-28-05-48-43-ptcl-sweep")
DEFAULT_SUMMARY = "llm_decomposed_combined_with_previous_per_op_summary.csv"

MODEL_LABELS = {
    "bert": "BERT",
    "gpt": "GPT",
    "resnet": "R50",
}
MODEL_ORDER = ["bert", "gpt", "resnet"]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Plot LLM downstream work in the paper style."
    )
    parser.add_argument(
        "result_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_RESULT_DIR,
        help="Directory that contains the combined per-op summary and new-op metrics.",
    )
    parser.add_argument(
        "--previous-result-dir",
        type=Path,
        default=DEFAULT_PREVIOUS_RESULT_DIR,
        help="Directory that contains previous prefill/resnet metrics used by the combined summary.",
    )
    parser.add_argument(
        "--summary",
        default=DEFAULT_SUMMARY,
        help="Combined per-op summary filename in result_dir.",
    )
    parser.add_argument(
        "--output-prefix",
        default="llm_downstream_compare_baseline_vs_lookup_table_paper",
        help="Output filename prefix.",
    )
    return parser.parse_args()


def clean_row(row):
    return {
        key.strip(): value.strip() if isinstance(value, str) else value
        for key, value in row.items()
    }


def metric_path(result_dir, previous_result_dir, filename):
    for directory in (result_dir, previous_result_dir):
        path = directory / filename
        if path.exists():
            return path
    raise FileNotFoundError(f"cannot find metric file {filename}")


@lru_cache(maxsize=None)
def read_l2tlb_counts(path):
    counts = {"downstream": 0.0, "local": 0.0, "iommu": 0.0}
    targets = {
        "downstream_req_count": "downstream",
        "local_req_count": "local",
        "iommu_req_count": "iommu",
    }
    with Path(path).open() as f:
        for line in f:
            if ".L2TLB" not in line:
                continue
            matched = None
            for counter in targets:
                if counter in line:
                    matched = counter
                    break
            if matched is None:
                continue
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 4:
                continue
            counts[targets[matched]] += float(parts[3])
    return counts


def collect_counts(result_dir, previous_result_dir, summary_name):
    summary_path = result_dir / summary_name
    if not summary_path.exists():
        raise FileNotFoundError(summary_path)

    totals = {}
    with summary_path.open(newline="") as f:
        for row in csv.DictReader(f):
            row = clean_row(row)
            model = row["model"]
            config = row["config"]
            if config not in {"baseline", "pasta"}:
                continue
            key = (model, config)
            bucket = totals.setdefault(
                key,
                {"ops": 0, "downstream": 0.0, "local": 0.0, "iommu": 0.0},
            )
            counts = read_l2tlb_counts(
                metric_path(result_dir, previous_result_dir, row["metrics_file"])
            )
            bucket["ops"] += 1
            for name, value in counts.items():
                bucket[name] += value
    return totals


def build_rows(totals):
    rows = []
    for model in MODEL_ORDER:
        baseline = totals.get((model, "baseline"))
        pasta = totals.get((model, "pasta"))
        if not baseline or not pasta or baseline["downstream"] <= 0:
            continue
        ratio = pasta["downstream"] / baseline["downstream"]
        rows.append(
            {
                "model": model,
                "label": MODEL_LABELS.get(model, model.upper()),
                "ops": baseline["ops"],
                "baseline_downstream": baseline["downstream"],
                "pasta_downstream": pasta["downstream"],
                "downstream_ratio": ratio,
                "downstream_reduction_percent": (1.0 - ratio) * 100.0,
                "baseline_local": baseline["local"],
                "pasta_local": pasta["local"],
                "baseline_iommu": baseline["iommu"],
                "pasta_iommu": pasta["iommu"],
            }
        )
    if not rows:
        raise SystemExit("no complete baseline/pasta LLM rows found")
    return rows


def write_csv(path, rows):
    ratios = [row["downstream_ratio"] for row in rows]
    avg_ratio = sum(ratios) / len(ratios)
    geomean_ratio = math.exp(sum(math.log(max(r, 1e-12)) for r in ratios) / len(ratios))
    total_baseline = sum(row["baseline_downstream"] for row in rows)
    total_pasta = sum(row["pasta_downstream"] for row in rows)

    fieldnames = [
        "model",
        "label",
        "ops",
        "baseline_downstream",
        "pasta_downstream",
        "downstream_ratio",
        "downstream_reduction_percent",
        "baseline_local",
        "pasta_local",
        "baseline_iommu",
        "pasta_iommu",
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
                    "baseline_downstream": total_baseline,
                    "pasta_downstream": total_pasta,
                    "downstream_ratio": total_pasta / total_baseline,
                    "downstream_reduction_percent": (1.0 - total_pasta / total_baseline)
                    * 100.0,
                    "baseline_local": sum(row["baseline_local"] for row in rows),
                    "pasta_local": sum(row["pasta_local"] for row in rows),
                    "baseline_iommu": sum(row["baseline_iommu"] for row in rows),
                    "pasta_iommu": sum(row["pasta_iommu"] for row in rows),
                }
            )
        )
        writer.writerow(
            format_row(
                {
                    "model": "AVG",
                    "label": "AVG",
                    "ops": "",
                    "baseline_downstream": "",
                    "pasta_downstream": "",
                    "downstream_ratio": avg_ratio,
                    "downstream_reduction_percent": (1.0 - avg_ratio) * 100.0,
                    "baseline_local": "",
                    "pasta_local": "",
                    "baseline_iommu": "",
                    "pasta_iommu": "",
                }
            )
        )
        writer.writerow(
            format_row(
                {
                    "model": "GEOMEAN",
                    "label": "GEOMEAN",
                    "ops": "",
                    "baseline_downstream": "",
                    "pasta_downstream": "",
                    "downstream_ratio": geomean_ratio,
                    "downstream_reduction_percent": (1.0 - geomean_ratio) * 100.0,
                    "baseline_local": "",
                    "pasta_local": "",
                    "baseline_iommu": "",
                    "pasta_iommu": "",
                }
            )
        )


def format_row(row):
    formatted = dict(row)
    for key in [
        "baseline_downstream",
        "pasta_downstream",
        "baseline_local",
        "pasta_local",
        "baseline_iommu",
        "pasta_iommu",
    ]:
        if formatted[key] != "":
            formatted[key] = str(int(round(float(formatted[key]))))
    for key in ["downstream_ratio", "downstream_reduction_percent"]:
        formatted[key] = f"{float(formatted[key]):.6f}"
    return formatted


def plot(path_prefix, rows):
    ratios = [row["downstream_ratio"] for row in rows]
    avg_ratio = sum(ratios) / len(ratios)
    geomean_ratio = math.exp(sum(math.log(max(r, 1e-12)) for r in ratios) / len(ratios))

    labels = [row["label"] for row in rows] + ["AVG", "GEOMEAN"]
    pasta_values = ratios + [avg_ratio, geomean_ratio]
    baseline_values = [1.0 for _ in labels]

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 18,
            "axes.labelsize": 24,
            "xtick.labelsize": 21,
            "ytick.labelsize": 21,
            "legend.fontsize": 21,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )

    fig, ax = plt.subplots(figsize=(10.48, 4.31))
    x = list(range(len(labels)))
    width = 0.23
    baseline_color = "#5DBCD2"
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

    ax.axhline(1.0, color="#2b2b2b", linestyle="--", linewidth=2.0)
    ax.grid(axis="y", color="#d6d6d6", linewidth=1.0, alpha=0.7)
    ax.set_axisbelow(True)
    ax.set_ylabel("Norm. downstream", labelpad=22)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=27, ha="center", rotation_mode="anchor")
    ax.set_ylim(0, max(1.08, max(pasta_values) * 1.12))
    ax.set_yticks([0, 0.5, 1.0])
    ax.set_yticklabels(["0", "0.5", "1"])
    ax.tick_params(axis="x", length=0, pad=0)
    ax.tick_params(axis="y", width=2, length=7, pad=8)
    for spine in ax.spines.values():
        spine.set_linewidth(2.1)
        spine.set_color("black")

    separator_x = len(rows) - 0.5
    ax.axvline(separator_x, color="#8b8b8b", linestyle=":", linewidth=2.0)
    ax.set_xlim(-0.55, len(labels) - 0.05)

    handles = [
        Patch(facecolor=baseline_color, edgecolor="none", label="Baseline"),
        Patch(facecolor=pasta_color, edgecolor="none", label="PASTA"),
    ]
    ax.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.20),
        ncol=2,
        frameon=False,
        handlelength=0.9,
        columnspacing=18.0,
        borderaxespad=0.0,
    )

    fig.tight_layout(pad=0.6)
    png_path = path_prefix.with_suffix(".png")
    pdf_path = path_prefix.with_suffix(".pdf")
    fig.savefig(png_path, dpi=100)
    fig.savefig(pdf_path)
    return png_path, pdf_path


def main():
    args = parse_args()
    result_dir = args.result_dir.resolve()
    previous_result_dir = args.previous_result_dir.resolve()
    rows = build_rows(collect_counts(result_dir, previous_result_dir, args.summary))
    output_prefix = result_dir / args.output_prefix
    png_path, pdf_path = plot(output_prefix, rows)
    csv_path = output_prefix.with_suffix(".csv")
    write_csv(csv_path, rows)
    print(f"Wrote {png_path}")
    print(f"Wrote {pdf_path}")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
