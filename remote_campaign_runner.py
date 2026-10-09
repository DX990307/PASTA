#!/usr/bin/env python3
"""Independent PTW configuration sweep; no simulator correctness changes."""

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "manifest.json"
RESULTS = ROOT / "results"
BENCHMARKS = ["matrixtranspose", "matrixmultiplication-ptw", "aes", "bitonicsort",
              "fastwalshtransform", "fft", "fir", "floydwarshall", "im2col", "kmeans",
              "pagerank", "relu", "simpleconvolution", "spmv"]
PROFILES = {"iotlb_half": 32, "iotlb_double": 128}
MODES = {"baseline": "baseline", "pasta": "ptcl_mode_flex_iommu_assist"}


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized(command):
    result = {}
    for argument in command[1:]:
        key, separator, value = argument.partition("=")
        if key != "-metric-file-name":
            result[key] = value if separator else "true"
    return result


def build_jobs(historical):
    jobs = []
    for benchmark in BENCHMARKS:
        for profile, sets in PROFILES.items():
            for mode in MODES:
                reference = historical[benchmark][MODES[mode]]
                original = reference["command"]
                command = ["bin/simulator", *(a for a in original[1:] if not a.startswith("-metric-file-name=")),
                           f"-iotlb-num-sets={sets}", "-metric-file-name={RESULT_DIR}/metrics"]
                flags = normalized(command)
                if {k:v for k,v in flags.items() if k != "-iotlb-num-sets"} != normalized(original):
                    raise RuntimeError("Unexpected non-IOTLB-set command changes")
                name = ("base_" if mode == "baseline" else "pasta_") + profile
                jobs.append({"id":f"IOTLB__{name}__{benchmark}", "scope_task":"IOTLB", "benchmark":benchmark,
                             "profile":profile,"mode":mode,"config_name":name,"command":command,"historical_command":original,
                             "configuration":{"iotlb_num_sets":sets,"iotlb_num_ways":32,"iotlb_entries":sets*32,"iotlb_mshrs":64,
                                 "mesh_switch_latency_cycles":32,"mesh_bandwidth_units":48,"mesh_bandwidth_gb_s":768,
                                 "gpm_count":48,"cus_per_gpm":32,"gmmu_ptw_count":4,"iommu_ptw_count":16,
                                 "iommu_pw_queue_capacity":64,"max_wg":76800,"gmmu_pte_lookup_cycles":32,"gmmu_lookup_slots":8,
                                 "reference_ptcl_set_lookup_cycles":64 if mode == "pasta" else None}})
    return jobs


def prepare():
    if MANIFEST.exists():
        load_manifest()
        print("Existing manifest and results retained")
        return
    binary = ROOT / "bin/simulator"
    if not binary.is_file():
        raise RuntimeError("Run bash build.sh first")
    jobs = build_jobs(json.loads((ROOT / "historical_commands.json").read_text())["commands"])
    snapshot = {"git_head": "4b5e718c052f2d459cd713b3ee5c73d724092c4c"}
    files = {str(f.relative_to(ROOT)): digest(f) for f in ROOT.rglob("*.go")
             if f.is_file() and f.suffix == ".go"}
    write_json(MANIFEST, {"created_at": now(), "job_count": len(jobs), "jobs": jobs,
                        "source_snapshot": "hyperscan-current-20260619 plus IOTLB set-count parameter only",
                        "source_git_head": snapshot["git_head"], "binaries": {"bin/simulator": digest(binary)},
                        "sources": files, "max_parallel": 17, "reserve_gib": 30,
                        "launch_interval_seconds": 20, "poll_seconds": 10,
                        "reuse_policy": "No historical performance results imported; resume skips completed configurations regardless of binary provenance.",
                        "source_changes": "Only IOTLB set count parameterized; ways32 and MSHRs64 unchanged; no correctness or latency fixes."})
    write_json(ROOT / "configs-before-launch.json", [{"id": j["id"], "command": j["command"],
               "configuration": j["configuration"]} for j in jobs])
    print(f"Prepared {len(jobs)} jobs; MT first, MM second; no benchmarks launched")


def load_manifest():
    manifest = json.loads(MANIFEST.read_text())
    if len(manifest["jobs"]) != 56 or len({j["id"] for j in manifest["jobs"]}) != 56:
        raise RuntimeError("Invalid IOTLB 56-job list")
    # Freeze checks prevent editing an active package, not reuse by binary identity.
    for relative, expected in {**manifest["binaries"], **manifest["sources"]}.items():
        if digest(ROOT / relative) != expected:
            raise RuntimeError(f"Frozen package changed: {relative}; preserve results and use a new package")
    return manifest


def job_dir(job):
    return RESULTS / job["id"]


def command_for(job):
    return [str(ROOT / argument) if i == 0 else argument.replace("{RESULT_DIR}", str(job_dir(job)))
            for i, argument in enumerate(job["command"])]


def read_state(job):
    path = job_dir(job) / "state.json"
    return json.loads(path.read_text()) if path.exists() else {"status": "queued"}


def live_command(pid):
    try:
        if Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z":
            return []
        return [part.decode(errors="replace") for part in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if part]
    except (OSError, IndexError):
        return []


def available_gib():
    return next(int(line.split()[1])/1024**2 for line in Path("/proc/meminfo").read_text().splitlines()
                if line.startswith("MemAvailable:"))


