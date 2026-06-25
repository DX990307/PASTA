#!/usr/bin/env python3
"""Summarize L2 TLB MSHR coalescing from *_metrics.csv files."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


CONFIG_SUFFIXES = sorted(
    [
        "baseline",
        "ptcl_mode_flex_iommu_assist",
        "idle_iommu_assist",
        "flex_entry",
        "ptcl_mode",
    ],
    key=len,
    reverse=True,
)

SELECTED_BENCHMARKS = [
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
    "matrixtranspose": "MT",
    "pagerank": "PR",
    "relu": "RELU",
    "simpleconvolution": "SC",
    "spmv": "SPMV",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize GPU L2TLB MSHR coalescing rates."
    )
    parser.add_argument(
        "result_dir",
        type=Path,
        help="Directory containing 400latency_*_metrics.csv files.",
    )
    parser.add_argument(
        "--config",
        default="ptcl_mode",
        help="Configuration suffix to summarize. Default: ptcl_mode.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output CSV path. Default: <result_dir>/mshr_coalescing_rate_<config>.csv.",
    )
    return parser.parse_args()


def parse_benchmark_config(path: Path) -> tuple[str, str]:
    name = path.name.removesuffix("_metrics.csv")
    if name.startswith("400latency_"):
        name = name[len("400latency_") :]

    for suffix in CONFIG_SUFFIXES:
        marker = "_" + suffix
        if name.endswith(marker):
            return name[: -len(marker)], suffix

    parts = name.rsplit("_", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return name, "unknown"


def load_l2tlb_counts(path: Path) -> dict[str, float]:
    counts = {
        "hit": 0.0,
        "miss": 0.0,
        "mshr-hit": 0.0,
    }
    with path.open(newline="", encoding="utf-8") as handle:
        for raw_row in csv.reader(handle):
            if len(raw_row) < 4:
                continue
            where = raw_row[1].strip()
            what = raw_row[2].strip()
            if not (where.startswith("GPU[") and where.endswith("].L2TLB")):
                continue
            if what in counts:
                counts[what] += float(raw_row[3])
    return counts


def rate(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def make_row(benchmark: str, label: str, counts: dict[str, float]) -> dict[str, str]:
    hit = counts["hit"]
    miss = counts["miss"]
    mshr_hit = counts["mshr-hit"]
    miss_side = miss + mshr_hit
    total = hit + miss_side
    return {
        "benchmark": benchmark,
        "label": label,
        "l2tlb_hit": f"{hit:.0f}",
        "l2tlb_miss": f"{miss:.0f}",
        "l2tlb_mshr_hit": f"{mshr_hit:.0f}",
        "miss_coalescing_rate": f"{rate(mshr_hit, miss_side):.6f}",
        "miss_coalescing_percent": f"{100.0 * rate(mshr_hit, miss_side):.2f}",
        "overall_mshr_hit_share": f"{rate(mshr_hit, total):.6f}",
        "overall_mshr_hit_percent": f"{100.0 * rate(mshr_hit, total):.2f}",
    }


def summarize(result_dir: Path, config: str) -> list[dict[str, str]]:
    by_benchmark: dict[str, dict[str, float]] = {}
    for path in sorted(result_dir.glob("*_metrics.csv")):
        benchmark, path_config = parse_benchmark_config(path)
        if path_config != config:
            continue
        if benchmark not in SELECTED_BENCHMARKS:
            continue
        by_benchmark[benchmark] = load_l2tlb_counts(path)

    rows: list[dict[str, str]] = []
    totals = {"hit": 0.0, "miss": 0.0, "mshr-hit": 0.0}
    for benchmark in SELECTED_BENCHMARKS:
        if benchmark not in by_benchmark:
            continue
        counts = by_benchmark[benchmark]
        for key, value in counts.items():
            totals[key] += value
        rows.append(make_row(benchmark, LABELS[benchmark], counts))

    if rows:
        rows.append(make_row("TOTAL", "TOTAL", totals))
    return rows


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def print_table(rows: list[dict[str, str]]) -> None:
    if not rows:
        print("No matching rows found.")
        return
    columns = [
        "label",
        "l2tlb_miss",
        "l2tlb_mshr_hit",
        "miss_coalescing_percent",
        "overall_mshr_hit_percent",
    ]
    widths = {
        column: max(len(column), *(len(row[column]) for row in rows))
        for column in columns
    }
    print("  ".join(column.ljust(widths[column]) for column in columns))
    print("  ".join("-" * widths[column] for column in columns))
    for row in rows:
        print("  ".join(row[column].rjust(widths[column]) for column in columns))


def main() -> None:
    args = parse_args()
    rows = summarize(args.result_dir, args.config)
    if not rows:
        print("No matching rows found.")
        return

    output = args.output
    if output is None:
        output = args.result_dir / f"mshr_coalescing_rate_{args.config}.csv"
    write_csv(output, rows)
    print(f"Wrote {output}")
    print_table(rows)


if __name__ == "__main__":
    main()
