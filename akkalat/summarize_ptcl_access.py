import argparse
import csv
from pathlib import Path


CONFIG_SUFFIXES = [
    "baseline_photon",
    "pasta_photon",
    "camsat_photon",
    "baseline",
    "pasta",
    "camsat",
    "photon",
    "ptcl_mode_flex_iommu_assist",
    "ptcl_mode",
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Summarize *_ptcl_access_summary.csv files."
    )
    parser.add_argument(
        "result_dir",
        type=Path,
        help="Directory containing *_ptcl_access_summary.csv files.",
    )
    parser.add_argument(
        "--scope",
        choices=["all", "cu", "both"],
        default="all",
        help="Rows to print from each summary file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional CSV output path. Defaults to stdout table only.",
    )
    return parser.parse_args()


def parse_benchmark_config(path):
    name = path.name.removesuffix("_ptcl_access_summary.csv")
    if name.endswith("_metrics"):
        name = name[: -len("_metrics")]
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


def load_rows(result_dir, scope):
    rows = []
    for path in sorted(result_dir.glob("*_ptcl_access_summary.csv")):
        benchmark, config = parse_benchmark_config(path)
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if scope != "both" and row["scope"] != scope:
                    continue
                out = {
                    "benchmark": benchmark,
                    "config": config,
                    "scope": row["scope"],
                    "component": row["component"],
                    "device_id": row["device_id"],
                    "pid": row["pid"],
                    "ptcl_line_size": row["ptcl_line_size"],
                    "log2_page_size": row["log2_page_size"],
                    "ptcl_line_count": row["ptcl_line_count"],
                    "total_accesses": row["total_accesses"],
                    "avg_used_pte_per_line": row["avg_used_pte_per_line"],
                    "p50_used_pte": row["p50_used_pte"],
                    "p90_used_pte": row["p90_used_pte"],
                    "p99_used_pte": row["p99_used_pte"],
                    "full_line_fraction": row["full_line_fraction"],
                    "single_pte_fraction": row["single_pte_fraction"],
                }
                rows.append(out)
    return rows


def write_csv(path, rows):
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def print_table(rows):
    columns = [
        "benchmark",
        "config",
        "scope",
        "component",
        "log2_page_size",
        "avg_used_pte_per_line",
        "p90_used_pte",
        "full_line_fraction",
        "single_pte_fraction",
        "ptcl_line_count",
    ]
    if not rows:
        print("No *_ptcl_access_summary.csv rows found.")
        return

    widths = {
        column: max(len(column), *(len(row[column]) for row in rows))
        for column in columns
    }
    header = "  ".join(column.ljust(widths[column]) for column in columns)
    print(header)
    print("  ".join("-" * widths[column] for column in columns))
    for row in rows:
        print("  ".join(row[column].ljust(widths[column]) for column in columns))


def main():
    args = parse_args()
    rows = load_rows(args.result_dir, args.scope)
    if args.output:
        write_csv(args.output, rows)
        print(f"Wrote {args.output}")
    print_table(rows)


if __name__ == "__main__":
    main()
