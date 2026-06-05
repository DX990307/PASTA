#!/usr/bin/env python3
import argparse
import csv
import re
from pathlib import Path

SUMMARY_RE = re.compile(
    r"^\[PF\]\[summary\] component=(?P<component>\S+) enqueued=(?P<enqueued>\d+) completed=(?P<completed>\d+) useful=(?P<useful>\d+) late=(?P<late>\d+) lost_before_use=(?P<lost_before_use>\d+) useful_rate_enqueued=(?P<useful_rate_enqueued>[-+0-9.eE]+) useful_rate_completed=(?P<useful_rate_completed>[-+0-9.eE]+) no_clear_pattern_skips=(?P<no_clear_pattern_skips>\d+)$"
)
BLOCK_RE = re.compile(
    r"^\[PF\]\[bo-summary\] component=(?P<component>\S+) page_block=(?P<page_block>\d+) enqueued=(?P<enqueued>\d+) completed=(?P<completed>\d+) useful=(?P<useful>\d+) late=(?P<late>\d+) lost_before_use=(?P<lost_before_use>\d+) useful_rate_enqueued=(?P<useful_rate_enqueued>[-+0-9.eE]+) useful_rate_completed=(?P<useful_rate_completed>[-+0-9.eE]+)$"
)
GPU_SUMMARY_RE = re.compile(
    r"^\[PF\]\[gpu-summary\] component=(?P<component>\S+) inserted=(?P<inserted>\d+) useful=(?P<useful>\d+) useful_hit=(?P<useful_hit>\d+) late_useful=(?P<late_useful>\d+) unused=(?P<unused>\d+) resident_pending=(?P<resident_pending>\d+) useful_rate=(?P<useful_rate>[-+0-9.eE]+) unused_rate=(?P<unused_rate>[-+0-9.eE]+)$"
)
GPU_BLOCK_RE = re.compile(
    r"^\[PF\]\[gpu-bo-summary\] component=(?P<component>\S+) page_block=(?P<page_block>\d+) inserted=(?P<inserted>\d+) useful=(?P<useful>\d+) useful_hit=(?P<useful_hit>\d+) late_useful=(?P<late_useful>\d+) unused=(?P<unused>\d+) resident_pending=(?P<resident_pending>\d+) useful_rate=(?P<useful_rate>[-+0-9.eE]+) unused_rate=(?P<unused_rate>[-+0-9.eE]+)$"
)
GPU_UNUSED_PTCL_RANGE_RE = re.compile(
    r"^\[PF\]\[gpu-unused-ptcl-range\] component=(?P<component>\S+) page_block=(?P<page_block>\d+) ptcl_start=(?P<ptcl_start>\d+) ptcl_end=(?P<ptcl_end>\d+) unique_ptcls=(?P<unique_ptcls>\d+) total_unused=(?P<total_unused>\d+)$"
)

def parse_args():
    parser = argparse.ArgumentParser(
        description="Summarize MMUTLB prefetch outcomes from a results directory."
    )
    parser.add_argument(
        "results_dir",
        type=Path,
        help="Directory containing *_metrics.csv and *_out.stdout files.",
    )
    parser.add_argument(
        "--run-output",
        type=Path,
        default=None,
        help="Output CSV for one-row-per-run summary. Default: <results_dir>/prefetch_outcomes_runs.csv",
    )
    parser.add_argument(
        "--block-output",
        type=Path,
        default=None,
        help="Output CSV for one-row-per-run-per-page-block summary. Default: <results_dir>/prefetch_outcomes_blocks.csv",
    )
    parser.add_argument(
        "--unused-range-output",
        type=Path,
        default=None,
        help="Output CSV for unused PTCL ranges parsed from stdout. Default: <results_dir>/prefetch_unused_ptcl_ranges.csv",
    )
    return parser.parse_args()

def load_metrics(metrics_path: Path):
    metrics = {}
    with metrics_path.open(newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) < 4:
                continue
            where = row[1].strip()
            what = row[2].strip()
            value = row[3].strip()
            try:
                metrics[(where, what)] = float(value)
            except ValueError:
                continue
    return metrics

