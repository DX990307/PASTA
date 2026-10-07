#!/usr/bin/env python3
"""Portable, restartable supervisor for the frozen R3-R7 remote package."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "manifest.json"
RESULTS = ROOT / "results"
SUPERVISOR_LOG = RESULTS / "supervisor.log"
LOCK = ROOT / "supervisor.lock"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def mem_available_bytes():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("MemAvailable is missing from /proc/meminfo")


def log(message):
    line = f"{utc_now()} {message}"
    print(line, flush=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    with SUPERVISOR_LOG.open("a") as output:
        output.write(line + "\n")


def load_manifest():
    manifest = json.loads(MANIFEST.read_text())
    for relative, expected in manifest["binaries"].items():
        binary = ROOT / relative
        if not binary.is_file() or digest(binary) != expected:
            raise RuntimeError(f"Frozen binary is missing or changed: {relative}")
    for relative, expected in manifest["sources"].items():
        source = ROOT / relative
        if not source.is_file() or digest(source) != expected:
            raise RuntimeError(f"Frozen source archive is missing or changed: {relative}")
    if len(manifest["jobs"]) != manifest["job_count"]:
        raise RuntimeError("Manifest job count changed")
    if len({job["id"] for job in manifest["jobs"]}) != len(manifest["jobs"]):
        raise RuntimeError("Duplicate job IDs in manifest")
    return manifest


def job_dir(job):
    return RESULTS / job["id"]


def command_for(job):
    command = []
    for index, argument in enumerate(job["command"]):
        argument = argument.replace("{RESULT_DIR}", str(job_dir(job)))
        if index == 0:
            argument = str(ROOT / argument)
        command.append(argument)
    return command


def state_path(job):
    return job_dir(job) / "state.json"


def read_state(job):
    path = state_path(job)
    if not path.exists():
        return {"status": "queued"}
    return json.loads(path.read_text())


def save_state(job, **values):
    state = read_state(job)
    state.update(values)
    state["updated_at"] = utc_now()
    write_json(state_path(job), state)


def pid_command_matches(pid, command):
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, PermissionError):
        return False
    actual = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
    return actual == command


def output_exists(job):
    target = job_dir(job) / job["completion_file"]
    return target.is_file() and target.stat().st_size > 0


def monitor_started(job):
    stdout = job_dir(job) / "stdout.log"
    if not stdout.is_file():
        return False
    with stdout.open(errors="replace") as source:
        for line in source:
            if "Monitoring simulation with http://localhost:" in line:
                return True
    return False


def validate_job(job):
    command = command_for(job)
    if any(arg in {"-disable-servers", "--disable-servers"} for arg in command):
        raise RuntimeError(f"AkitaRTM is disabled in {job['id']}")
    if not output_exists(job):
        raise RuntimeError(f"Missing completion output for {job['id']}")
    if not monitor_started(job):
        raise RuntimeError(f"AkitaRTM startup URL missing for {job['id']}")
    if job["kind"] == "gpu":
        metrics = job_dir(job) / "metrics.csv"
        text = metrics.read_text(errors="replace")
        if "Driver" not in text or "total_time" not in text:
            raise RuntimeError(f"Driver.total_time missing from {job['id']}")
    else:
        result = json.loads((job_dir(job) / "result.json").read_text())
        if result.get("Scenario") != job["scenario"] or result.get("Assist") != job["assist"]:
            raise RuntimeError(f"R7 result identity mismatch in {job['id']}")


def launch(job):
    directory = job_dir(job)
    directory.mkdir(parents=True, exist_ok=True)
    command = command_for(job)
    stdout_path = directory / "stdout.log"
    output = stdout_path.open("w")
    output.write(f"Executing {shlex.join(command)}\n")
    output.write(f"Start time: {utc_now()}\n")
    output.write(f"Launch MemAvailable: {mem_available_bytes()} bytes\n")
    output.flush()
    process = subprocess.Popen(
        command,
        cwd=directory,
        stdout=output,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    save_state(
        job,
        status="running",
        pid=process.pid,
        started_at=utc_now(),
        command=command,
        stdout=str(stdout_path),
    )
    log(f"launched {job['id']} pid={process.pid}")
    return {"job": job, "process": process, "output": output}


def finish(active, returncode):
    job = active["job"]
    output = active.get("output")
    if output is not None:
        output.write(f"Return code: {returncode}\n")
        output.write(f"End time: {utc_now()}\n")
        output.close()
    try:
        if returncode != 0:
            raise RuntimeError(f"process returned {returncode}")
        validate_job(job)
    except Exception as error:
        save_state(
            job,
            status="failed",
            returncode=returncode,
            finished_at=utc_now(),
            error=str(error),
        )
        log(f"failed {job['id']} returncode={returncode}: {error}")
        return
    save_state(
        job,
        status="completed",
        returncode=returncode,
        finished_at=utc_now(),
        completion_sha256=digest(job_dir(job) / job["completion_file"]),
        akitartm_started=True,
    )
    log(f"completed {job['id']}")


def status_summary(manifest):
    counts = {name: 0 for name in ("queued", "running", "stale", "completed", "failed", "interrupted")}
    by_group = {}
    for job in manifest["jobs"]:
        state = read_state(job)
        status = state.get("status", "queued")
        if status == "running" and not pid_command_matches(state.get("pid"), command_for(job)):
            status = "stale"
        counts[status] = counts.get(status, 0) + 1
        group = by_group.setdefault(job["scope_task"], {})
        group[status] = group.get(status, 0) + 1
    return counts, by_group


def run():
    manifest = load_manifest()
    lock = LOCK.open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("Another supervisor already owns this package") from error

    active = {}
    queued = []
    for job in manifest["jobs"]:
        state = read_state(job)
        if state.get("status") == "completed":
            validate_job(job)
            continue
        if state.get("status") == "running":
            pid = state.get("pid")
            if isinstance(pid, int) and pid_command_matches(pid, command_for(job)):
                active[pid] = {"job": job, "process": None, "output": None}
                continue
            save_state(job, status="interrupted", finished_at=utc_now())
        queued.append(job)

    stop = False

    def request_stop(_signum, _frame):
        nonlocal stop
        stop = True
        log("stop requested; no new jobs will launch, active jobs are retained")

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    max_workers = manifest["max_workers"]
    reserve = manifest["reserve_gib"] * 1024 ** 3
    interval = manifest["launch_interval_seconds"]
    poll = manifest["poll_seconds"]
    last_launch = 0.0
    log(f"supervisor start queued={len(queued)} active={len(active)}")

    while queued or active:
        for pid, item in list(active.items()):
            process = item["process"]
            if process is not None:
                returncode = process.poll()
                if returncode is None:
                    continue
                finish(item, returncode)
                del active[pid]
            elif not pid_command_matches(pid, command_for(item["job"])):
                finish(item, 0 if output_exists(item["job"]) else -1)
                del active[pid]

        now = time.monotonic()
        if (
            not stop
            and queued
            and len(active) < max_workers
            and mem_available_bytes() >= reserve
            and now - last_launch >= interval
        ):
            job = queued.pop(0)
            item = launch(job)
            active[item["process"].pid] = item
            last_launch = time.monotonic()

        if stop:
            break
        time.sleep(poll)

    counts, _ = status_summary(manifest)
    log(f"supervisor stop counts={counts}")
    return 1 if counts.get("failed", 0) else 0


def show_status():
    manifest = load_manifest()
    counts, by_group = status_summary(manifest)
    print(json.dumps({
        "total": len(manifest["jobs"]),
        "counts": counts,
        "by_group": by_group,
        "mem_available_gib": round(mem_available_bytes() / 1024 ** 3, 3),
        "reuse": manifest["reuse"],
        "r7_scope": manifest["r7_scope"],
    }, indent=2, sort_keys=True))


def list_jobs():
    manifest = load_manifest()
    for job in manifest["jobs"]:
        print(f"{read_state(job).get('status', 'queued'):11} {job['id']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["run", "status", "list"], nargs="?", default="run")
    args = parser.parse_args()
    if args.action == "run":
        return run()
    if args.action == "status":
        show_status()
    else:
        list_jobs()
    return 0


if __name__ == "__main__":
    sys.exit(main())
