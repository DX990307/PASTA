#!/usr/bin/env python3
"""Standalone 400latency sweeps across multiple GPU page sizes."""

import argparse
from datetime import datetime
import math
import os
from pathlib import Path
import shlex
import subprocess
import time


ROOT_DIR = Path(__file__).resolve().parent
TARGETS = ["400latency"]

DEFAULT_PAGE_SIZES = "64kb,2mb"
DEFAULT_CONFIGS = "baseline,ptcl_mode_flex_iommu_assist"
DEFAULT_MAX_WORKERS = 0
DEFAULT_MAX_WORKLOADS = 16
DEFAULT_MIN_FREE_RAM_GB = 60.0
DEFAULT_MEMORY_SCAN_INTERVAL_MINUTES = 30.0
INITIAL_FILL_SETTLE_SECONDS = 2.0

DEFAULT_NUM_MEMORY_BANKS = 16
DEFAULT_BANDWIDTH = 48
DEFAULT_SWITCH_LATENCY = 32
DEFAULT_DRAM_FREQUENCY_MHZ = 500.0
DEFAULT_DRAM_TIMING_SCALE = 1.0
DEFAULT_DRAM_COMMAND_QUEUE_SIZE = 8
DEFAULT_DRAM_TRANSACTION_QUEUE_SIZE = 32

FAST_DRAM_NUM_MEMORY_BANKS = 32
FAST_DRAM_BANDWIDTH = 192
FAST_DRAM_SWITCH_LATENCY = 4
FAST_DRAM_FREQUENCY_MHZ = 1000.0
FAST_DRAM_TIMING_SCALE = 0.25
FAST_DRAM_COMMAND_QUEUE_SIZE = 32
FAST_DRAM_TRANSACTION_QUEUE_SIZE = 128

BASE_GMMU_TLB_SETS = 16
BASE_GMMU_TLB_WAYS = 16
BASE_PAGE_SIZE_BYTES = 4 * 1024
BASE_PTCL_LINE_SIZE = 8
MIN_HUGEPAGE_PTCL_LINE_SIZE = 8

DEFAULT_ADAPTIVE_LOW = 4
DEFAULT_ADAPTIVE_HIGH = 16
DEFAULT_ADAPTIVE_THRESHOLD_PAIRS = "0:2,1:4,2:6,2:8,4:12,4:16,8:24"
DEFAULT_MMUTLB_PTCL_RETURN_LATENCY = 80
DEFAULT_GMMU_NUM_REQ_PER_CYCLE = 128
DEFAULT_GMMU_TLB_NUM_SETS = 16
DEFAULT_GMMU_TLB_NUM_WAYS = 16
DEFAULT_GMMU_PTE_LOOKUP_LATENCY = 16
DEFAULT_BASELINE_GMMU_PTE_LOOKUP_LATENCY = 32
DEFAULT_GMMU_PTE_LOOKUP_SLOTS = 8
DEFAULT_GMMU_PTCL_LINE_SIZE = 8
DEFAULT_GMMU_FLEX_PCD_WAYS = 0
DEFAULT_GMMU_FLEX_PROMOTION_THRESHOLD = 3
DEFAULT_TIMEOUT_MINUTES = 0.0
DEFAULT_PHOTON_SAMPLED_WARMUP = 512
DEFAULT_PHOTON_SAMPLED_GRANULARITY = 512
DEFAULT_PHOTON_LOOP_SAMPLED_WARMUP = 512
DEFAULT_PHOTON_FIXED_WARMUP = 64
DEFAULT_PHOTON_FIXED_PERIOD = 64
DEFAULT_PHOTON_FIXED_DETAIL = 1

