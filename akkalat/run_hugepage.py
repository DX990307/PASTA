#!/usr/bin/env python3
"""Run 400latency sweeps across multiple GPU page sizes.

This script is a thin wrapper around runall2.py. It creates one result
subdirectory per page size and forwards the sweep to runall2.py with the
corresponding -log2-page-size flag.
"""

import argparse
from datetime import datetime
import math
import os
from pathlib import Path
import shlex
import subprocess
import sys

import runall2


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_PAGE_SIZES = "64kb,2mb"
DEFAULT_CONFIGS = "baseline,ptcl_mode_flex_iommu_assist"
BASE_GMMU_TLB_SETS = 16
BASE_GMMU_TLB_WAYS = 16
BASE_PAGE_SIZE_BYTES = 4 * 1024
BASE_PTCL_LINE_SIZE = 8
MIN_HUGEPAGE_PTCL_LINE_SIZE = 4


PAGE_SIZE_ALIASES = {
    "4k": 4 * 1024,
    "4kb": 4 * 1024,
    "64k": 64 * 1024,
    "64kb": 64 * 1024,
    "2m": 2 * 1024 * 1024,
    "2mb": 2 * 1024 * 1024,
}


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Run huge-page experiments by sweeping -log2-page-size values. "
            "Unknown options are forwarded to runall2.py."
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
        default=DEFAULT_CONFIGS,
        help=(
            "Comma-separated runall2 config names. Defaults to baseline and "
            "ptcl_mode_flex_iommu_assist."
        ),
    )
    parser.add_argument(
        "--benchmarks",
        default="",
        help=(
            "Comma-separated runall2 benchmark list or preset. Empty keeps "
            "runall2.py's default benchmark set."
        ),
    )
    parser.add_argument(
        "--extra-benchmark-flags",
        default="",
        help=(
            "Extra benchmark flags forwarded to runall2.py. Any existing "
            "-log2-page-size flag is replaced by this sweep."
        ),
    )
    parser.add_argument(
        "--hugepage-20260626-flags",
        "--hugepage-paper-flags",
        dest="hugepage_20260626_flags",
        action="store_true",
        help=(
            "Forward runall2.py's 2026-06-26 huge-page benchmark flag preset "
            "instead of spelling out the long --extra-benchmark-flags string."
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
    parser.add_argument(
        "--output-dir",
        default="",
        help=(
            "Top-level output directory. Empty creates "
            "results/<timestamp>-hugepage-sweep."
        ),
    )
    parser.add_argument(
        "--runall-script",
        default=str(ROOT_DIR / "runall2.py"),
        help="Path to runall2.py.",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable used to invoke runall2.py.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help=(
            "In --sequential-page-sizes mode, continue to the next page size "
            "if one runall2 invocation fails."
        ),
    )
    parser.add_argument(
        "--sequential-page-sizes",
        action="store_true",
        help=(
            "Run one runall2.py invocation per page size. By default, all "
            "page sizes are mixed into one scheduler queue."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the runall2.py commands without executing them.",
    )
    return parser.parse_known_args()


def parse_csv(value):
    return [item.strip() for item in value.split(",") if item.strip()]


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

        # Small integers are treated as log2 page-size values.
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


def page_extra_flags(args, page_size):
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
        sets, ways = capacity_normalized_gmmu_tlb_shape(page_size["bytes"])
        extra_flags += [
            f"-gmmu-tlb-num-sets={sets}",
            f"-gmmu-tlb-num-ways={ways}",
        ]

    if args.hugepage_aware_ptcl_line_size:
        extra_flags = remove_flag_names(
            extra_flags,
            {
                "-gmmu-ptcl-line-size",
                "--gmmu-ptcl-line-size",
            },
        )
        extra_flags.append(
            "-gmmu-ptcl-line-size="
            f'{hugepage_aware_ptcl_line_size(page_size["bytes"])}'
        )

    return extra_flags


def build_runall_argv(args, forwarded_args, page_size):
    runall_argv = [
        "--configs",
        args.configs,
        f"--extra-benchmark-flags={shlex.join(page_extra_flags(args, page_size))}",
    ]

    if args.hugepage_20260626_flags:
        runall_argv.append("--hugepage-20260626-flags")

    if args.benchmarks:
        runall_argv += ["--benchmarks", args.benchmarks]

    runall_argv += forwarded_args
    return runall_argv


def build_runall_command(args, forwarded_args, output_dir, page_size):
    run_dir = output_dir / page_size["label"]
    run_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        args.python,
        str(Path(args.runall_script).expanduser().resolve()),
        "--rerun-missing",
        str(run_dir),
        *build_runall_argv(args, forwarded_args, page_size),
    ]
    return cmd, run_dir


def write_manifest(output_dir, page_sizes, args, forwarded_args):
    manifest = output_dir / "hugepage_manifest.txt"
    with manifest.open("w", encoding="utf-8") as f:
        f.write(f"Created: {datetime.now()}\n")
        f.write(f"Page sizes: {args.page_sizes}\n")
        f.write(f"Configs: {args.configs}\n")
        f.write(
            "Capacity-normalized GMMU TLB: "
            f"{args.capacity_normalized_gmmu_tlb}\n"
        )
        f.write(
            "Huge-page-aware PTCL line size: "
            f"{args.hugepage_aware_ptcl_line_size}\n"
        )
        f.write(
            "Page-size scheduling: "
            f"{'sequential' if args.sequential_page_sizes else 'combined-round-robin'}\n"
        )
        if args.benchmarks:
            f.write(f"Benchmarks: {args.benchmarks}\n")
        f.write(f"Hugepage 2026-06-26 flags: {args.hugepage_20260626_flags}\n")
        f.write(f"Extra benchmark flags: {args.extra_benchmark_flags}\n")
        if forwarded_args:
            f.write(f"Forwarded runall2 args: {shlex.join(forwarded_args)}\n")
        f.write("\nResolved page sizes:\n")
        for page_size in page_sizes:
            f.write(
                f'  {page_size["label"]}: '
                f'{page_size["bytes"]} bytes, '
                f'log2={page_size["log2"]}\n'
            )
            if args.capacity_normalized_gmmu_tlb:
                sets, ways = capacity_normalized_gmmu_tlb_shape(page_size["bytes"])
                f.write(
                    f"    capacity-normalized GMMU TLB: "
                    f"{sets} sets x {ways} ways\n"
                )
            if args.hugepage_aware_ptcl_line_size:
                f.write(
                    "    huge-page-aware PTCL line size: "
                    f'{hugepage_aware_ptcl_line_size(page_size["bytes"])}\n'
                )
    return manifest


def run_sequential_page_sizes(args, forwarded_args, output_dir, page_sizes):
    failures = []
    for page_size in page_sizes:
        cmd, run_dir = build_runall_command(
            args, forwarded_args, output_dir, page_size
        )
        print(
            f'\n=== Page size {page_size["label"]} '
            f'(log2={page_size["log2"]}) ===',
            flush=True,
        )
        print(f"Results: {run_dir}", flush=True)
        print(shlex.join(cmd), flush=True)

        if args.dry_run:
            continue

        result = subprocess.run(cmd, cwd=ROOT_DIR)
        if result.returncode != 0:
            failures.append((page_size["label"], result.returncode))
            print(
                f'Page-size run failed: {page_size["label"]}, '
                f"returncode={result.returncode}",
                flush=True,
            )
            if not args.continue_on_error:
                break

    if failures:
        print("\nFailures:", flush=True)
        for label, returncode in failures:
            print(f"  {label}: returncode={returncode}", flush=True)
        raise SystemExit(1)


def interleave_experiment_groups(groups):
    interleaved = []
    max_len = max((len(exps) for _, exps in groups), default=0)
    for index in range(max_len):
        for _, exps in groups:
            if index < len(exps):
                interleaved.append(exps[index])
    return interleaved


def build_combined_page_experiments(args, forwarded_args, output_dir, page_sizes):
    page_exp_groups = []
    scheduler_args = None

    for page_size in page_sizes:
        run_dir = output_dir / page_size["label"]
        run_dir.mkdir(parents=True, exist_ok=True)

        runall_args = runall2.parse_args(
            build_runall_argv(args, forwarded_args, page_size)
        )
        runall_args.dry_run = args.dry_run
        runall2.validate_args(runall_args)

        exps = runall2.configured_experiments(runall_args)
        if not exps:
            print(
                f'No experiments configured for page size {page_size["label"]}.',
                flush=True,
            )
            continue

        exps = runall2.filter_missing_metric_exps(exps, str(run_dir))
        common_flags = runall2.build_common_flags(runall_args)
        runall2.prepare_experiments(runall_args, exps, common_flags)

        for exp in exps:
            exp["results_dir"] = str(run_dir)
            exp["page_size_label"] = page_size["label"]
            exp["page_size_log2"] = page_size["log2"]

        print(
            f'Queued missing experiments for {page_size["label"]}: {len(exps)}',
            flush=True,
        )
        page_exp_groups.append((page_size["label"], exps))

        if scheduler_args is None:
            scheduler_args = runall_args

    return interleave_experiment_groups(page_exp_groups), scheduler_args


def run_combined_page_sizes(args, forwarded_args, output_dir, page_sizes):
    exps, scheduler_args = build_combined_page_experiments(
        args, forwarded_args, output_dir, page_sizes
    )
    if not exps:
        print("No missing-metrics experiments found for any page size.", flush=True)
        return
    if scheduler_args is None:
        raise ValueError("no runall2 scheduler args were constructed")

    runall2.output_dir = str(output_dir)
    print(
        f"Queued {len(exps)} missing experiments across "
        f"{len(page_sizes)} page sizes",
        flush=True,
    )
    runall2.run_experiment_queue(scheduler_args, exps)


def main():
    args, forwarded_args = parse_args()
    page_sizes = unique_page_sizes(args.page_sizes)
    output_dir = create_output_dir(args)
    manifest = write_manifest(output_dir, page_sizes, args, forwarded_args)

    print(f"Output directory: {output_dir}", flush=True)
    print(f"Manifest: {manifest}", flush=True)

    if args.sequential_page_sizes:
        run_sequential_page_sizes(args, forwarded_args, output_dir, page_sizes)
    else:
        run_combined_page_sizes(args, forwarded_args, output_dir, page_sizes)


if __name__ == "__main__":
    main()
