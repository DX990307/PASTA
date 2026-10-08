#!/usr/bin/env python3
import argparse
import csv
import math
from pathlib import Path


BASELINE_SUFFIX = "_baseline_metrics.csv"
IDEAL_SUFFIX = "_ideal_translation_metrics.csv"
PROFILE_SUFFIX = "_ptcl_residency_profile_metrics.csv"


def read_metric(path, target_where, target_what):
    with path.open(newline="", encoding="utf-8") as csv_file:
        rows = csv.reader(csv_file)
        header = [column.strip() for column in next(rows)]
        where_index = header.index("where")
        what_index = header.index("what")
        value_index = header.index("value")
        for row in rows:
            if (
                row[where_index].strip() == target_where
                and row[what_index].strip() == target_what
            ):
                return float(row[value_index])
    raise ValueError(f"missing {target_where}/{target_what} in {path}")


def collect(result_dir):
    records = []
    pattern = f"400latency_*{IDEAL_SUFFIX}"
    for ideal_path in sorted(result_dir.glob(pattern)):
        stem = ideal_path.name
        benchmark = stem[len("400latency_") : -len(IDEAL_SUFFIX)]
        baseline_path = result_dir / f"400latency_{benchmark}{BASELINE_SUFFIX}"
        reference_config = "baseline"
        if not baseline_path.exists():
            baseline_path = result_dir / f"400latency_{benchmark}{PROFILE_SUFFIX}"
            reference_config = "ptcl_residency_profile"
        if not baseline_path.exists():
            continue

        baseline_time = read_metric(baseline_path, "Driver", "total_time")
        ideal_time = read_metric(ideal_path, "Driver", "total_time")
        if baseline_time <= 0 or ideal_time <= 0:
            continue
        records.append(
            (
                benchmark,
                reference_config,
                baseline_time,
                ideal_time,
                baseline_time / ideal_time,
            )
        )
    return records


def write_summary(result_dir, records):
    output_path = result_dir / "ideal_translation_speedup_summary.csv"
    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(
            [
                "benchmark",
                "reference_config",
                "baseline_time",
                "ideal_time",
                "speedup",
                "improvement_percent",
            ]
        )
        for benchmark, reference, baseline_time, ideal_time, speedup in records:
            writer.writerow(
                [
                    benchmark,
                    reference,
                    baseline_time,
                    ideal_time,
                    speedup,
                    (speedup - 1.0) * 100.0,
                ]
            )
        if records:
            gmean = math.exp(
                sum(math.log(record[4]) for record in records) / len(records)
            )
            writer.writerow(
                [
                    "GMEAN",
                    "",
                    "",
                    "",
                    f"{gmean:.9f}",
                    f"{(gmean - 1) * 100:.6f}",
                ]
            )
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Compare baseline and ideal-address-translation runtime."
    )
    parser.add_argument("result_dir", type=Path)
    args = parser.parse_args()

    records = collect(args.result_dir)
    if not records:
        raise SystemExit("no matched baseline/ideal_translation metric pairs")

    print("benchmark\treference\tbaseline\tideal\tspeedup\timprovement")
    for benchmark, reference, baseline_time, ideal_time, speedup in records:
        print(
            f"{benchmark}\t{reference}\t{baseline_time:.12g}\t"
            f"{ideal_time:.12g}\t{speedup:.4f}x\t"
            f"{(speedup - 1) * 100:.2f}%"
        )
    gmean = math.exp(sum(math.log(record[4]) for record in records) / len(records))
    print(f"GMEAN\t\t\t\t{gmean:.4f}x\t{(gmean - 1) * 100:.2f}%")
    print(f"Wrote {write_summary(args.result_dir, records)}")


if __name__ == "__main__":
    main()
