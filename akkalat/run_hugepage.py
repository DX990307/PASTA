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


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_PAGE_SIZES = "64kb,2mb"
DEFAULT_CONFIGS = "baseline,ptcl_mode_flex_iommu_assist"


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
        help="Continue to the next page size if one runall2 invocation fails.",
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


def build_runall_command(args, forwarded_args, output_dir, page_size):
    run_dir = output_dir / page_size["label"]
    run_dir.mkdir(parents=True, exist_ok=True)

    extra_flags = remove_log2_page_size_flags(
        shlex.split(args.extra_benchmark_flags)
    )
    extra_flags.append(f'-log2-page-size={page_size["log2"]}')

    cmd = [
        args.python,
        str(Path(args.runall_script).expanduser().resolve()),
        "--rerun-missing",
        str(run_dir),
        "--configs",
        args.configs,
        f"--extra-benchmark-flags={shlex.join(extra_flags)}",
    ]

    if args.benchmarks:
        cmd += ["--benchmarks", args.benchmarks]

    cmd += forwarded_args
    return cmd, run_dir


def write_manifest(output_dir, page_sizes, args, forwarded_args):
    manifest = output_dir / "hugepage_manifest.txt"
    with manifest.open("w", encoding="utf-8") as f:
        f.write(f"Created: {datetime.now()}\n")
        f.write(f"Page sizes: {args.page_sizes}\n")
        f.write(f"Configs: {args.configs}\n")
        if args.benchmarks:
            f.write(f"Benchmarks: {args.benchmarks}\n")
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
    return manifest


def main():
    args, forwarded_args = parse_args()
    page_sizes = unique_page_sizes(args.page_sizes)
    output_dir = create_output_dir(args)
    manifest = write_manifest(output_dir, page_sizes, args, forwarded_args)

    print(f"Output directory: {output_dir}", flush=True)
    print(f"Manifest: {manifest}", flush=True)

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


if __name__ == "__main__":
    main()