def parse_stdout(stdout_path: Path):
    summary = None
    blocks = []
    gpu_summaries = []
    gpu_blocks = []
    gpu_unused_ranges = []
    if not stdout_path.exists():
        return summary, blocks, gpu_summaries, gpu_blocks, gpu_unused_ranges

    with stdout_path.open() as f:
        for line in f:
            line = line.strip()
            m = SUMMARY_RE.match(line)
            if m:
                summary = m.groupdict()
                continue
            m = BLOCK_RE.match(line)
            if m:
                blocks.append(m.groupdict())
                continue
            m = GPU_SUMMARY_RE.match(line)
            if m:
                gpu_summaries.append(m.groupdict())
                continue
            m = GPU_BLOCK_RE.match(line)
            if m:
                gpu_blocks.append(m.groupdict())
                continue
            m = GPU_UNUSED_PTCL_RANGE_RE.match(line)
            if m:
                gpu_unused_ranges.append(m.groupdict())
    return summary, blocks, gpu_summaries, gpu_blocks, gpu_unused_ranges

def parse_experiment_name(stem: str):
    parts = stem.split("_")
    benchmark = parts[1] if len(parts) > 1 else ""
    mode = "_".join(parts[2:]) if len(parts) > 2 else ""
    return benchmark, mode

def run_summary_row(metrics_path: Path):
    stem = metrics_path.name.removesuffix("_metrics.csv")
    benchmark, mode = parse_experiment_name(stem)
    metrics = load_metrics(metrics_path)
    stdout_path = metrics_path.with_name(stem + "_out.stdout")
    summary, _, gpu_summaries, _, _ = parse_stdout(stdout_path)

    def metric(where, what, default=0.0):
        return metrics.get((where, what), default)

    gmmu_components = sorted({where for (where, _) in metrics.keys() if where.endswith(".GMMUCache")})

    def summed_metric(what):
        return sum(metric(component, what, 0.0) for component in gmmu_components)

    row = {
        "experiment": stem,
        "benchmark": benchmark,
        "mode": mode,
        "total_time": metric("Driver", "total_time"),
        "incoming_req_count": metric("IOMMUTLB", "incoming_req_count"),
        "req_to_mmu_count": metric("IOMMUTLB", "req_to_mmu_count"),
        "prefetch_generated_candidates": metric("IOMMUTLB", "prefetch_generated_candidates"),
        "prefetch_enqueued_candidates": metric("IOMMUTLB", "prefetch_enqueued_candidates"),
        "prefetch_completed_fills": metric("IOMMUTLB", "prefetch_completed_fills"),
        "prefetch_useful_hits": metric("IOMMUTLB", "prefetch_useful_hits"),
        "prefetch_late_demands": metric("IOMMUTLB", "prefetch_late_demands"),
        "prefetch_lost_before_use": metric("IOMMUTLB", "prefetch_lost_before_use"),
        "prefetch_useful_rate_by_enqueued": metric("IOMMUTLB", "prefetch_useful_rate_by_enqueued"),
        "prefetch_useful_rate_by_completed": metric("IOMMUTLB", "prefetch_useful_rate_by_completed"),
        "prefetch_rejected_by_prefix_filter": metric("IOMMUTLB", "prefetch_rejected_by_prefix_filter"),
        "prefetch_rejected_by_duplicate_filter": metric("IOMMUTLB", "prefetch_rejected_by_duplicate_filter"),
        "prefetch_rejected_by_invalid_target": metric("IOMMUTLB", "prefetch_rejected_by_invalid_target"),
        "prefetch_no_clear_pattern_skips": metric("IOMMUTLB", "prefetch_no_clear_pattern_skips"),
        "mmu_req_average_latency": metric("MMU", "req_average_latency"),
        "gmmucache_prefetch_exact_inserted": summed_metric("prefetch_exact_inserted"),
        "gmmucache_prefetch_exact_useful": summed_metric("prefetch_exact_useful"),
        "gmmucache_prefetch_exact_useful_hit": summed_metric("prefetch_exact_useful_hit"),
        "gmmucache_prefetch_exact_late_useful": summed_metric("prefetch_exact_late_useful"),
        "gmmucache_prefetch_exact_unused": summed_metric("prefetch_exact_unused"),
        "gmmucache_prefetch_exact_resident_pending": summed_metric("prefetch_exact_resident_pending"),
        "gmmucache_prefetch_exact_useful_rate": 0.0,
        "gmmucache_prefetch_exact_unused_rate": 0.0,
    }

    if row["gmmucache_prefetch_exact_inserted"] > 0:
        row["gmmucache_prefetch_exact_useful_rate"] = row["gmmucache_prefetch_exact_useful"] / row["gmmucache_prefetch_exact_inserted"]
        row["gmmucache_prefetch_exact_unused_rate"] = row["gmmucache_prefetch_exact_unused"] / row["gmmucache_prefetch_exact_inserted"]

    if summary is not None:
        row.update({
            "log_summary_enqueued": int(summary["enqueued"]),
            "log_summary_completed": int(summary["completed"]),
            "log_summary_useful": int(summary["useful"]),
            "log_summary_late": int(summary["late"]),
            "log_summary_lost_before_use": int(summary["lost_before_use"]),
            "log_summary_useful_rate_enqueued": float(summary["useful_rate_enqueued"]),
            "log_summary_useful_rate_completed": float(summary["useful_rate_completed"]),
            "log_summary_no_clear_pattern_skips": int(summary["no_clear_pattern_skips"]),
        })

    if gpu_summaries:
        row.update({
            "log_gpu_prefetch_exact_inserted": sum(int(item["inserted"]) for item in gpu_summaries),
            "log_gpu_prefetch_exact_useful": sum(int(item["useful"]) for item in gpu_summaries),
            "log_gpu_prefetch_exact_useful_hit": sum(int(item["useful_hit"]) for item in gpu_summaries),
            "log_gpu_prefetch_exact_late_useful": sum(int(item["late_useful"]) for item in gpu_summaries),
            "log_gpu_prefetch_exact_unused": sum(int(item["unused"]) for item in gpu_summaries),
            "log_gpu_prefetch_exact_resident_pending": sum(int(item["resident_pending"]) for item in gpu_summaries),
        })
    return row, summary, stdout_path

