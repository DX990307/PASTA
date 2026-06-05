import argparse
import concurrent.futures
from datetime import datetime
import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
TARGETS = [
    "400latency",
]

# These timing simulations are heavy. Keep parallelism conservative unless
# you're sure the machine can handle more concurrent runs.
MAX_WORKERS = 16

ALL_BENCHMARKS = [
    "bitonicsort",
    "relu",
    "spmv",
    "matrixmultiplication",
    "matrixtranspose",
    "fastwalshtransform",
    "fft",
    "kmeans",
    "im2col",
    "aes",
    "floydwarshall",
    "pagerank",
    "simpleconvolution",
    "fir",
]


# Configure which benchmarks to run for each target here.
# Use ["all"] to expand to every benchmark in ALL_BENCHMARKS.
BENCHMARKS_BY_TARGET = {
    "400latency": [
       "all"
    ],
    # "TLBSensitiveStudy": ["all"],
}

DEFAULT_BENCHMARK_FLAGS = [
    "-max-wg=76800",
    # "-max-wg=38400",
]

BASE_COMMON_FLAGS = [
    "-timing",
    "-num-memory-banks=16",
    "-bandwidth=48",
    "-switch-latency=32",
    "-magic-memory-copy",
    "-report-all",
]

DEFAULT_ADAPTIVE_LOW = 2
DEFAULT_ADAPTIVE_HIGH = 6
DEFAULT_ADAPTIVE_THRESHOLD_PAIRS = "0:2,1:4,2:6,2:8,4:12,4:16,8:24"
DEFAULT_MMUTLB_PTCL_RETURN_LATENCY = 80

PREFETCH_FLAGS = [
    "-mmutlb-prefetch",
    "-mmutlb-prefetch-admission=6",
    "-mmutlb-prefetch-max-learners=4",
    "-mmutlb-prefetch-lookahead=2",
    "-mmutlb-prefetch-max-candidates=4",
]

COALESCING_FLAGS = [
    "-mmu-walk-coalescing",
]

VPN_MSHR_BASELINE_FLAGS = [
    "-gmmu-vpn-mshr-baseline",
    "-mmutlb-vpn-mshr-baseline",
    "-gmmu-initial-ptcl-mode=false",
    "-gmmu-ptcl-threshold-low=0",
    "-gmmu-ptcl-threshold-high=1000000",
    "-mmutlb-demand-pte-only",
]

output_dir = ""
stdout_lock = threading.Lock()



def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only-config",
        dest="only_config",
        default="",
        help="Only run experiments with this ablation config name (e.g. 'camsat').",
    )
    parser.add_argument(
        "--rerun-missing",
        dest="rerun_missing",
        default="",
        help="Reuse an existing results directory and rerun only experiments whose metrics CSV is missing.",
    )
    parser.add_argument(
        "--max-workers",
        dest="max_workers",
        type=int,
        default=MAX_WORKERS,
        help="Maximum number of concurrent experiments to launch.",
    )
    parser.add_argument(
        "--adaptive-threshold-low",
        dest="adaptive_threshold_low",
        type=int,
        default=DEFAULT_ADAPTIVE_LOW,
        help="Default adaptive low threshold used in non-scan mode.",
    )
    parser.add_argument(
        "--adaptive-threshold-high",
        dest="adaptive_threshold_high",
        type=int,
        default=DEFAULT_ADAPTIVE_HIGH,
        help="Default adaptive high threshold used in non-scan mode.",
    )
    parser.add_argument(
        "--adaptive-threshold-scan",
        dest="adaptive_threshold_scan",
        action="store_true",
        help="Scan adaptive thresholds with PTCL adaptation, prefetching, and MMU coalescing all enabled.",
    )
    parser.add_argument(
        "--adaptive-threshold-pairs",
        dest="adaptive_threshold_pairs",
        default=DEFAULT_ADAPTIVE_THRESHOLD_PAIRS,
        help='Comma-separated low:high pairs, for example "2:6,4:12,20:60".',
    )
    parser.add_argument(
        "--mmutlb-ptcl-return-latency",
        dest="mmutlb_ptcl_return_latency",
        type=int,
        default=DEFAULT_MMUTLB_PTCL_RETURN_LATENCY,
        help="Fixed MMUTLB/IOTLB lookup latency per requested PTE (per bitmap bit), in cycles, applied before each buffered translation request is looked up.",
    )
    return parser.parse_args()


