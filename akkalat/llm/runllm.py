#!/usr/bin/env python3
"""Small wrapper for running LLM-style workloads through runall2.py."""

import argparse
from pathlib import Path
import shlex
import subprocess
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]

PROFILES = {
    "tiny": {
        "description": "Fast smoke test.",
        "flags": [
            "-bert-mode=block",
            "-bert-size=tiny",
            "-bert-batch-size=1",
            "-bert-seq-len=2",
            "-bert-hidden-size=16",
            "-bert-num-heads=4",
            "-bert-num-layers=1",
            "-bert-intermediate-size=32",
            "-gpt-mode=block",
            "-gpt-size=tiny",
            "-gpt-batch-size=1",
            "-gpt-seq-len=2",
            "-gpt-hidden-size=16",
            "-gpt-num-heads=4",
            "-gpt-num-layers=1",
            "-gpt-intermediate-size=32",
            "-llminfer-mode=mixed",
            "-llminfer-size=tiny",
        ],
    },
    "small": {
        "description": "Default debug profile.",
        "flags": [
            "-bert-mode=full",
            "-bert-size=custom",
            "-bert-batch-size=1",
            "-bert-seq-len=32",
            "-bert-hidden-size=512",
            "-bert-num-heads=8",
            "-bert-num-layers=1",
            "-bert-intermediate-size=2048",
            "-gpt-mode=full",
            "-gpt-size=custom",
            "-gpt-batch-size=1",
            "-gpt-seq-len=32",
            "-gpt-hidden-size=512",
            "-gpt-num-heads=8",
            "-gpt-num-layers=1",
            "-gpt-intermediate-size=2048",
            "-llminfer-mode=mixed",
            "-llminfer-size=small",
        ],
    },
    "7b": {
        "description": "One-layer 7B-shape profile with about a 1GiB active footprint.",
        "flags": [
            "-bert-mode=full",
            "-bert-size=custom",
            "-bert-batch-size=1",
            "-bert-seq-len=5120",
            "-bert-hidden-size=4096",
            "-bert-num-heads=32",
            "-bert-num-layers=1",
            "-bert-intermediate-size=11008",
            "-gpt-mode=full",
            "-gpt-size=7b-proxy",
            "-gpt-batch-size=1",
            "-gpt-seq-len=5120",
            "-gpt-num-layers=1",
            "-llminfer-mode=decode",
            "-llminfer-size=7b-proxy",
        ],
    },
    "custom": {
        "description": "No preset flags; use --extra-benchmark-flags.",
        "flags": [],
    },
}


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "LLM workload runner based on runall2.py. Unknown args are "
            "forwarded."
        )
    )
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default="small",
        help="Workload-size preset.",
    )
    parser.add_argument(
        "--list-profiles",
        action="store_true",
        help="Print available profiles and exit.",
    )
    parser.add_argument(
        "--benchmarks",
        default="bert,gpt,llminference",
        help="Comma-separated workload list. Default: bert,gpt,llminference.",
    )
    parser.add_argument(
        "--configs",
        default="baseline",
        help="runall2 config list, e.g. baseline or baseline,sample_all.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="Maximum concurrent experiments.",
    )
    parser.add_argument(
        "--sampled-warmups",
        default="",
        help="Forwarded to runall2.py.",
    )
    parser.add_argument(
        "--sampled-granularities",
        default="",
        help="Forwarded to runall2.py.",
    )
    parser.add_argument(
        "--extra-benchmark-flags",
        default="",
        help=(
            "Extra benchmark flags appended after the profile flags, so they "
            "can override the preset."
        ),
    )
    parser.add_argument(
        "--log-subtasks",
        dest="log_subtasks",
        action="store_true",
        default=True,
        help="Print model steps and sub-operators. Enabled by default.",
    )
    parser.add_argument(
        "--no-log-subtasks",
        dest="log_subtasks",
        action="store_false",
        help="Disable model step and sub-operator logging.",
    )
    parser.add_argument(
        "--enable-servers",
        action="store_true",
        help="Deprecated no-op. Servers are always left enabled.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print runall2 commands without building or running.",
    )
    return parser.parse_known_args()


def print_profiles():
    for name in sorted(PROFILES):
        profile = PROFILES[name]
        print(f"{name}: {profile['description']}")
        if profile["flags"]:
            print("  " + shlex.join(profile["flags"]))


def build_command(args, forwarded):
    profile = PROFILES[args.profile]
    extra_flags = shlex.split(args.extra_benchmark_flags)
    log_flags = []
    if args.log_subtasks:
        log_flags = [
            "-bert-log-subtasks",
            "-gpt-log-subtasks",
            "-llminfer-log-subtasks",
        ]

    benchmark_flags = profile["flags"] + log_flags + extra_flags

    cmd = [
        sys.executable,
        str(ROOT_DIR / "runall2.py"),
        "--benchmarks",
        args.benchmarks,
        "--configs",
        args.configs,
        "--max-workers",
        str(args.max_workers),
        "--extra-benchmark-flags",
        shlex.join(benchmark_flags),
    ]

    if args.sampled_warmups:
        cmd += ["--sampled-warmups", args.sampled_warmups]
    if args.sampled_granularities:
        cmd += ["--sampled-granularities", args.sampled_granularities]
    if args.dry_run:
        cmd.append("--dry-run")

    cmd += forwarded
    return cmd


def main():
    args, forwarded = parse_args()
    if args.list_profiles:
        print_profiles()
        return

    cmd = build_command(args, forwarded)
    print("Running:", flush=True)
    print(shlex.join(cmd), flush=True)
    raise SystemExit(subprocess.call(cmd, cwd=ROOT_DIR.parent))


if __name__ == "__main__":
    main()