def block_rows(metrics_path: Path, stdout_path: Path):
    stem = metrics_path.name.removesuffix("_metrics.csv")
    benchmark, mode = parse_experiment_name(stem)
    _, blocks, _, gpu_blocks, _ = parse_stdout(stdout_path)
    rows = []
    for block in blocks:
        rows.append({
            "scope": "iommutlb_proxy",
            "component": block["component"],
            "experiment": stem,
            "benchmark": benchmark,
            "mode": mode,
            "page_block": int(block["page_block"]),
            "enqueued": int(block["enqueued"]),
            "completed": int(block["completed"]),
            "useful": int(block["useful"]),
            "late": int(block["late"]),
            "lost_before_use": int(block["lost_before_use"]),
            "useful_rate_enqueued": float(block["useful_rate_enqueued"]),
            "useful_rate_completed": float(block["useful_rate_completed"]),
        })
    for block in gpu_blocks:
        rows.append({
            "scope": "gmmucache_exact",
            "component": block["component"],
            "experiment": stem,
            "benchmark": benchmark,
            "mode": mode,
            "page_block": int(block["page_block"]),
            "inserted": int(block["inserted"]),
            "useful": int(block["useful"]),
            "useful_hit": int(block["useful_hit"]),
            "late_useful": int(block["late_useful"]),
            "unused": int(block["unused"]),
            "resident_pending": int(block["resident_pending"]),
            "useful_rate": float(block["useful_rate"]),
            "unused_rate": float(block["unused_rate"]),
        })
    return rows

def unused_range_rows(stdout_path: Path):
    stem = stdout_path.name.removesuffix("_out.stdout")
    benchmark, mode = parse_experiment_name(stem)
    _, _, _, _, gpu_unused_ranges = parse_stdout(stdout_path)
    rows = []
    for entry in gpu_unused_ranges:
        rows.append({
            "experiment": stem,
            "benchmark": benchmark,
            "mode": mode,
            "component": entry["component"],
            "page_block": int(entry["page_block"]),
            "ptcl_start": int(entry["ptcl_start"]),
            "ptcl_end": int(entry["ptcl_end"]),
            "unique_ptcls": int(entry["unique_ptcls"]),
            "total_unused": int(entry["total_unused"]),
        })
    return rows

def write_csv(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

def main():
    args = parse_args()
    results_dir = args.results_dir
    run_output = args.run_output or (results_dir / "prefetch_outcomes_runs.csv")
    block_output = args.block_output or (results_dir / "prefetch_outcomes_blocks.csv")
    unused_range_output = args.unused_range_output or (results_dir / "prefetch_unused_ptcl_ranges.csv")

    run_rows = []
    block_rows_all = []
    for metrics_path in sorted(results_dir.glob("*_metrics.csv")):
        run_row, _, stdout_path = run_summary_row(metrics_path)
        run_rows.append(run_row)
        block_rows_all.extend(block_rows(metrics_path, stdout_path))

    unused_range_rows_all = []
    for stdout_path in sorted(results_dir.glob("*_out.stdout")):
        unused_range_rows_all.extend(unused_range_rows(stdout_path))

    write_csv(run_output, run_rows)
    write_csv(block_output, block_rows_all)
    write_csv(unused_range_output, unused_range_rows_all)

    print(f"Wrote run summary: {run_output}")
    print(f"Wrote block summary: {block_output}")
    print(f"Wrote unused PTCL ranges: {unused_range_output}")

if __name__ == "__main__":
    main()
