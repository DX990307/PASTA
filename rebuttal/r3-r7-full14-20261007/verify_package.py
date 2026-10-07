#!/usr/bin/env python3
"""Verify remote package hashes, scope, reuse, and historical-command fidelity."""

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def key(argument):
    return argument.split("=", 1)[0]


def main():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    for group in ("binaries", "sources"):
        for relative, expected in manifest[group].items():
            path = ROOT / relative
            if not path.is_file() or digest(path) != expected:
                raise ValueError(f"{group} artifact missing or changed: {relative}")

    jobs = manifest["jobs"]
    expected_counts = {"R3": 56, "R4": 14, "R5": 56, "R6": 14, "R7": 4}
    actual_counts = {scope: sum(job["scope_task"] == scope for job in jobs) for scope in expected_counts}
    if actual_counts != expected_counts or len(jobs) != 144 or len({job["id"] for job in jobs}) != 144:
        raise ValueError(f"Unexpected scope matrix: {actual_counts}")

    full14 = {
        "aes", "bitonicsort", "fastwalshtransform", "fft", "fir", "floydwarshall",
        "im2col", "kmeans", "matrixmultiplication-ptw", "matrixtranspose", "pagerank",
        "relu", "simpleconvolution", "spmv",
    }
    variants = {
        "R3": {"baseline", "pasta", "neighbor", "latpc"},
        "R4": {"pasta_no_plt"},
        "R5": {"baseline_g8_i16", "baseline_g4_i32", "pasta_g8_i16", "pasta_g4_i32"},
        "R6": {"pasta_no_assist"},
    }
    for scope, wanted in variants.items():
        scoped = [job for job in jobs if job["scope_task"] == scope]
        if {job["variant"] for job in scoped} != wanted:
            raise ValueError(f"Wrong {scope} variants")
        for variant in wanted:
            if {job["benchmark"] for job in scoped if job["variant"] == variant} != full14:
                raise ValueError(f"Incomplete FULL14 coverage for {scope}/{variant}")

    audited = 0
    for job in jobs:
        command = job["command"]
        if any(argument in {"-disable-servers", "--disable-servers"} for argument in command):
            raise ValueError(f"AkitaRTM disabled in {job['id']}")
        if job["kind"] != "gpu":
            continue
        audited += 1
        historical = job["historical_arguments"]
        target = [argument for argument in command[1:] if not argument.startswith("-metric-file-name=")]
        changed = {key(argument) for argument in job["target_flag_changes"]}
        if ([argument for argument in historical if key(argument) not in changed]
                != [argument for argument in target if key(argument) not in changed]):
            raise ValueError(f"Non-target command drift in {job['id']}")
        for flag in ("-ptw-demand-pte-only", "-mmutlb-demand-pte-only"):
            if (flag in historical) != (flag in target):
                raise ValueError(f"Demand-PTE flag changed in {job['id']}: {flag}")
        if "-max-wg=76800" not in target:
            raise ValueError(f"Wrong workgroup cap in {job['id']}")

    r7 = [job for job in jobs if job["scope_task"] == "R7"]
    expected_r7 = {(scenario, assist) for scenario in ("native-burst", "capacity-race") for assist in (False, True)}
    if {(job["scenario"], job["assist"]) for job in r7} != expected_r7:
        raise ValueError("Wrong R7 controlled-flow matrix")
    if any(job["workload_scope"] != "not-applicable-controlled-open-loop-flow" for job in r7):
        raise ValueError("R7 incorrectly labeled as a GPU workload")

    print(json.dumps({
        "passed": True,
        "jobs": len(jobs),
        "audited_gpu_commands": audited,
        "scope_counts": actual_counts,
        "full14": sorted(full14),
        "r7_unique_controlled_flows": len(r7),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