TRADITIONAL_BENCHMARKS = [
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

EXPERIMENTAL_BENCHMARKS = [
    "resnet",
    "llmop",
    "llminference",
    "matrixmultiplication-ptw",
    "matrixmultiplication-ptw-heavy",
]

REMOVED_MONOLITHIC_LLM_BENCHMARKS = {
    "bert",
    "gpt",
    "kvcache",
    "kvcache-decode",
    "kvcache-decode-30b",
}

HELIOSTAT_REQUESTED_BENCHMARKS = [
    "lu",
    "j2d",
    "fdtd2d",
    "matr",
    "gups",
    "gesm",
]

HELIOSTAT_NEW_HUGEPAGE_BENCHMARKS = [
    "lu",
    "j2d",
    "fdtd2d",
    "gups",
    "gesm",
]

HUGEPAGE_CURRENT_BENCHMARKS = [

    "bitonicsort",
    "im2col",
    "floydwarshall",
    "aes",
    "relu",
    "spmv",
    "matrixmultiplication-ptw",
    "matrixtranspose",
    "fastwalshtransform",
    "fft",
    "kmeans",
    "pagerank",
    "simpleconvolution",
    "fir",
    *HELIOSTAT_NEW_HUGEPAGE_BENCHMARKS,
]

HUGEPAGE_HUGE_BENCHMARKS = [
    # "lu",
    # "j2d",
    # "fdtd2d",
    # "gups",
    # "gesm",      
    "relu",
    "bitonicsort",
    "im2col",
    "floydwarshall",
    "aes",
    "spmv",
    "matrixmultiplication-ptw",
    "matrixtranspose",
    "fastwalshtransform",
    "fft",
    "kmeans",
    "pagerank",
    "simpleconvolution",
    "fir",
    # "relu-huge",
    # "bitonicsort-huge",
    # "im2col-huge",
    # "floydwarshall-huge",
    # "aes-huge",
    # "spmv-huge",
    # "matrixmultiplication-ptw-huge",
    # "matrixtranspose-huge",
    # "fastwalshtransform-huge",
    # "fft-huge",
    # "kmeans-huge",
    # "pagerank-huge",
    # "simpleconvolution-huge",
    # "fir-huge",
    # *HELIOSTAT_NEW_HUGEPAGE_BENCHMARKS,
]

HUGEPAGE_SMOKE_BENCHMARKS = [
    "im2col-huge",
    "matrixmultiplication-ptw-huge",
]

DEFAULT_ALL_BENCHMARKS = list(dict.fromkeys(
    TRADITIONAL_BENCHMARKS + EXPERIMENTAL_BENCHMARKS
))

KNOWN_BENCHMARKS = list(dict.fromkeys(
    TRADITIONAL_BENCHMARKS + EXPERIMENTAL_BENCHMARKS +
    HUGEPAGE_HUGE_BENCHMARKS + HELIOSTAT_REQUESTED_BENCHMARKS +
    ["matr-huge"]
))

HUGEPAGE_PRESSURE_BENCHMARKS = HUGEPAGE_HUGE_BENCHMARKS

DEFAULT_RUN_BENCHMARKS = HUGEPAGE_HUGE_BENCHMARKS

BENCHMARK_ALIASES = {
    "all": DEFAULT_ALL_BENCHMARKS,
    "traditional": TRADITIONAL_BENCHMARKS,
    "llm": ["llmop"],
    "experimental": EXPERIMENTAL_BENCHMARKS,
    "heliostat-requested": HELIOSTAT_REQUESTED_BENCHMARKS,
    "hugepage": HUGEPAGE_PRESSURE_BENCHMARKS,
    "hugepage-current": HUGEPAGE_CURRENT_BENCHMARKS,
    "hugepage-huge": HUGEPAGE_HUGE_BENCHMARKS,
    "hugepage-pressure": HUGEPAGE_PRESSURE_BENCHMARKS,
    "hugepage-smoke": HUGEPAGE_SMOKE_BENCHMARKS,
}

PAGE_SIZE_ALIASES = {
    "4k": 4 * 1024,
    "4kb": 4 * 1024,
    "64k": 64 * 1024,
    "64kb": 64 * 1024,
    "2m": 2 * 1024 * 1024,
    "2mb": 2 * 1024 * 1024,
}

BASE_COMMON_FLAGS = [
    "-timing",
    "-magic-memory-copy",
    "-report-all",
]

DEFAULT_BENCHMARK_FLAGS = [
    "-max-wg=76800",
]

GLOBAL_PHOTON_FLAGS = [
    "-sampled",
    "-branch-sampled",
    "-kernel-sampled",
    "-loop-sampled",
]

PHOTON_BRANCH_LOOP_FLAGS = {
    "-branch-sampled",
    "-loop-sampled",
}

TIMING_ADAPTIVE_PHOTON_FLAGS = PHOTON_BRANCH_LOOP_FLAGS | {
    "-kernel-sampled",
}

COALESCING_FLAGS = [
    "-mmu-walk-coalescing",
]

IOMMU_TLB_OPT_FLAGS = [
    "-mmutlb-flex-tlb",
]

VPN_MSHR_BASELINE_FLAGS = [
    "-gmmu-vpn-mshr-baseline",
    "-mmutlb-vpn-mshr-baseline",
    "-gmmu-initial-ptcl-mode=false",
    "-gmmu-ptcl-threshold-low=0",
    "-gmmu-ptcl-threshold-high=1000000",
    "-mmutlb-demand-pte-only",
]

PTW_DEMAND_PTE_ONLY_FLAGS = [
    "-ptw-demand-pte-only",
]

CONFIGS = [
    ("baseline", []),
    ("sample_all", ["-sampled", "-branch-sampled", "-kernel-sampled"]),
    (
        "sample_all_loop",
        ["-sampled", "-branch-sampled", "-kernel-sampled", "-loop-sampled"],
    ),
    ("sample_wf", ["-sampled"]),
    ("sample_branch", ["-branch-sampled"]),
    ("sample_kernel", ["-kernel-sampled"]),
    ("sample_loop", ["-loop-sampled"]),
]

PTCL_CONFIG_NAMES = [
    "baseline",
    "flex_entry",
    "ptcl_mode",
    "ptcl_parallel",
    "ptcl_flex",
    "ptcl_mode_flex",
    "pasta",
    "coalescing",
    "camsat",
]

EXTRA_PTCL_CONFIG_NAMES = [
    "idle_iommu_assist",
    "ptcl_mode_flex_iommu_assist",
]

ABLATION_STUDY_CONFIG_NAMES = [
    "baseline",
    "ptcl_mode",
    "flex_entry",
    "idle_iommu_assist",
    "ptcl_mode_flex_iommu_assist",
]


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Run standalone huge-page experiments by sweeping "
            "-log2-page-size values."
        )
    )
    parser.add_argument(
        "--page-sizes",
        default=DEFAULT_PAGE_SIZES,
        help=(
            "Comma-separated page sizes. Accepts aliases such as 4kb,64kb,2mb, "
            "raw byte counts, or log2 values such as 12,16,21."
        ),
    )
    parser.add_argument(
        "--configs",
        default="",
        help=(
            "Comma-separated config list. Defaults to "
            f"{DEFAULT_CONFIGS}. Supported PTCL/Flex choices: "
            + ",".join(PTCL_CONFIG_NAMES + EXTRA_PTCL_CONFIG_NAMES)
            + ". Photon sampled choices: "
            + ",".join(name for name, _ in CONFIGS)
            + ". Use ptcl_all or photon_all/all for grouped configs."
        ),
    )
    parser.add_argument(
        "--benchmarks",
        default="",
        help=(
            "Comma-separated benchmark list. Presets: "
            + ",".join(sorted(BENCHMARK_ALIASES))
            + ". Empty uses the hugepage-pressure preset. Use "
            "hugepage-current to reproduce the previous run_hugepage list."
        ),
    )
    parser.add_argument(
        "--extra-benchmark-flags",
        default="",
        help=(
            "Extra benchmark flags appended to every benchmark command. Any "
            "existing -log2-page-size flag is replaced by this sweep."
        ),
    )
    parser.add_argument(
        "--capacity-normalized-gmmu-tlb",
        action="store_true",
        help=(
            "Scale GMMU L2 TLB entries down as page size grows so byte reach "
            "stays close to the 4KB 16x16 baseline."
        ),
    )
    parser.add_argument(
        "--hugepage-aware-ptcl-line-size",
        action="store_true",
        help=(
            "Scale -gmmu-ptcl-line-size with page size while keeping enough "
            "PTEs per line for PTCL/Flex promotion on huge pages."
        ),
    )
    parser.set_defaults(ptcl_friendly_cu_access=True)
    parser.add_argument(
        "--ptcl-friendly-cu-access",
        dest="ptcl_friendly_cu_access",
        action="store_true",
        help=(
            "Use PTCL-friendly CU access organization: strictly partition workgroups "
            "across CUs and align allocations to PTCL-line boundaries. "
            "Enabled by default."
        ),
    )
    parser.add_argument(
        "--no-ptcl-friendly-cu-access",
        dest="ptcl_friendly_cu_access",
        action="store_false",
        help="Use the legacy round-robin CU dispatch and unaligned allocations.",
    )
    parser.add_argument(
        "--fast-dram",
        action="store_true",
        help=(
            "Use a fast-memory preset: more memory banks, higher mesh "
            "bandwidth, lower switch latency, faster DRAM timing, and larger "
            "DRAM queues. Explicit memory flags override this preset."
        ),
    )
    parser.add_argument(
        "--num-memory-banks",
        type=int,
        default=None,
        help=(
            "Pass -num-memory-banks. Empty uses 16, or 32 with --fast-dram."
        ),
    )
    parser.add_argument(
        "--bandwidth",
        type=int,
        default=None,
        help=(
            "Pass -bandwidth, in multiples of 16GB/s. Empty uses 48, or 192 "
            "with --fast-dram."
        ),
    )
    parser.add_argument(
        "--switch-latency",
        type=int,
        default=None,
        help="Pass -switch-latency. Empty uses 32, or 4 with --fast-dram.",
    )
    parser.add_argument(
        "--dram-frequency-mhz",
        type=float,
        default=None,
        help=(
            "Pass -dram-frequency-mhz. Empty uses 500, or 1000 with --fast-dram."
        ),
    )
    parser.add_argument(
        "--dram-timing-scale",
        type=float,
        default=None,
        help=(
            "Pass -dram-timing-scale. Values below 1 model faster DRAM timing. "
            "Empty uses 1.0, or 0.25 with --fast-dram."
        ),
    )
    parser.add_argument(
        "--dram-command-queue-size",
        type=int,
        default=None,
        help=(
            "Pass -dram-command-queue-size. Empty uses 8, or 32 with "
            "--fast-dram."
        ),
    )
    parser.add_argument(
        "--dram-transaction-queue-size",
        type=int,
        default=None,
        help=(
            "Pass -dram-transaction-queue-size. Empty uses 32, or 128 with "
            "--fast-dram."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help=(
            "Top-level output directory. Empty creates "
            "results/<timestamp>-hugepage-sweep."
        ),
    )
    parser.add_argument(
        "--only-config",
        default="",
        help="Only run experiments with this config name.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help=(
            "Legacy optional additional cap on concurrent experiments. 0 means "
            "only --max-workloads and available RAM control launches."
        ),
    )
    parser.add_argument(
        "--max-workloads",
        type=int,
        default=DEFAULT_MAX_WORKLOADS,
        help=(
            "Hard cap on concurrently running benchmark workloads. Must be "
            f"between 1 and {DEFAULT_MAX_WORKLOADS}."
        ),
    )
    parser.add_argument(
        "--max-wg",
        type=int,
        default=None,
        help="Pass -max-wg to each benchmark. 0 disables the default cap.",
    )
    parser.add_argument(
        "--timeout-minutes",
        type=float,
        default=DEFAULT_TIMEOUT_MINUTES,
        help="Kill an experiment after this many minutes. 0 disables timeout.",
    )
    parser.add_argument(
        "--min-free-ram-gb",
        type=float,
        default=DEFAULT_MIN_FREE_RAM_GB,
        help=(
            "Minimum Linux MemAvailable, in GiB, required before launching the "
            "next benchmark."
        ),
    )
    parser.add_argument(
        "--memory-scan-interval-minutes",
        type=float,
        default=DEFAULT_MEMORY_SCAN_INTERVAL_MINUTES,
        help=(
            "How often to check MemAvailable and consider launching one "
            "benchmark."
        ),
    )
    parser.add_argument(
        "--photon-debug",
        action="store_true",
        help="Add -photon-debug to sampled WSG-style configs.",
    )
    parser.add_argument(
        "--photon",
        action="store_true",
        help=(
            "Append the script-level Photon sampled flags to every selected "
            "config."
        ),
    )
    parser.add_argument(
        "--photon-no-branch-loop",
        action="store_true",
        help=(
            "When --photon is set, skip -branch-sampled and -loop-sampled "
            "for debugging sampled execution."
        ),
    )
    parser.add_argument(
        "--photon-fixed-schedule",
        action="store_true",
        help=(
            "When --photon is set, use a deterministic WF sampled mask and "
            "skip timing-adaptive branch/kernel/loop sampled execution."
        ),
    )
    parser.add_argument(
        "--photon-verbose",
        action="store_true",
        help="Add -photon-debug and -photon-debug-verbose to sampled configs.",
    )
    parser.add_argument(
        "--sampled-warmups",
        default="",
        help="Comma-separated -sampled-warmup values for sampled configs.",
    )
    parser.add_argument(
        "--sampled-granularities",
        default="",
        help="Comma-separated -sampled-granularity values for sampled configs.",
    )
    parser.add_argument(
        "--sampled-fixed-warmup",
        type=int,
        default=None,
        help=(
            "Pass -sampled-fixed-warmup when --photon-fixed-schedule is set. "
            "Defaults to -sampled-warmup."
        ),
    )
    parser.add_argument(
        "--sampled-fixed-period",
        type=int,
        default=None,
        help=(
            "Pass -sampled-fixed-period when --photon-fixed-schedule is set. "
            "Defaults to -sampled-granularity."
        ),
    )
    parser.add_argument(
        "--sampled-fixed-detail",
        type=int,
        default=None,
        help=(
            "Pass -sampled-fixed-detail when --photon-fixed-schedule is set."
        ),
    )
    parser.add_argument(
        "--adaptive-threshold-low",
        type=int,
        default=DEFAULT_ADAPTIVE_LOW,
        help="Default adaptive low threshold used in non-scan mode.",
    )
    parser.add_argument(
        "--adaptive-threshold-high",
        type=int,
        default=DEFAULT_ADAPTIVE_HIGH,
        help="Default adaptive high threshold used in non-scan mode.",
    )
    parser.add_argument(
        "--adaptive-threshold-scan",
        action="store_true",
        help=(
            "Scan adaptive thresholds with PTCL adaptation and MMU coalescing "
            "enabled."
        ),
    )
    parser.add_argument(
        "--adaptive-threshold-pairs",
        default=DEFAULT_ADAPTIVE_THRESHOLD_PAIRS,
        help='Comma-separated low:high pairs, for example "2:6,4:12,20:60".',
    )
    parser.add_argument(
        "--ptcl-flex-test",
        action="store_true",
        help=(
            "Run only ptcl_mode, ptcl_parallel, ptcl_flex, and "
            "ptcl_mode_flex configs."
        ),
    )
    parser.add_argument(
        "--ptcl-flex-iommu-assist-test",
        action="store_true",
        help=(
            "Run only ptcl_mode, flex_entry, idle_iommu_assist, and "
            "ptcl_mode_flex_iommu_assist configs."
        ),
    )
    parser.add_argument(
        "--ablation-study",
        action="store_true",
        help=(
            "Run baseline plus ptcl_mode, flex_entry, idle_iommu_assist, "
            "and ptcl_mode_flex_iommu_assist configs."
        ),
    )
    parser.add_argument(
        "--mmutlb-ptcl-return-latency",
        type=int,
        default=DEFAULT_MMUTLB_PTCL_RETURN_LATENCY,
        help=(
            "Fixed MMUTLB/IOTLB lookup latency per requested PTE, in cycles."
        ),
    )
    parser.add_argument(
        "--gmmu-pte-lookup-latency",
        type=int,
        default=DEFAULT_GMMU_PTE_LOOKUP_LATENCY,
        help="Fixed GMMU L2 TLB lookup latency per internal PTE lookup job.",
    )
    parser.add_argument(
        "--gmmu-num-req-per-cycle",
        type=int,
        default=DEFAULT_GMMU_NUM_REQ_PER_CYCLE,
        help=(
            "GMMU L2 TLB top/bottom/response processing width. This is "
            "separate from --gmmu-pte-lookup-slots."
        ),
    )
    parser.add_argument(
        "--gmmu-tlb-num-sets",
        type=int,
        default=DEFAULT_GMMU_TLB_NUM_SETS,
        help="Number of sets in each GMMU L2 TLB.",
    )
    parser.add_argument(
        "--gmmu-tlb-num-ways",
        type=int,
        default=DEFAULT_GMMU_TLB_NUM_WAYS,
        help="Number of ways in each GMMU L2 TLB.",
    )
    parser.add_argument(
        "--gmmu-pte-lookup-slots",
        type=int,
        default=DEFAULT_GMMU_PTE_LOOKUP_SLOTS,
        help="Maximum number of GMMU L2 TLB internal PTE lookup jobs in flight.",
    )
    parser.add_argument(
        "--gmmu-ptcl-serial-lookup",
        action="store_true",
        help=(
            "Model non-flex GMMU PTCL lookup as one serial bitmap lookup "
            "instead of parallel per-bit lookup jobs."
        ),
    )
    parser.add_argument(
        "--gmmu-ptcl-line-size",
        type=int,
        default=DEFAULT_GMMU_PTCL_LINE_SIZE,
        help="Number of PTEs per GMMU PTCL line.",
    )
    parser.add_argument(
        "--gmmu-flex-promotion-threshold",
        type=int,
        default=DEFAULT_GMMU_FLEX_PROMOTION_THRESHOLD,
        help="Minimum valid bitmap fill bits before Flex stores a PTCL-line entry.",
    )
    parser.add_argument(
        "--gmmu-flex-pcd-ways",
        type=int,
        default=DEFAULT_GMMU_FLEX_PCD_WAYS,
        help=(
            "Exact PTCL locator rows per PCD set. 0 uses ceil(GMMU TLB ways / 8)."
        ),
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue to the next page size if one page-size sweep fails.",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Do not rebuild the 400latency binary before running.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print benchmark commands without building or running them.",
    )
    return parser.parse_args()


