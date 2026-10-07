#!/usr/bin/env python3
"""Requeue only failures caused by the R3 simple-SOTA v13 flag guard."""

import fcntl
import json
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parent
LOCK = ROOT / "supervisor.lock"
KNOWN_MESSAGES = (
    "simple SOTA comparison requires ordinary VPN MSHRs",
    "abstract Neighbor requires demand-only native walkers",
    "LATPC requires ordinary demand-only walkers",
)


def main():
    lock = LOCK.open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError("Stop remote_campaign_runner.py before applying v14")

    manifest = json.loads((ROOT / "manifest.json").read_text())
    reset = []
    skipped = []
    for job in manifest["jobs"]:
        if job.get("scope_task") != "R3" or job.get("variant") not in {"neighbor", "latpc"}:
            continue
        directory = ROOT / "results" / job["id"]
        state_path = directory / "state.json"
        if not state_path.is_file():
            continue
        state = json.loads(state_path.read_text())
        if state.get("status") != "failed":
            continue
        stdout_path = directory / "stdout.log"
        output = stdout_path.read_text(errors="replace") if stdout_path.is_file() else ""
        if not any(message in output for message in KNOWN_MESSAGES):
            skipped.append(job["id"])
            continue
        backup = directory / "state.pre-v14.json"
        if not backup.exists():
            shutil.copy2(state_path, backup)
        state.update({
            "status": "queued",
            "v14_retry": True,
            "v14_prior_error": state.get("error", ""),
        })
        temporary = state_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        temporary.replace(state_path)
        reset.append(job["id"])

    print(json.dumps({"reset": reset, "reset_count": len(reset), "skipped": skipped}, indent=2))
    if skipped:
        print("Refused to reset failures without the known v13 panic.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
