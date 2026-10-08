#!/usr/bin/env python3
import argparse
import csv
from pathlib import Path


PROFILE_SUFFIX = "_ptcl_residency_profile_metrics.csv"


def read_profile(path):
    probes = 0.0
    resident_sum = 0.0
    histogram = [0.0] * 9
    window_cycles = set()
    window_count = 0.0
    demand_sum = 0.0
    demand_histogram = [0.0] * 9
    demand_resident_sum = 0.0
    line_sizes = set()

    with path.open(newline="", encoding="utf-8") as csv_file:
        rows = csv.reader(csv_file)
        header = [column.strip() for column in next(rows)]
        what_index = header.index("what")
        value_index = header.index("value")
        for row in rows:
            what = row[what_index].strip()
            value = float(row[value_index])
            if what == "ptcl_residency_probes":
                probes += value
            elif what == "ptcl_residency_resident_pte_sum":
                resident_sum += value
            elif what == "ptcl_line_size":
                line_sizes.add(int(value))
            elif what == "ptcl_residency_window_cycles":
                window_cycles.add(int(value))
            elif what == "ptcl_window_count":
                window_count += value
            elif what == "ptcl_window_demand_pte_sum":
                demand_sum += value
            elif what == "ptcl_window_demand_resident_pte_sum":
                demand_resident_sum += value
            elif what.startswith("ptcl_window_demand_hist_"):
                bucket = int(what.rsplit("_", 1)[1])
                demand_histogram[bucket] += value
            elif what.startswith("ptcl_residency_hist_"):
                bucket = int(what.rsplit("_", 1)[1])
                histogram[bucket] += value

    if len(line_sizes) != 1:
        raise ValueError(f"inconsistent PTCL line sizes in {path}: {line_sizes}")
    return {
        "probes": probes,
        "resident_sum": resident_sum,
        "histogram": histogram,
        "line_size": line_sizes.pop(),
        "window_cycles": window_cycles.pop() if len(window_cycles) == 1 else 0,
        "window_count": window_count,
        "demand_sum": demand_sum,
        "demand_histogram": demand_histogram,
        "demand_resident_sum": demand_resident_sum,
    }


def collect(result_dir):
    records = []
    pattern = f"400latency_*{PROFILE_SUFFIX}"
    for path in sorted(result_dir.glob(pattern)):
        benchmark = path.name[len("400latency_") : -len(PROFILE_SUFFIX)]
        profile = read_profile(path)
        probes = profile["probes"]
        if probes <= 0:
            continue
        histogram = profile["histogram"]
        line_size = profile["line_size"]
        windows = profile["window_count"]
        if windows <= 0:
            windows = probes
        demand_histogram = profile["demand_histogram"]
        demand_sum = profile["demand_sum"]
        records.append(
            {
                "benchmark": benchmark,
                "probes": probes,
                "avg": profile["resident_sum"] / probes,
                "nonzero": 1.0 - histogram[0] / probes,
                "full": histogram[line_size] / probes,
                "histogram": histogram,
                "window_cycles": profile["window_cycles"],
                "windows": windows,
                "avg_demand": demand_sum / windows if demand_sum > 0 else 0.0,
                "multi": sum(demand_histogram[2:]) / windows,
                "demand_ge4": sum(demand_histogram[4:]) / windows,
                "demand_full": demand_histogram[line_size] / windows,
                "demand_resident_fraction": (
                    profile["demand_resident_sum"] / demand_sum
                    if demand_sum > 0
                    else 0.0
                ),
                "demand_histogram": demand_histogram,
            }
        )
    return records


def write_summary(result_dir, records):
    output_path = result_dir / "ptcl_residency_summary.csv"
    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(
            [
                "benchmark",
                "probes",
                "avg_resident_pte",
                "nonzero_fraction",
                "full_fraction",
                "window_cycles",
                "window_count",
                "avg_window_demand_pte",
                "multi_demand_fraction",
                "demand_ge4_fraction",
                "full_demand_fraction",
                "demand_resident_fraction",
                *[f"hist_{bucket}" for bucket in range(9)],
                *[f"demand_hist_{bucket}" for bucket in range(9)],
            ]
        )
        for record in records:
            writer.writerow(
                [
                    record["benchmark"],
                    int(record["probes"]),
                    f'{record["avg"]:.9f}',
                    f'{record["nonzero"]:.9f}',
                    f'{record["full"]:.9f}',
                    record["window_cycles"],
                    int(record["windows"]),
                    f'{record["avg_demand"]:.9f}',
                    f'{record["multi"]:.9f}',
                    f'{record["demand_ge4"]:.9f}',
                    f'{record["demand_full"]:.9f}',
                    f'{record["demand_resident_fraction"]:.9f}',
                    *[int(value) for value in record["histogram"]],
                    *[int(value) for value in record["demand_histogram"]],
                ]
            )
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Aggregate demand-weighted L2 PTCL residency metrics."
    )
    parser.add_argument("result_dir", type=Path)
    args = parser.parse_args()

    records = collect(args.result_dir)
    if not records:
        raise SystemExit("no PTCL residency profile metrics found")

    if any(record["window_cycles"] > 0 for record in records):
        print(
            "benchmark\twindows\tcycles\tavg demand\tmulti\t>=4\tfull\t"
            "avg resident at open\tdemand already resident"
        )
        for record in records:
            print(
                f'{record["benchmark"]}\t{int(record["windows"])}\t'
                f'{record["window_cycles"]}\t{record["avg_demand"]:.4f}\t'
                f'{record["multi"]:.2%}\t{record["demand_ge4"]:.2%}\t'
                f'{record["demand_full"]:.2%}\t{record["avg"]:.4f}\t'
                f'{record["demand_resident_fraction"]:.2%}'
            )
    else:
        print("benchmark\tprobes\tavg resident PTE\tnonzero\tfull")
        for record in records:
            print(
                f'{record["benchmark"]}\t{int(record["probes"])}\t'
                f'{record["avg"]:.4f}\t{record["nonzero"]:.2%}\t'
                f'{record["full"]:.2%}'
            )
    print(f"Wrote {write_summary(args.result_dir, records)}")


if __name__ == "__main__":
    main()