def parse_csv(value):
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_int_csv(value, label):
    values = []
    for item in parse_csv(value):
        parsed = int(item)
        if parsed <= 0:
            raise ValueError(f"{label} values must be positive: {item}")
        values.append(parsed)
    return values


def unique_preserving_order(items):
    seen = set()
    unique = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def unique_preserving_config_names(configs):
    seen = set()
    unique = []
    for name, flags in configs:
        if name in seen:
            continue
        seen.add(name)
        unique.append((name, flags))
    return unique


def parse_page_size(token):
    raw = token.strip()
    normalized = raw.lower().replace("_", "").replace("-", "")
    if not normalized:
        raise ValueError("empty page-size token")

    if normalized in PAGE_SIZE_ALIASES:
        page_bytes = PAGE_SIZE_ALIASES[normalized]
    else:
        try:
            value = int(normalized, 0)
        except ValueError as exc:
            raise ValueError(f"invalid page size: {raw}") from exc

        if value <= 0:
            raise ValueError(f"page size must be positive: {raw}")

        if value <= 63:
            page_bytes = 1 << value
        else:
            page_bytes = value

    if page_bytes & (page_bytes - 1) != 0:
        raise ValueError(f"page size must be a power of two: {raw}")

    log2_page_size = int(math.log2(page_bytes))
    return page_bytes, log2_page_size, page_size_label(page_bytes)