def parse_threshold_pairs(raw_pairs):
    pairs = []
    for token in raw_pairs.split(","):
        token = token.strip()
        if not token:
            continue

        if ":" not in token:
            raise ValueError(f"invalid threshold pair '{token}', expected low:high")

        low_s, high_s = token.split(":", 1)
        low = int(low_s.strip())
        high = int(high_s.strip())
        if low > high:
            low, high = high, low
        pairs.append((low, high))

    if not pairs:
        raise ValueError("no valid adaptive threshold pairs provided")

    return pairs


def adaptive_flags(low, high):
    return [
        "-gmmu-initial-ptcl-mode=true",
        f"-gmmu-ptcl-threshold-low={low}",
        f"-gmmu-ptcl-threshold-high={high}",
    ]


def build_common_flags(args):
    return BASE_COMMON_FLAGS + [
        f"-mmutlb-ptcl-return-latency={args.mmutlb_ptcl_return_latency}",
    ]


def build_ablation_configs(args):
    if args.adaptive_threshold_scan:
        threshold_pairs = parse_threshold_pairs(args.adaptive_threshold_pairs)
        return [
            (
                f"adaptive_l{low}_h{high}_camsat",
                adaptive_flags(low, high) + COALESCING_FLAGS + PREFETCH_FLAGS,
            )
            for low, high in threshold_pairs
        ]

    low = args.adaptive_threshold_low
    high = args.adaptive_threshold_high
    if low > high:
        low, high = high, low

    return [
        (
            "baseline",
            VPN_MSHR_BASELINE_FLAGS,
        ),
        (
            "ptcl_mode",
            adaptive_flags(low, high),
        ),
        (
            "prefetching",
            VPN_MSHR_BASELINE_FLAGS + PREFETCH_FLAGS,
        ),
        (
            "coalescing",
            VPN_MSHR_BASELINE_FLAGS + COALESCING_FLAGS,
        ),
        (
            "camsat",
            adaptive_flags(low, high) + COALESCING_FLAGS + PREFETCH_FLAGS,
        ),
    ]


def get_benchmarks_for_target(target):
    selected = BENCHMARKS_BY_TARGET.get(target, ["all"])
    if "all" in selected:
        return ALL_BENCHMARKS[:]

    unknown = sorted(set(selected) - set(ALL_BENCHMARKS))
    if unknown:
        raise ValueError(f"unknown benchmarks for {target}: {unknown}")

    return selected


def make_exps(ablation_configs):
    exps = []
    for target in TARGETS:
        for benchmark in get_benchmarks_for_target(target):
            for config_name, config_flags in ablation_configs:
                exps.append(
                    {
                        "target": target,
                        "benchmark": benchmark,
                        "config_name": config_name,
                        "flags": DEFAULT_BENCHMARK_FLAGS + config_flags,
                    }
                )
    return exps


def filter_missing_metric_exps(exps, results_dir):
    missing = []
    missing_dir = Path(results_dir)
    for exp in exps:
        stem = (
            f'{exp["target"]}_{exp["benchmark"]}_{exp["config_name"]}'
        )
        metrics_csv = missing_dir / f"{stem}_metrics.csv"
        if not metrics_csv.exists():
            missing.append(exp)

    return missing


def build_env():
    env = os.environ.copy()
    env.setdefault("GOCACHE", "/tmp/gocache")
    return env


