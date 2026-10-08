#!/usr/bin/env python3
import argparse
import csv
from pathlib import Path


PROFILE_SUFFIX = "_ptcl_mshr_lifetime_profile_metrics.csv"
LIFETIME_BUCKETS = [
    "le_32",
    "le_64",
    "le_128",
    "le_256",
    "le_512",
    "le_1024",
    "le_2048",
    "le_4096",
    "gt_4096",
]


def read_profile(path):
    components = {}
    line_sizes = set()
    enabled = False

    with path.open(newline="", encoding="utf-8") as csv_file:
        rows = csv.reader(csv_file)
        header = [column.strip() for column in next(rows)]
        where_index = header.index("where")
        what_index = header.index("what")
        value_index = header.index("value")
        for row in rows:
            where = row[where_index].strip()
            what = row[what_index].strip()
            value = float(row[value_index])
            if what == "ptcl_line_size":
                line_sizes.add(int(value))
            if not what.startswith("ptcl_mshr_"):
                continue
            component = components.setdefault(where, {})
            component[what] = value
            if what == "ptcl_mshr_lifetime_profile_enabled" and value > 0:
                enabled = True

    if len(line_sizes) != 1:
        raise ValueError(f"inconsistent PTCL line sizes in {path}: {line_sizes}")

    demand_histogram = [0.0] * 9
    lifetime_by_demand = [0.0] * 9
    lifetime_histogram = [0.0] * len(LIFETIME_BUCKETS)
    completed = 0.0
    lifetime_sum = 0.0
    lifetime_minima = []
    lifetime_max = 0.0
    arrival_span_sum = 0.0
    arrival_span_max = 0.0
    demand_sum = 0.0

    for component in components.values():
        count = component.get("ptcl_mshr_completed_entries", 0.0)
        completed += count
        lifetime_sum += component.get("ptcl_mshr_lifetime_cycle_sum", 0.0)
        lifetime_max = max(
            lifetime_max,
            component.get("ptcl_mshr_lifetime_cycle_max", 0.0),
        )
        if count > 0:
            lifetime_minima.append(
                component.get("ptcl_mshr_lifetime_cycle_min", 0.0)
            )
        arrival_span_sum += component.get(
            "ptcl_mshr_arrival_span_cycle_sum", 0.0
        )
        arrival_span_max = max(
            arrival_span_max,
            component.get("ptcl_mshr_arrival_span_cycle_max", 0.0),
        )
        demand_sum += component.get("ptcl_mshr_demand_pte_sum", 0.0)
        for demand in range(9):
            demand_histogram[demand] += component.get(
                f"ptcl_mshr_demand_hist_{demand}", 0.0
            )
            lifetime_by_demand[demand] += component.get(
                f"ptcl_mshr_lifetime_cycles_demand_{demand}", 0.0
            )
        for bucket, bucket_name in enumerate(LIFETIME_BUCKETS):
            lifetime_histogram[bucket] += component.get(
                f"ptcl_mshr_lifetime_hist_{bucket_name}", 0.0
            )

    return {
        "enabled": enabled,
        "line_size": line_sizes.pop(),
        "completed": completed,
        "lifetime_sum": lifetime_sum,
        "lifetime_min": min(lifetime_minima) if lifetime_minima else 0.0,
        "lifetime_max": lifetime_max,
        "arrival_span_sum": arrival_span_sum,
        "arrival_span_max": arrival_span_max,
        "demand_sum": demand_sum,
        "demand_histogram": demand_histogram,
        "lifetime_by_demand": lifetime_by_demand,
        "lifetime_histogram": lifetime_histogram,
    }


def collect(result_dir):
    records = []
    for path in sorted(result_dir.glob(f"400latency_*{PROFILE_SUFFIX}")):
        benchmark = path.name[len("400latency_") : -len(PROFILE_SUFFIX)]
        profile = read_profile(path)
        entries = profile["completed"]
        if not profile["enabled"] or entries <= 0:
            continue
        demand_histogram = profile["demand_histogram"]
        line_size = profile["line_size"]
        records.append(
            {
                "benchmark": benchmark,
                **profile,
                "avg_lifetime": profile["lifetime_sum"] / entries,
                "avg_arrival_span": profile["arrival_span_sum"] / entries,
                "avg_demand": profile["demand_sum"] / entries,
                "multi": sum(demand_histogram[2:]) / entries,
                "full": demand_histogram[line_size] / entries,
            }
        )
    return records


def write_summary(result_dir, records):
    output_path = result_dir / "ptcl_mshr_lifetime_summary.csv"
    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(
            [
                "benchmark",
                "completed_entries",
                "avg_lifetime_cycles",
                "min_lifetime_cycles",
                "max_lifetime_cycles",
                "avg_first_to_last_arrival_cycles",
                "max_first_to_last_arrival_cycles",
                "avg_demand_pte",
                "multi_demand_fraction",
                "full_demand_fraction",
                *[f"demand_hist_{demand}" for demand in range(9)],
                *[f"lifetime_hist_{bucket}" for bucket in LIFETIME_BUCKETS],
                *[f"avg_lifetime_demand_{demand}" for demand in range(9)],
            ]
        )
        for record in records:
            avg_by_demand = []
            for demand in range(9):
                count = record["demand_histogram"][demand]
                total = record["lifetime_by_demand"][demand]
                avg_by_demand.append(total / count if count > 0 else 0.0)
            writer.writerow(
                [
                    record["benchmark"],
                    int(record["completed"]),
                    f'{record["avg_lifetime"]:.6f}',
                    int(record["lifetime_min"]),
                    int(record["lifetime_max"]),
                    f'{record["avg_arrival_span"]:.6f}',
                    int(record["arrival_span_max"]),
                    f'{record["avg_demand"]:.9f}',
                    f'{record["multi"]:.9f}',
                    f'{record["full"]:.9f}',
                    *[int(value) for value in record["demand_histogram"]],
                    *[int(value) for value in record["lifetime_histogram"]],
                    *[f"{value:.6f}" for value in avg_by_demand],
                ]
            )
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Aggregate real PTCL MSHR allocation-to-removal lifetimes."
    )
    parser.add_argument("result_dir", type=Path)
    args = parser.parse_args()

    records = collect(args.result_dir)
    if not records:
        raise SystemExit("no completed PTCL MSHR lifetime profile metrics found")

    print(
        "benchmark\tentries\tavg lifetime\tmin\tmax\tavg arrival span\t"
        "avg demand\tmulti\tfull\t>4096 cycles"
    )
    for record in records:
        over_4096 = record["lifetime_histogram"][-1] / record["completed"]
        print(
            f'{record["benchmark"]}\t{int(record["completed"])}\t'
            f'{record["avg_lifetime"]:.2f}\t{int(record["lifetime_min"])}\t'
            f'{int(record["lifetime_max"])}\t{record["avg_arrival_span"]:.2f}\t'
            f'{record["avg_demand"]:.4f}\t{record["multi"]:.2%}\t'
            f'{record["full"]:.2%}\t{over_4096:.2%}'
        )
    print(f"Wrote {write_summary(args.result_dir, records)}")


if __name__ == "__main__":
    main()