def page_size_label(page_bytes):
    if page_bytes % (1024 * 1024) == 0:
        return f"{page_bytes // (1024 * 1024)}mb"
    if page_bytes % 1024 == 0:
        return f"{page_bytes // 1024}kb"
    return f"{page_bytes}b"


def unique_page_sizes(raw_page_sizes):
    seen = set()
    page_sizes = []
    for token in parse_csv(raw_page_sizes):
        page_bytes, log2_page_size, label = parse_page_size(token)
        if page_bytes in seen:
            continue
        seen.add(page_bytes)
        page_sizes.append({
            "bytes": page_bytes,
            "log2": log2_page_size,
            "label": label,
        })

    if not page_sizes:
        raise ValueError("no page sizes selected")

    return page_sizes


def expand_benchmark_selection(selected):
    expanded = []
    for item in selected:
        if item in BENCHMARK_ALIASES:
            expanded += BENCHMARK_ALIASES[item]
        else:
            expanded.append(item)

    blocked = [
        item for item in expanded
        if item in REMOVED_MONOLITHIC_LLM_BENCHMARKS
    ]
    if blocked:
        raise ValueError(
            "monolithic LLM benchmarks were removed from this runner: "
            + ",".join(blocked)
            + ". Use runllm_decomposed.py for BERT/GPT experiments."
        )

    return unique_preserving_order(expanded)


def get_selected_benchmarks(args):
    if args.benchmarks:
        selected = parse_csv(args.benchmarks)
    else:
        selected = DEFAULT_RUN_BENCHMARKS

    selected = expand_benchmark_selection(selected)
    unknown = sorted(set(selected) - set(KNOWN_BENCHMARKS))
    if unknown:
        raise ValueError(f"unknown benchmarks: {unknown}")
    return selected


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
        "-gmmu-initial-ptcl-mode=false",
        f"-gmmu-ptcl-threshold-low={low}",
        f"-gmmu-ptcl-threshold-high={high}",
    ]


def baseline_gmmu_lookup_flags():
    return [
        f"-gmmu-pte-lookup-latency={DEFAULT_BASELINE_GMMU_PTE_LOOKUP_LATENCY}",
    ]


def ptcl_gmmu_lookup_flags(args):
    return [
        f"-gmmu-pte-lookup-latency={2 * args.gmmu_pte_lookup_latency}",
    ]


def build_ptcl_config_map(args):
    low = args.adaptive_threshold_low
    high = args.adaptive_threshold_high
    if low > high:
        low, high = high, low

    flex_flags = [
        "-gmmu-flex-tlb",
        f"-gmmu-flex-promotion-threshold={args.gmmu_flex_promotion_threshold}",
    ]

    return {
        "baseline": VPN_MSHR_BASELINE_FLAGS
        + PTW_DEMAND_PTE_ONLY_FLAGS
        + baseline_gmmu_lookup_flags(),
        "idle_iommu_assist": VPN_MSHR_BASELINE_FLAGS
        + PTW_DEMAND_PTE_ONLY_FLAGS
        + baseline_gmmu_lookup_flags()
        + ["-gmmu-idle-iommu-assist"],
        "flex_entry": VPN_MSHR_BASELINE_FLAGS + flex_flags,
        "ptcl_mode": adaptive_flags(low, high) + ptcl_gmmu_lookup_flags(args),
        "ptcl_parallel": adaptive_flags(low, high) + ptcl_gmmu_lookup_flags(args),
        "ptcl_flex": VPN_MSHR_BASELINE_FLAGS + flex_flags,
        "ptcl_mode_flex": adaptive_flags(low, high)
        + flex_flags
        + IOMMU_TLB_OPT_FLAGS,
        "ptcl_mode_flex_iommu_assist": adaptive_flags(low, high)
        + flex_flags
        + IOMMU_TLB_OPT_FLAGS
        + ["-gmmu-idle-iommu-assist"],
        "pasta": adaptive_flags(low, high)
        + flex_flags
        + IOMMU_TLB_OPT_FLAGS,
        "coalescing": VPN_MSHR_BASELINE_FLAGS + COALESCING_FLAGS,
        "camsat": adaptive_flags(low, high)
        + IOMMU_TLB_OPT_FLAGS
        + COALESCING_FLAGS
        + ptcl_gmmu_lookup_flags(args),
    }


def build_selected_configs(args, ptcl_configs, requested):
    photon_configs = {name: flags for name, flags in CONFIGS}
    selected = []

    explicit_ptcl = any(
        name in ptcl_configs and name != "baseline"
        for name in requested
    )
    explicit_photon = any(
        name in photon_configs and name != "baseline"
        for name in requested
    )

    for name in requested:
        if name == "ptcl_all":
            selected += [
                (config_name, ptcl_configs[config_name])
                for config_name in PTCL_CONFIG_NAMES
            ]
            continue

        if name in ("all", "photon_all"):
            selected += build_photon_configs(args, list(photon_configs))
            continue

        if name == "baseline":
            if explicit_photon and not explicit_ptcl:
                selected += build_photon_configs(args, ["baseline"])
            else:
                selected.append(("baseline", ptcl_configs["baseline"]))
            continue

        if name in ptcl_configs:
            selected.append((name, ptcl_configs[name]))
            continue

        if name in photon_configs:
            selected += build_photon_configs(args, [name])
            continue

        allowed = sorted(
            set(ptcl_configs)
            | set(photon_configs)
            | {"ptcl_all", "photon_all", "all"}
        )
        raise ValueError(
            f"unknown config: {name}. Allowed: {', '.join(allowed)}"
        )

    return unique_preserving_config_names(selected)


def build_ablation_configs(args):
    if args.adaptive_threshold_scan:
        if (
            args.configs
            or args.ptcl_flex_test
            or args.ptcl_flex_iommu_assist_test
            or args.ablation_study
        ):
            raise ValueError(
                "--adaptive-threshold-scan cannot be combined with --configs "
                "or PTCL test presets"
            )
        threshold_pairs = parse_threshold_pairs(args.adaptive_threshold_pairs)
        return [
            (
                f"adaptive_l{low}_h{high}_camsat",
                adaptive_flags(low, high)
                + IOMMU_TLB_OPT_FLAGS
                + COALESCING_FLAGS
                + ptcl_gmmu_lookup_flags(args),
            )
            for low, high in threshold_pairs
        ]

    ptcl_configs = build_ptcl_config_map(args)
    preset_count = sum(
        1
        for enabled in (
            args.ptcl_flex_test,
            args.ptcl_flex_iommu_assist_test,
            args.ablation_study,
        )
        if enabled
    )
    if preset_count > 1:
        raise ValueError(
            "--ptcl-flex-test, --ptcl-flex-iommu-assist-test, and "
            "--ablation-study are mutually exclusive"
        )

    if args.ptcl_flex_test:
        if args.configs:
            raise ValueError("--ptcl-flex-test cannot be combined with --configs")
        return [
            ("ptcl_mode", ptcl_configs["ptcl_mode"]),
            ("ptcl_parallel", ptcl_configs["ptcl_parallel"]),
            ("ptcl_flex", ptcl_configs["ptcl_flex"]),
            ("ptcl_mode_flex", ptcl_configs["ptcl_mode_flex"]),
        ]

    if args.ptcl_flex_iommu_assist_test or args.ablation_study:
        if args.configs:
            preset = "--ablation-study"
            if args.ptcl_flex_iommu_assist_test:
                preset = "--ptcl-flex-iommu-assist-test"
            raise ValueError(f"{preset} cannot be combined with --configs")
        return [
            (config_name, ptcl_configs[config_name])
            for config_name in ABLATION_STUDY_CONFIG_NAMES
        ]

    requested = parse_csv(args.configs) if args.configs else parse_csv(DEFAULT_CONFIGS)
    return build_selected_configs(args, ptcl_configs, requested)