def build_targets(exps):
    env = build_env()
    targets = sorted({exp["target"] for exp in exps})

    for target in targets:
        target_dir = os.path.join(ROOT_DIR, target)
        print(f"Building {target} in {target_dir}")
        process = subprocess.Popen(["go", "build"], cwd=target_dir, env=env)
        process.wait()
        if process.returncode != 0:
            raise RuntimeError(f"failed to build {target}")


def exp_file_stem(exp):
    return os.path.join(
        output_dir,
        f'{exp["target"]}_{exp["benchmark"]}_{exp["config_name"]}',
    )


def run_exp(exp):
    binary = os.path.join(ROOT_DIR, exp["target"], exp["target"])
    file_stem = exp_file_stem(exp)
    metric_file_name = f"{file_stem}_metrics"

    cmd = [
        binary,
        f'-benchmark={exp["benchmark"]}',
        *exp["common_flags"],
        *exp["flags"],
        f"-metric-file-name={metric_file_name}",
    ]
    cmd_str = shlex.join(cmd)
    with stdout_lock:
        print(cmd_str, flush=True)

    out_file_name = f"{file_stem}_out.stdout"
    with open(out_file_name, "w") as out_file:
        out_file.write(f"Executing {cmd_str}\n")
        start_time = datetime.now()
        out_file.write(f"Start time: {start_time}\n")
        out_file.flush()

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=ROOT_DIR,
            text=True,
            bufsize=1,
        )

        assert process.stdout is not None
        for line in process.stdout:
            out_file.write(line)
            out_file.flush()

        process.wait()

        end_time = datetime.now()
        out_file.write(f"Return code: {process.returncode}\n")
        out_file.write(f"End time: {end_time}\n")
        out_file.write(f"Elapsed time: {end_time - start_time}\n")

    if process.returncode != 0:
        print(f"Error executing {cmd_str}")
        return {"exp": exp, "returncode": process.returncode}

    metrics_csv = metric_file_name + ".csv"
    if not os.path.exists(metrics_csv):
        print(f"Missing metrics file for {cmd_str}: {metrics_csv}")
        return {"exp": exp, "returncode": -1, "missing_metrics": metrics_csv}

    print(f"Executed {cmd_str}, time {end_time - start_time}")
    return {"exp": exp, "returncode": 0}


def create_output_dir():
    global output_dir
    output_dir = os.path.join(
        ROOT_DIR,
        "results",
        datetime.now().strftime("%Y-%m-%d-%H-%M-%S-ptcl-prefetch-sweep"),
    )

    results_dir = os.path.join(ROOT_DIR, "results")
    if not os.path.exists(results_dir):
        os.makedirs(results_dir)

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)


def main():
    global output_dir

    args = parse_args()
    common_flags = build_common_flags(args)
    ablation_configs = build_ablation_configs(args)
    if args.only_config:
        ablation_configs = [c for c in ablation_configs if c[0] == args.only_config]
        if not ablation_configs:
            raise ValueError(f"no ablation config named '{args.only_config}'")
    exps = make_exps(ablation_configs)
    if not exps:
        print("No experiments configured.")
        return

    if args.rerun_missing:
        output_dir = os.path.abspath(args.rerun_missing)
        if not os.path.isdir(output_dir):
            raise ValueError(f"results directory does not exist: {output_dir}")
        exps = filter_missing_metric_exps(exps, output_dir)
        if not exps:
            print(f"No missing-metrics experiments found in {output_dir}")
            return
        print(
            f"Rerunning {len(exps)} experiments with missing metrics in {output_dir}"
        )
    else:
        create_output_dir()

    build_targets(exps)

    if args.max_workers <= 0:
        raise ValueError("MAX_WORKERS must be greater than 0")

    max_workers = min(args.max_workers, len(exps))
    print(f"Using common flags: {shlex.join(common_flags)}")
    print(f"Launching {len(exps)} experiments with max_workers={max_workers}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        for exp in exps:
            exp["common_flags"] = common_flags
        futures = [executor.submit(run_exp, exp) for exp in exps]
        for future in concurrent.futures.as_completed(futures):
            print(future.result())


if __name__ == "__main__":
    main()
