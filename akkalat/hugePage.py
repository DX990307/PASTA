import argparse
import concurrent.futures
from datetime import datetime
import os
from pathlib import Path
import shlex
import subprocess

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
TARGETS = [
    "400latency",
]

MAX_WORKERS = 16

ALL_BENCHMARKS = [
    "bitonicsort",
    "relu",
    "matrixmultiplication",
    "matrixtranspose",
    "kmeans",
    "spmv",
    "im2col",
    "aes",
    "fft",
    "floydwarshall",
    "pagerank",
    "simpleconvolution",
    "fastwalshtransform",
    "fir",
]

BENCHMARKS_BY_TARGET = {
    "400latency": ["all"],
}

DEFAULT_BENCHMARK_FLAGS = [
    "-max-wg=76800",
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
DEFAULT_MMUTLB_LOOKUP_LATENCY = 80
DEFAULT_GMMU_PTE_LOOKUP_LATENCY = 32
DEFAULT_CONFIGS = "baseline,camsat"
DEFAULT_PAGE_SIZES = "16kb,32kb,2mb"

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

PAGE_SIZE_ALIASES = {
    # "4kb": ("4kb", 12),
    "16kb": ("16kb", 14),
    "32kb": ("32kb", 15),
    "2mb": ("2mb", 21),
}

output_dir = ""


def parse_args():
    parser = argparse.ArgumentParser(
        description="Sweep benchmark results across multiple page sizes."
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
        help="Adaptive low threshold for PTCL mode.",
    )
    parser.add_argument(
        "--adaptive-threshold-high",
        dest="adaptive_threshold_high",
        type=int,
        default=DEFAULT_ADAPTIVE_HIGH,
        help="Adaptive high threshold for PTCL mode.",
    )
    parser.add_argument(
        "--mmutlb-ptcl-return-latency",
        dest="mmutlb_ptcl_return_latency",
        type=int,
        default=DEFAULT_MMUTLB_LOOKUP_LATENCY,
        help="Fixed MMUTLB/IOTLB lookup latency per requested PTE (per bitmap bit), in cycles.",
    )
    parser.add_argument(
        "--gmmu-pte-lookup-latency",
        dest="gmmu_pte_lookup_latency",
        type=int,
        default=DEFAULT_GMMU_PTE_LOOKUP_LATENCY,
        help="Fixed GMMU L2 TLB lookup latency per internal PTE lookup job, in cycles.",
    )
    parser.add_argument(
        "--configs",
        default=DEFAULT_CONFIGS,
        help=(
            "Comma-separated configs to run. Available: "
            "baseline,ptcl_mode,coalescing,camsat. "
            f"Default: {DEFAULT_CONFIGS}"
        ),
    )
    parser.add_argument(
        "--page-sizes",
        default=DEFAULT_PAGE_SIZES,
        help=(
            "Comma-separated page sizes to sweep. Available: "
            "4kb,16kb,32kb,2mb. "
            f"Default: {DEFAULT_PAGE_SIZES}"
        ),
    )
    return parser.parse_args()


def adaptive_flags(low, high):
    return [
        "-gmmu-initial-ptcl-mode=false",
        f"-gmmu-ptcl-threshold-low={low}",
        f"-gmmu-ptcl-threshold-high={high}",
    ]


def build_common_flags(args):
    return BASE_COMMON_FLAGS + [
        f"-mmutlb-ptcl-return-latency={args.mmutlb_ptcl_return_latency}",
        f"-gmmu-pte-lookup-latency={args.gmmu_pte_lookup_latency}",
    ]


def base_configs(args):
    low = min(args.adaptive_threshold_low, args.adaptive_threshold_high)
    high = max(args.adaptive_threshold_low, args.adaptive_threshold_high)
    return {
        "baseline": VPN_MSHR_BASELINE_FLAGS,
        "ptcl_mode": adaptive_flags(low, high),
        "coalescing": VPN_MSHR_BASELINE_FLAGS + COALESCING_FLAGS,
        "camsat": adaptive_flags(low, high) + COALESCING_FLAGS,
    }


def parse_named_list(raw, allowed, kind):
    names = []
    for token in raw.split(","):
        name = token.strip().lower()
        if not name:
            continue
        if name not in allowed:
            raise ValueError(f"unknown {kind} '{token}'. Allowed: {', '.join(sorted(allowed))}")
        names.append(name)

    if not names:
        raise ValueError(f"no valid {kind} provided")

    return names


def build_configs(args):
    configs = base_configs(args)
    selected = parse_named_list(args.configs, configs.keys(), "config")
    return [(name, configs[name]) for name in selected]


def build_page_sizes(args):
    selected = parse_named_list(args.page_sizes, PAGE_SIZE_ALIASES.keys(), "page size")
    return [PAGE_SIZE_ALIASES[name] for name in selected]


def get_benchmarks_for_target(target):
    selected = BENCHMARKS_BY_TARGET.get(target, ["all"])
    if "all" in selected:
        return ALL_BENCHMARKS[:]

    unknown = sorted(set(selected) - set(ALL_BENCHMARKS))
    if unknown:
        raise ValueError(f"unknown benchmarks for {target}: {unknown}")

    return selected


def make_exps(configs, page_sizes):
    exps = []
    for target in TARGETS:
        for benchmark in get_benchmarks_for_target(target):
            for config_name, config_flags in configs:
                for page_label, page_size in page_sizes:
                    exps.append(
                        {
                            "target": target,
                            "benchmark": benchmark,
                            "config_name": config_name,
                            "page_label": page_label,
                            "log2_page_size": page_size,
                            "flags": DEFAULT_BENCHMARK_FLAGS
                            + config_flags
                            + [f"-log2-page-size={page_size}"],
                        }
                    )
    return exps


def filter_missing_metric_exps(exps, results_dir):
    missing = []
    missing_dir = Path(results_dir)
    for exp in exps:
        stem = f'{exp["target"]}_{exp["benchmark"]}_{exp["config_name"]}_{exp["page_label"]}'
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
        f'{exp["target"]}_{exp["benchmark"]}_{exp["config_name"]}_{exp["page_label"]}',
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
    print(cmd_str)

    out_file_name = f"{file_stem}_out.stdout"
    with open(out_file_name, "w") as out_file:
        out_file.write(f"Executing {cmd_str}\n")
        start_time = datetime.now()
        out_file.write(f"Start time: {start_time}\n")
        out_file.flush()

        process = subprocess.Popen(
            cmd,
            stdout=out_file,
            stderr=out_file,
            cwd=ROOT_DIR,
        )
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
        datetime.now().strftime("%Y-%m-%d-%H-%M-%S-huge-page-sweep"),
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
    configs = build_configs(args)
    page_sizes = build_page_sizes(args)
    exps = make_exps(configs, page_sizes)
    if not exps:
        print("No experiments configured.")
        return

    if args.rerun_missing:
        output_dir = os.path.abspath(args.rerun_missing)
        if not os.path.isdir(output_dir):
            raise ValueError(f"rerun directory does not exist: {output_dir}")
        exps = filter_missing_metric_exps(exps, output_dir)
        if not exps:
            print(f"No missing metric files in {output_dir}")
            return
        print(f"Rerunning {len(exps)} missing experiments in {output_dir}")
    else:
        create_output_dir()

    for exp in exps:
        exp["common_flags"] = common_flags

    print(f"Results directory: {output_dir}")
    print(f"Common flags: {' '.join(common_flags)}")
    print(
        "Configs: "
        + ", ".join(config_name for config_name, _ in configs)
    )
    print(
        "Page sizes: "
        + ", ".join(f"{label}=2^{size}" for label, size in page_sizes)
    )

    build_targets(exps)

    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = [executor.submit(run_exp, exp) for exp in exps]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result and result.get("returncode", 0) != 0:
                failures.append(result)

    if failures:
        print("\nFailures:")
        for failure in failures:
            exp = failure["exp"]
            print(
                f'- {exp["target"]} {exp["benchmark"]} '
                f'{exp["config_name"]} {exp["page_label"]}: '
                f'returncode={failure["returncode"]}'
            )
    else:
        print("\nAll experiments completed successfully.")


if __name__ == "__main__":
    main()