def build_photon_configs(args, requested):
    selected = []
    for name, flags in CONFIGS:
        if name not in requested:
            continue
        config_flags = flags[:]
        if (args.photon_debug or args.photon_verbose) and name != "baseline":
            config_flags.append("-photon-debug")
        if args.photon_verbose and name != "baseline":
            config_flags.append("-photon-debug-verbose")
        selected += expand_sampled_params(args, name, config_flags)

    return selected


def expand_sampled_params(args, name, flags):
    if "-sampled" not in flags:
        return [(name, flags)]

    warmups = parse_int_csv(args.sampled_warmups, "sampled-warmups")
    granularities = parse_int_csv(
        args.sampled_granularities, "sampled-granularities")
    if not warmups and not granularities:
        return [(name, flags)]
    if not warmups:
        warmups = [1024]
    if not granularities:
        granularities = [2048]

    expanded = []
    for warmup in warmups:
        for granularity in granularities:
            expanded.append((
                f"{name}_w{warmup}_g{granularity}",
                flags + [
                    f"-sampled-warmup={warmup}",
                    f"-sampled-granularity={granularity}",
                ],
            ))
    return expanded


def selected_global_photon_flags(args):
    if args.photon_fixed_schedule:
        return ["-sampled", "-sampled-fixed-schedule"]

    flags = list(GLOBAL_PHOTON_FLAGS)
    if args.photon_no_branch_loop:
        flags = [
            flag for flag in flags
            if flag not in PHOTON_BRANCH_LOOP_FLAGS
        ]
    return flags


def has_flag_with_prefix(flags, prefix):
    return any(flag.startswith(prefix) for flag in flags)


def append_unique_flag(flags, flag):
    if flag not in flags:
        flags.append(flag)


def append_default_photon_tuning_flags(args, flags):
    if (
        "-loop-sampled" in flags and
        not has_flag_with_prefix(flags, "-loop-sampled-warmup=")
    ):
        flags.append(
            f"-loop-sampled-warmup={DEFAULT_PHOTON_LOOP_SAMPLED_WARMUP}"
        )

    if not has_flag_with_prefix(flags, "-sampled-warmup="):
        flags.append(
            f"-sampled-warmup={DEFAULT_PHOTON_SAMPLED_WARMUP}"
        )
    if not has_flag_with_prefix(flags, "-sampled-granularity="):
        flags.append(
            f"-sampled-granularity={DEFAULT_PHOTON_SAMPLED_GRANULARITY}"
        )

    if "-sampled-fixed-schedule" in flags:
        fixed_warmup = (
            args.sampled_fixed_warmup
            if args.sampled_fixed_warmup is not None
            else DEFAULT_PHOTON_FIXED_WARMUP
        )
        fixed_period = (
            args.sampled_fixed_period
            if args.sampled_fixed_period is not None
            else DEFAULT_PHOTON_FIXED_PERIOD
        )
        fixed_detail = (
            args.sampled_fixed_detail
            if args.sampled_fixed_detail is not None
            else DEFAULT_PHOTON_FIXED_DETAIL
        )
        if (
            not has_flag_with_prefix(flags, "-sampled-fixed-warmup=")
        ):
            flags.append(
                f"-sampled-fixed-warmup={fixed_warmup}"
            )
        if (
            not has_flag_with_prefix(flags, "-sampled-fixed-period=")
        ):
            flags.append(
                f"-sampled-fixed-period={fixed_period}"
            )
        if (
            not has_flag_with_prefix(flags, "-sampled-fixed-detail=")
        ):
            flags.append(
                f"-sampled-fixed-detail={fixed_detail}"
            )


def add_global_photon_flags(args, flags):
    if not args.photon:
        return flags

    photon_flags = flags[:]
    if args.photon_fixed_schedule:
        photon_flags = [
            flag for flag in photon_flags
            if flag not in TIMING_ADAPTIVE_PHOTON_FLAGS
        ]

    for flag in selected_global_photon_flags(args):
        append_unique_flag(photon_flags, flag)

    if args.photon_debug or args.photon_verbose:
        append_unique_flag(photon_flags, "-photon-debug")
    if args.photon_verbose:
        append_unique_flag(photon_flags, "-photon-debug-verbose")

    append_default_photon_tuning_flags(args, photon_flags)
    return photon_flags