def driver_time(directory):
    found = []
    with (directory / "metrics.csv").open(newline="") as source:
        for row in csv.reader(source):
            row = [item.strip() for item in row]
            if len(row) >= 4 and row[1:3] == ["Driver", "total_time"]:
                found.append(float(row[3]))
    if len(found) != 1 or not math.isfinite(found[0]) or found[0] <= 0:
        raise RuntimeError("Missing unique positive Driver.total_time")
    if "Monitoring simulation with http://localhost:" not in (directory / "stdout.log").read_text(errors="replace"):
        raise RuntimeError("AkitaRTM startup record missing")
    return found[0]


def worker(job):
    directory = job_dir(job)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (directory / "stdout.log").exists():
            raise RuntimeError("Refusing to overwrite an existing experiment log")
        command = command_for(job)
        current = {"status": "running", "started_at": now(), "worker_pid": os.getpid(), "command": command}
        with (directory / "stdout.log").open("x") as output:
            output.write(f"Executing {shlex.join(command)}\nStart time: {now()}\n")
            output.flush()
            process = subprocess.Popen(command, cwd=ROOT / "akkalat", stdout=output,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            current["pid"] = process.pid
            write_json(directory / "state.json", current)
            returncode = process.wait()
            output.write(f"Return code: {returncode}\nEnd time: {now()}\n")
        current.update(status="failed", finished_at=now(), returncode=returncode)
        if returncode == 0:
            try:
                current["driver_time_s"] = driver_time(directory)
                current["status"] = "completed"
            except (OSError, ValueError, RuntimeError) as error:
                current["error"] = str(error)
        if returncode == -signal.SIGKILL:
            write_json(ROOT / "admission-paused.json", {"job": job["id"], "reason": "SIGKILL; possible OOM, requires inspection"})
        write_json(directory / "state.json", current)


def status_summary(jobs):
    rows = []
    for job in jobs:
        current = read_state(job)
        if current["status"] in {"running", "starting"}:
            if not live_command(current.get("worker_pid", -1)) and live_command(current.get("pid", -1)) != command_for(job):
                current["status"] = "stale"
        rows.append({"id": job["id"], **current})
    return {"counts": dict(Counter(row["status"] for row in rows)), "total": len(jobs),
            "mem_available_gib": round(available_gib(), 3),
            "admission_paused": (ROOT / "admission-paused.json").exists(), "jobs": rows}


def run(manifest, workers):
    RESULTS.mkdir(exist_ok=True)
    with (ROOT / "supervisor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        jobs = manifest["jobs"]
        print((ROOT / "configs-before-launch.json").read_text(), flush=True)
        stop = [False]
        signal.signal(signal.SIGINT, lambda *_: stop.__setitem__(0, True))
        signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__(0, True))
        processes = {}
        last_launch = 0.0
        while not stop[0]:
            for pid, (process, job) in list(processes.items()):
                code = process.poll()
                if code is not None:
                    if read_state(job)["status"] in {"starting", "running"}:
                        write_json(job_dir(job) / "state.json", {"status": "failed", "error": "Worker exited without final record", "returncode": code})
                    del processes[pid]
            snapshot = status_summary(jobs)
            write_json(RESULTS / "status.json", snapshot)
            active = sum(snapshot["counts"].get(name, 0) for name in ("running", "starting"))
            waiting = [j for j in jobs if read_state(j)["status"] == "queued"]
            if not active and not waiting:
                break
            if (waiting and active < workers and not snapshot["admission_paused"]
                    and available_gib() >= manifest["reserve_gib"]
                    and time.monotonic() - last_launch >= manifest["launch_interval_seconds"]):
                job = waiting[0]
                write_json(job_dir(job) / "state.json", {"status": "starting", "started_at": now()})
                handle = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "worker", "--job", job["id"]], start_new_session=True)
                processes[handle.pid] = (handle, job)
                print(f"{now()} LAUNCH {job['id']} worker={handle.pid}", flush=True)
                last_launch = time.monotonic()
            time.sleep(manifest["poll_seconds"])
        print("Supervisor stopped; existing workers and simulators are left running", flush=True)


def summary(jobs):
    rows = []
    for benchmark in BENCHMARKS:
        for profile in PROFILES:
            pair = [next(j for j in jobs if j["benchmark"]==benchmark and j["profile"]==profile and j["mode"]==mode) for mode in MODES]
            if all(read_state(j)["status"] == "completed" for j in pair):
                bt,pt = [driver_time(job_dir(j)) for j in pair]
                speedup=bt/pt
                rows.append({"benchmark":benchmark,"profile":profile,"baseline_time_s":bt,"pasta_time_s":pt,"speedup":speedup,"performance_improvement_pct":(speedup-1)*100})
    RESULTS.mkdir(exist_ok=True)
    with (RESULTS/"speedups.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=["benchmark","profile","baseline_time_s","pasta_time_s","speedup","performance_improvement_pct"])
        w.writeheader();w.writerows(rows)
    print(f"Wrote {len(rows)} completed pairs to results/speedups.csv")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "list", "status", "run", "worker", "summary"])
    parser.add_argument("--groups", choices=["IOTLB"], default="IOTLB")
    parser.add_argument("--workers", type=int)
    parser.add_argument("--job")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
        return
    manifest = load_manifest()
    jobs = manifest["jobs"]
    if args.action == "list":
        for job in jobs:
            print(job["id"], shlex.join(command_for(job)))
    elif args.action == "status":
        print(json.dumps(status_summary(jobs), indent=2))
    elif args.action == "summary":
        summary(jobs)
    elif args.action == "worker":
        worker(next(j for j in jobs if j["id"] == args.job))
    else:
        workers = args.workers if args.workers is not None else manifest["max_parallel"]
        if workers <= 0:
            parser.error("workers must be positive")
        run(manifest, workers)


if __name__ == "__main__":
    main()