def capacity_normalized_gmmu_tlb_shape(page_bytes):
    base_entries = BASE_GMMU_TLB_SETS * BASE_GMMU_TLB_WAYS
    target_entries = max(1, base_entries * BASE_PAGE_SIZE_BYTES // page_bytes)

    best = None
    for sets in range(1, BASE_GMMU_TLB_SETS + 1):
        for ways in range(1, BASE_GMMU_TLB_WAYS + 1):
            entries = sets * ways
            if entries < target_entries:
                continue
            overage = entries - target_entries
            shape_skew = abs(sets - ways)
            candidate = (overage, shape_skew, entries, sets, ways)
            if best is None or candidate < best:
                best = candidate

    if best is None:
        return BASE_GMMU_TLB_SETS, BASE_GMMU_TLB_WAYS

    _, _, _, sets, ways = best
    return sets, ways


def hugepage_aware_ptcl_line_size(page_bytes):
    target_bytes = BASE_PAGE_SIZE_BYTES * BASE_PTCL_LINE_SIZE
    line_size = target_bytes // page_bytes
    if page_bytes > BASE_PAGE_SIZE_BYTES:
        line_size = max(line_size, MIN_HUGEPAGE_PTCL_LINE_SIZE)
    if line_size < 1:
        return 1
    if line_size > BASE_PTCL_LINE_SIZE:
        return BASE_PTCL_LINE_SIZE
    return line_size


def remove_log2_page_size_flags(flags):
    cleaned = []
    skip_next = False
    removed = []
    for i, flag in enumerate(flags):
        if skip_next:
            skip_next = False
            removed.append(flag)
            continue

        if flag in ("-log2-page-size", "--log2-page-size"):
            removed.append(flag)
            if i + 1 < len(flags):
                skip_next = True
            continue

        if (
            flag.startswith("-log2-page-size=")
            or flag.startswith("--log2-page-size=")
        ):
            removed.append(flag)
            continue

        cleaned.append(flag)

    if removed:
        print(
            "Ignoring page-size flags from --extra-benchmark-flags: "
            + shlex.join(removed),
            flush=True,
        )

    return cleaned


def remove_flag_names(flags, names):
    cleaned = []
    skip_next = False
    removed = []
    normalized_names = set(names)

    for i, flag in enumerate(flags):
        if skip_next:
            skip_next = False
            removed.append(flag)
            continue

        matched = False
        for name in normalized_names:
            if flag == name:
                removed.append(flag)
                if i + 1 < len(flags):
                    skip_next = True
                matched = True
                break
            if flag.startswith(name + "="):
                removed.append(flag)
                matched = True
                break

        if matched:
            continue

        cleaned.append(flag)

    if removed:
        print(
            "Ignoring overridden flags from --extra-benchmark-flags: "
            + shlex.join(removed),
            flush=True,
        )

    return cleaned


def page_gmmu_tlb_shape(args, page_size):
    if args.capacity_normalized_gmmu_tlb:
        return capacity_normalized_gmmu_tlb_shape(page_size["bytes"])
    return args.gmmu_tlb_num_sets, args.gmmu_tlb_num_ways


def page_ptcl_line_size(args, page_size):
    if args.hugepage_aware_ptcl_line_size:
        return hugepage_aware_ptcl_line_size(page_size["bytes"])
    return args.gmmu_ptcl_line_size


def memory_value(args, explicit, default, fast_default):
    if explicit is not None:
        return explicit
    if args.fast_dram:
        return fast_default
    return default


def memory_config(args):
    return {
        "num_memory_banks": memory_value(
            args,
            args.num_memory_banks,
            DEFAULT_NUM_MEMORY_BANKS,
            FAST_DRAM_NUM_MEMORY_BANKS,
        ),
        "bandwidth": memory_value(
            args,
            args.bandwidth,
            DEFAULT_BANDWIDTH,
            FAST_DRAM_BANDWIDTH,
        ),
        "switch_latency": memory_value(
            args,
            args.switch_latency,
            DEFAULT_SWITCH_LATENCY,
            FAST_DRAM_SWITCH_LATENCY,
        ),
        "dram_frequency_mhz": memory_value(
            args,
            args.dram_frequency_mhz,
            DEFAULT_DRAM_FREQUENCY_MHZ,
            FAST_DRAM_FREQUENCY_MHZ,
        ),
        "dram_timing_scale": memory_value(
            args,
            args.dram_timing_scale,
            DEFAULT_DRAM_TIMING_SCALE,
            FAST_DRAM_TIMING_SCALE,
        ),
        "dram_command_queue_size": memory_value(
            args,
            args.dram_command_queue_size,
            DEFAULT_DRAM_COMMAND_QUEUE_SIZE,
            FAST_DRAM_COMMAND_QUEUE_SIZE,
        ),
        "dram_transaction_queue_size": memory_value(
            args,
            args.dram_transaction_queue_size,
            DEFAULT_DRAM_TRANSACTION_QUEUE_SIZE,
            FAST_DRAM_TRANSACTION_QUEUE_SIZE,
        ),
    }


def page_extra_benchmark_flags(args, page_size):
    extra_flags = remove_log2_page_size_flags(
        shlex.split(args.extra_benchmark_flags)
    )
    extra_flags.append(f'-log2-page-size={page_size["log2"]}')

    if args.capacity_normalized_gmmu_tlb:
        extra_flags = remove_flag_names(
            extra_flags,
            {
                "-gmmu-tlb-num-sets",
                "--gmmu-tlb-num-sets",
                "-gmmu-tlb-num-ways",
                "--gmmu-tlb-num-ways",
            },
        )

    if args.hugepage_aware_ptcl_line_size:
        extra_flags = remove_flag_names(
            extra_flags,
            {
                "-gmmu-ptcl-line-size",
                "--gmmu-ptcl-line-size",
            },
        )

    return extra_flags


def build_common_flags(args, page_size):
    sets, ways = page_gmmu_tlb_shape(args, page_size)
    ptcl_line_size = page_ptcl_line_size(args, page_size)
    mem_config = memory_config(args)
    flags = BASE_COMMON_FLAGS + [
        f'-num-memory-banks={mem_config["num_memory_banks"]}',
        f'-bandwidth={mem_config["bandwidth"]}',
        f'-switch-latency={mem_config["switch_latency"]}',
        f'-dram-frequency-mhz={mem_config["dram_frequency_mhz"]}',
        f'-dram-timing-scale={mem_config["dram_timing_scale"]}',
        f'-dram-command-queue-size={mem_config["dram_command_queue_size"]}',
        "-dram-transaction-queue-size="
        f'{mem_config["dram_transaction_queue_size"]}',
        f"-mmutlb-ptcl-return-latency={args.mmutlb_ptcl_return_latency}",
        f"-gmmu-num-req-per-cycle={args.gmmu_num_req_per_cycle}",
        f"-gmmu-tlb-num-sets={sets}",
        f"-gmmu-tlb-num-ways={ways}",
        f"-gmmu-pte-lookup-latency={args.gmmu_pte_lookup_latency}",
        f"-gmmu-pte-lookup-slots={args.gmmu_pte_lookup_slots}",
        f"-gmmu-ptcl-line-size={ptcl_line_size}",
        f"-gmmu-flex-pcd-ways={args.gmmu_flex_pcd_ways}",
    ]
    if args.gmmu_ptcl_serial_lookup:
        flags.append("-gmmu-ptcl-serial-lookup")
    if args.ptcl_friendly_cu_access:
        flags.extend([
            "-ptcl-aligned-alloc",
            "-cu-dispatch-alg=partition-strict",
        ])
    return flags


def strip_disable_server_flags(flags):
    return flags


def default_benchmark_flags(args):
    if args.max_wg is not None:
        if args.max_wg <= 0:
            return []
        return [f"-max-wg={args.max_wg}"]
    return DEFAULT_BENCHMARK_FLAGS[:]


def make_page_exps(args, page_size, run_dir, ablation_configs):
    exps = []
    extra_flags = strip_disable_server_flags(
        page_extra_benchmark_flags(args, page_size)
    )
    common_flags = build_common_flags(args, page_size)
    timeout_seconds = int(args.timeout_minutes * 60)

    for target in TARGETS:
        for benchmark in get_selected_benchmarks(args):
            for config_name, config_flags in ablation_configs:
                flags = (
                    default_benchmark_flags(args)
                    + extra_flags
                    + strip_disable_server_flags(config_flags)
                )
                flags = add_global_photon_flags(args, flags)
                exps.append({
                    "target": target,
                    "benchmark": benchmark,
                    "config_name": config_name,
                    "common_flags": common_flags,
                    "flags": flags,
                    "results_dir": run_dir,
                    "timeout_seconds": timeout_seconds,
                })
    return exps


def filter_missing_metric_exps(exps, results_dir):
    missing = []
    missing_dir = Path(results_dir)
    for exp in exps:
        stem = f'{exp["target"]}_{exp["benchmark"]}_{exp["config_name"]}'
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
        target_dir = ROOT_DIR / target
        print(f"Building {target} in {target_dir}", flush=True)
        process = subprocess.Popen(
            ["go", "build", "-buildvcs=false"],
            cwd=target_dir,
            env=env,
        )
        process.wait()
        if process.returncode != 0:
            raise RuntimeError(f"failed to build {target}")


def exp_file_stem(exp):
    return str(
        Path(exp["results_dir"])
        / f'{exp["target"]}_{exp["benchmark"]}_{exp["config_name"]}'
    )


def experiment_command(exp):
    binary = ROOT_DIR / exp["target"] / exp["target"]
    metric_file_name = f"{exp_file_stem(exp)}_metrics"
    return [
        str(binary),
        f'-benchmark={exp["benchmark"]}',
        *exp["common_flags"],
        *exp["flags"],
        f"-metric-file-name={metric_file_name}",
    ]


def read_mem_available_kb():
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as meminfo_file:
            for line in meminfo_file:
                if line.startswith("MemAvailable:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1])
    except (FileNotFoundError, PermissionError, ValueError):
        return None

    return None


def format_memory_kb(kb):
    if kb is None:
        return "unknown"
    if kb >= 1024 * 1024:
        return f"{kb / (1024 * 1024):.2f} GiB"
    if kb >= 1024:
        return f"{kb / 1024:.2f} MiB"
    return f"{kb} KiB"


def launch_experiment(exp):
    file_stem = exp_file_stem(exp)
    metric_file_name = f"{file_stem}_metrics"

    cmd = experiment_command(exp)
    cmd_str = shlex.join(cmd)
    print(cmd_str, flush=True)

    out_file_name = f"{file_stem}_out.stdout"
    out_file = open(out_file_name, "w", encoding="utf-8")
    start_time = datetime.now()
    launch_mem_kb = read_mem_available_kb()
    out_file.write(f"Executing {cmd_str}\n")
    out_file.write(f"Start time: {start_time}\n")
    out_file.write(
        "Launch MemAvailable: "
        f"{launch_mem_kb} KiB ({format_memory_kb(launch_mem_kb)})\n"
    )
    out_file.flush()

    process = subprocess.Popen(
        cmd,
        stdout=out_file,
        stderr=subprocess.STDOUT,
        cwd=ROOT_DIR,
        text=True,
        bufsize=1,
    )

    return {
        "exp": exp,
        "process": process,
        "cmd_str": cmd_str,
        "metric_file_name": metric_file_name,
        "out_file": out_file,
        "start_time": start_time,
        "start_monotonic": time.monotonic(),
    }


def finalize_experiment(state, timed_out=False):
    process = state["process"]
    if timed_out and process.poll() is None:
        process.kill()
        process.wait()

    end_time = datetime.now()
    elapsed_time = end_time - state["start_time"]
    out_file = state["out_file"]
    out_file.write(f"Return code: {process.returncode}\n")
    if timed_out:
        timeout_seconds = state["exp"].get("timeout_seconds", 0)
        out_file.write(f"Timed out after {timeout_seconds} seconds\n")
    out_file.write(f"End time: {end_time}\n")
    out_file.write(f"Elapsed time: {elapsed_time}\n")
    out_file.close()

    cmd_str = state["cmd_str"]
    if timed_out:
        print(f"Timed out executing {cmd_str}", flush=True)
        return {
            "exp": state["exp"],
            "returncode": -9,
            "timeout": state["exp"].get("timeout_seconds", 0),
        }

    if process.returncode != 0:
        print(f"Error executing {cmd_str}", flush=True)
        return {"exp": state["exp"], "returncode": process.returncode}

    metrics_csv = state["metric_file_name"] + ".csv"
    if not os.path.exists(metrics_csv):
        print(f"Missing metrics file for {cmd_str}: {metrics_csv}", flush=True)
        return {
            "exp": state["exp"],
            "returncode": -1,
            "missing_metrics": metrics_csv,
        }

    print(f"Executed {cmd_str}, time {elapsed_time}", flush=True)
    return {"exp": state["exp"], "returncode": 0}


def effective_workload_cap(args):
    cap = args.max_workloads
    if args.max_workers > 0:
        cap = min(cap, args.max_workers)
    return cap


def running_cap_reached(args, running):
    return len(running) >= effective_workload_cap(args)


def print_scheduler_status(prefix, queued, running, completed, failed):
    print(
        f"{prefix} queued={len(queued)} running={len(running)} "
        f"completed={completed} failed={failed}",
        flush=True,
    )


def try_launch_ready_experiments(
    queued,
    running,
    args,
    min_mem_available_kb,
    status_prefix,
    completed,
    failed,
    settle_seconds=0.0,
    max_launches=None,
):
    launched_any = False
    blocked_reason = ""
    launched_count = 0

    while (
        queued
        and not running_cap_reached(args, running)
        and (max_launches is None or launched_count < max_launches)
    ):
        available_kb = read_mem_available_kb()
        print(
            f"{status_prefix} "
            f"MemAvailable={available_kb} KiB "
            f"({format_memory_kb(available_kb)}), "
            f"threshold={min_mem_available_kb} KiB "
            f"({format_memory_kb(min_mem_available_kb)})",
            flush=True,
        )
        print_scheduler_status(
            status_prefix, queued, running, completed, failed)

        if available_kb is None:
            blocked_reason = "mem_unknown"
            break

        if available_kb < min_mem_available_kb:
            blocked_reason = "low_mem"
            break

        exp = queued.pop(0)
        running.append(launch_experiment(exp))
        launched_any = True
        launched_count += 1
        print_scheduler_status("[launch]", queued, running, completed, failed)

        if settle_seconds > 0 and queued and not running_cap_reached(args, running):
            time.sleep(settle_seconds)

    if queued and running_cap_reached(args, running):
        blocked_reason = "cap"

    return launched_any, blocked_reason


def memory_gated_run(exps, args):
    queued = list(exps)
    running = []
    completed = 0
    failed = 0
    min_mem_available_kb = int(args.min_free_ram_gb * 1024 * 1024)
    scan_interval_seconds = args.memory_scan_interval_minutes * 60
    next_scan_time = time.monotonic() + scan_interval_seconds
    initial_fill = True
    initial_fill_cap_logged = False

    print(
        "Memory gate: "
        f"MemAvailable >= {min_mem_available_kb} KiB "
        f"({format_memory_kb(min_mem_available_kb)}), "
        f"scan interval={args.memory_scan_interval_minutes} minutes",
        flush=True,
    )
    print(
        f"Max running workloads: {effective_workload_cap(args)}",
        flush=True,
    )
    if args.max_workers > 0 and args.max_workers < args.max_workloads:
        print(f"Legacy max-workers cap also applied: {args.max_workers}", flush=True)
    else:
        print("Legacy max-workers cap: disabled", flush=True)

    while queued or running:
        now = time.monotonic()
        still_running = []
        completed_this_round = False
        for state in running:
            process = state["process"]
            timeout_seconds = state["exp"].get("timeout_seconds", 0)
            timed_out = (
                timeout_seconds > 0
                and process.poll() is None
                and now - state["start_monotonic"] >= timeout_seconds
            )

            if timed_out:
                result = finalize_experiment(state, timed_out=True)
            elif process.poll() is None:
                still_running.append(state)
                continue
            else:
                result = finalize_experiment(state)

            completed_this_round = True
            completed += 1
            if result["returncode"] != 0:
                failed += 1
            print(result, flush=True)

        running = still_running
        if completed_this_round:
            initial_fill_cap_logged = False
            if queued and not initial_fill:
                next_scan_time = time.monotonic()

        if queued and initial_fill:
            launched_any, blocked_reason = try_launch_ready_experiments(
                queued,
                running,
                args,
                min_mem_available_kb,
                "[initial-fill]",
                completed,
                failed,
                settle_seconds=INITIAL_FILL_SETTLE_SECONDS,
            )
            if launched_any:
                initial_fill_cap_logged = False

            if blocked_reason == "mem_unknown":
                print(
                    "Initial fill paused: cannot read Linux MemAvailable.",
                    flush=True,
                )
                initial_fill = False
                next_scan_time = time.monotonic() + scan_interval_seconds
            elif blocked_reason == "low_mem":
                print(
                    "Initial fill complete: not enough free RAM to launch "
                    "the next benchmark.",
                    flush=True,
                )
                initial_fill = False
                next_scan_time = time.monotonic() + scan_interval_seconds

            if queued and running_cap_reached(args, running):
                if not initial_fill_cap_logged:
                    print(
                        "Initial fill paused: optional max-workers cap is reached.",
                        flush=True,
                    )
                    print_scheduler_status(
                        "[initial-fill]", queued, running, completed, failed)
                    initial_fill_cap_logged = True

            if launched_any:
                continue

        now = time.monotonic()
        if queued and not initial_fill and now >= next_scan_time:
            _, blocked_reason = try_launch_ready_experiments(
                queued,
                running,
                args,
                min_mem_available_kb,
                "[memory-scan]",
                completed,
                failed,
                max_launches=1,
            )

            if blocked_reason == "mem_unknown":
                print("Waiting: cannot read Linux MemAvailable.", flush=True)
            elif blocked_reason == "low_mem":
                print(
                    "Waiting: not enough free RAM to launch the next benchmark.",
                    flush=True,
                )
            elif blocked_reason == "cap":
                print("Waiting: optional max-workers cap is reached.", flush=True)

            next_scan_time = time.monotonic() + scan_interval_seconds

        if queued or running:
            if queued and not initial_fill:
                now = time.monotonic()
                sleep_seconds = max(0.1, min(30.0, next_scan_time - now))
            else:
                sleep_seconds = 5.0
            time.sleep(sleep_seconds)

    print_scheduler_status("[summary]", queued, running, completed, failed)
    return failed


def create_output_dir(args):
    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser().resolve()
    else:
        output_dir = (
            ROOT_DIR
            / "results"
            / datetime.now().strftime("%Y-%m-%d-%H-%M-%S-hugepage-sweep")
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def write_manifest(output_dir, page_sizes, args, ablation_configs):
    manifest = output_dir / "hugepage_manifest.txt"
    with manifest.open("w", encoding="utf-8") as f:
        f.write(f"Created: {datetime.now()}\n")
        f.write(f"Standalone runner: True\n")
        f.write(f"Page sizes: {args.page_sizes}\n")
        f.write(f"Configs: {args.configs or DEFAULT_CONFIGS}\n")
        f.write(
            "Resolved configs: "
            + ",".join(name for name, _ in ablation_configs)
            + "\n"
        )
        f.write(
            "Capacity-normalized GMMU TLB: "
            f"{args.capacity_normalized_gmmu_tlb}\n"
        )
        f.write(
            "Huge-page-aware PTCL line size: "
            f"{args.hugepage_aware_ptcl_line_size}\n"
        )
        mem_config = memory_config(args)
        f.write(f"Fast DRAM preset: {args.fast_dram}\n")
        f.write(
            "Memory config: "
            f'num_memory_banks={mem_config["num_memory_banks"]}, '
            f'bandwidth={mem_config["bandwidth"]}, '
            f'switch_latency={mem_config["switch_latency"]}, '
            f'dram_frequency_mhz={mem_config["dram_frequency_mhz"]}, '
            f'dram_timing_scale={mem_config["dram_timing_scale"]}, '
            f'dram_command_queue_size={mem_config["dram_command_queue_size"]}, '
            "dram_transaction_queue_size="
            f'{mem_config["dram_transaction_queue_size"]}\n'
        )
        if args.benchmarks:
            benchmark_selection = args.benchmarks
        else:
            benchmark_selection = "hugepage-pressure"
        f.write(f"Benchmarks: {benchmark_selection}\n")
        f.write(
            "Resolved benchmarks: "
            + ",".join(get_selected_benchmarks(args))
            + "\n"
        )
        f.write(f"Extra benchmark flags: {args.extra_benchmark_flags}\n")
        f.write("\nResolved page sizes:\n")
        for page_size in page_sizes:
            sets, ways = page_gmmu_tlb_shape(args, page_size)
            f.write(
                f'  {page_size["label"]}: '
                f'{page_size["bytes"]} bytes, '
                f'log2={page_size["log2"]}\n'
            )
            f.write(f"    GMMU TLB: {sets} sets x {ways} ways\n")
            f.write(
                "    GMMU PTCL line size: "
                f"{page_ptcl_line_size(args, page_size)}\n"
            )
    return manifest


def dry_run_commands(exps):
    for exp in exps:
        print(shlex.join(experiment_command(exp)), flush=True)


def validate_args(args):
    if args.max_workers < 0:
        raise ValueError("--max-workers must be non-negative")
    if args.max_workloads <= 0:
        raise ValueError("--max-workloads must be greater than 0")
    if args.max_workloads > DEFAULT_MAX_WORKLOADS:
        raise ValueError(
            f"--max-workloads cannot exceed {DEFAULT_MAX_WORKLOADS}"
        )
    mem_config = memory_config(args)
    num_memory_banks = mem_config["num_memory_banks"]
    if num_memory_banks <= 0:
        raise ValueError("--num-memory-banks must be positive")
    if num_memory_banks > 64:
        raise ValueError("--num-memory-banks must be 64 or less for this DRAM shape")
    if (4 * 1024 * 1024 * 1024) % num_memory_banks != 0:
        raise ValueError("--num-memory-banks must evenly divide 4GB")
    if mem_config["bandwidth"] <= 0:
        raise ValueError("--bandwidth must be positive")
    if mem_config["switch_latency"] < 0:
        raise ValueError("--switch-latency must be non-negative")
    if mem_config["dram_frequency_mhz"] <= 0:
        raise ValueError("--dram-frequency-mhz must be positive")
    if mem_config["dram_timing_scale"] <= 0:
        raise ValueError("--dram-timing-scale must be positive")
    if mem_config["dram_command_queue_size"] <= 0:
        raise ValueError("--dram-command-queue-size must be positive")
    if mem_config["dram_transaction_queue_size"] <= 0:
        raise ValueError("--dram-transaction-queue-size must be positive")
    if args.gmmu_tlb_num_sets <= 0:
        raise ValueError("--gmmu-tlb-num-sets must be positive")
    if args.gmmu_tlb_num_ways <= 0:
        raise ValueError("--gmmu-tlb-num-ways must be positive")
    if args.gmmu_ptcl_line_size <= 0 or args.gmmu_ptcl_line_size > 8:
        raise ValueError("--gmmu-ptcl-line-size must be in the range 1..8")
    if args.min_free_ram_gb < 0:
        raise ValueError("--min-free-ram-gb must be non-negative")
    if args.memory_scan_interval_minutes <= 0:
        raise ValueError("--memory-scan-interval-minutes must be greater than 0")


def prepare_page_runs(args, output_dir, page_sizes, ablation_configs):
    page_runs = []
    total_exps = 0

    for page_size in page_sizes:
        run_dir = output_dir / page_size["label"]
        run_dir.mkdir(parents=True, exist_ok=True)
        exps = make_page_exps(args, page_size, run_dir, ablation_configs)
        exps = filter_missing_metric_exps(exps, run_dir)
        total_exps += len(exps)
        page_runs.append((page_size, run_dir, exps))

    return page_runs, total_exps


def print_page_header(page_size, run_dir, exps):
    print(
        f'\n=== Page size {page_size["label"]} '
        f'(log2={page_size["log2"]}) ===',
        flush=True,
    )
    print(f"Results: {run_dir}", flush=True)
    print(f"Queued missing experiments: {len(exps)}", flush=True)


def main():
    args = parse_args()
    validate_args(args)
    page_sizes = unique_page_sizes(args.page_sizes)
    ablation_configs = build_ablation_configs(args)
    if args.only_config:
        ablation_configs = [c for c in ablation_configs if c[0] == args.only_config]
        if not ablation_configs:
            raise ValueError(f"no config named '{args.only_config}'")

    output_dir = create_output_dir(args)
    manifest = write_manifest(output_dir, page_sizes, args, ablation_configs)
    page_runs, total_exps = prepare_page_runs(
        args, output_dir, page_sizes, ablation_configs
    )

    print(f"Output directory: {output_dir}", flush=True)
    print(f"Manifest: {manifest}", flush=True)
    print(f"Queued {total_exps} missing experiments", flush=True)
    if args.fast_dram:
        mem_config = memory_config(args)
        print(
            "Fast DRAM config: "
            f'num_memory_banks={mem_config["num_memory_banks"]}, '
            f'bandwidth={mem_config["bandwidth"]}, '
            f'switch_latency={mem_config["switch_latency"]}, '
            f'dram_frequency_mhz={mem_config["dram_frequency_mhz"]}, '
            f'dram_timing_scale={mem_config["dram_timing_scale"]}, '
            f'dram_command_queue_size={mem_config["dram_command_queue_size"]}, '
            "dram_transaction_queue_size="
            f'{mem_config["dram_transaction_queue_size"]}',
            flush=True,
        )
    if args.photon:
        photon_defaults = selected_global_photon_flags(args)
        append_default_photon_tuning_flags(args, photon_defaults)
        print(f"Global Photon flags: {shlex.join(photon_defaults)}", flush=True)
    if args.timeout_minutes > 0:
        print(f"Experiment timeout: {args.timeout_minutes} minutes", flush=True)

    if args.dry_run:
        for page_size, run_dir, exps in page_runs:
            print_page_header(page_size, run_dir, exps)
            dry_run_commands(exps)
        return

    all_exps = [exp for _, _, exps in page_runs for exp in exps]
    if not all_exps:
        print("No missing-metrics experiments found.", flush=True)
        return

    if not args.skip_build:
        build_targets(all_exps)

    failures = []
    for page_size, run_dir, exps in page_runs:
        print_page_header(page_size, run_dir, exps)
        if not exps:
            continue
        failed = memory_gated_run(exps, args)
        if failed:
            failures.append((page_size["label"], failed))
            if not args.continue_on_error:
                break

    if failures:
        print("\nFailures:", flush=True)
        for label, failed in failures:
            print(f"  {label}: failed experiments={failed}", flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
