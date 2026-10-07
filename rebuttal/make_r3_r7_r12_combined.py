#!/usr/bin/env python3
"""Create one global 16-worker queue for the R3-R7 and R12 packages."""

import hashlib
import json
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parent
R3 = ROOT / "r3-r7-full14-20261007"
R12 = ROOT / "r12-sota-simple-full14-20261007"
OUTPUT = ROOT / "r3-r7-r12-full14-20261007"


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def load(package):
    manifest = json.loads((package / "manifest.json").read_text())
    for group in ("binaries", "sources"):
        for relative, expected in manifest[group].items():
            path = package / relative
            if not path.is_file() or digest(path) != expected:
                raise RuntimeError(f"Changed input artifact: {path}")
    return manifest


def prefix_job(job, package_name):
    copied = json.loads(json.dumps(job))
    copied["command"][0] = f"../{package_name}/{copied['command'][0]}"
    copied["source_package"] = package_name
    return copied


def interleave(left, right):
    output = []
    size = max(len(left), len(right))
    for index in range(size):
        if index < len(left):
            output.append(left[index])
        if index < len(right):
            output.append(right[index])
    return output


VERIFY = r'''#!/usr/bin/env python3
import hashlib, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""): h.update(chunk)
    return h.hexdigest()

m = json.loads((ROOT / "manifest.json").read_text())
for group in ("binaries", "sources"):
    for relative, expected in m[group].items():
        path = ROOT / relative
        if not path.is_file() or digest(path) != expected: raise ValueError(f"changed artifact: {relative}")
jobs = m["jobs"]
if len(jobs) != 200 or len({j["id"] for j in jobs}) != 200: raise ValueError("expected 200 unique jobs")
counts = {scope: sum(j["scope_task"] == scope for j in jobs) for scope in ("R3", "R4", "R5", "R6", "R7", "R12")}
if counts != {"R3": 56, "R4": 14, "R5": 56, "R6": 14, "R7": 4, "R12": 56}: raise ValueError(counts)
if m["max_workers"] != 17 or m["reserve_gib"] != 30: raise ValueError("wrong global resource policy")
if any(j.get("akita_rtm_required") is not True for j in jobs): raise ValueError("AkitaRTM contract missing")
if any(any(a in {"-disable-servers", "--disable-servers"} for a in j["command"]) for j in jobs): raise ValueError("AkitaRTM disabled")
print(json.dumps({"passed": True, "jobs": len(jobs), "scope_counts": counts, "max_workers": 17, "reserve_gib": 30}, indent=2, sort_keys=True))
'''


README = """# Combined R3-R7 and R12 Queue

This directory provides one global supervisor for both formal packages:

- R3-R7: 144 jobs.
- R12-Simple: 56 jobs.
- Total: 200 unique jobs.

Jobs from the two packages are interleaved so both campaigns make progress.
There is one global limit of 17 live simulations and one global 30-GiB
`MemAvailable` reserve. Do not also run either component package's `run.sh` on
the same machine while this combined supervisor is active.

The combined package references the binaries and source archives in the two
sibling directories; it does not modify or duplicate them.

```bash
cd /path/to/PASTA-rebuttal/rebuttal/r3-r7-r12-full14-20261007
python3 verify_package.py
./run.sh
```

Status:

```bash
python3 remote_campaign_runner.py status
```

Stop scheduling with `Ctrl-C`. Existing child simulations remain alive and
are rediscovered when `./run.sh` is started again.
"""


def main():
    r3 = load(R3)
    r12 = load(R12)
    if OUTPUT.exists():
        raise RuntimeError(f"Refusing to overwrite {OUTPUT}")
    OUTPUT.mkdir()
    shutil.copy2(R3 / "remote_campaign_runner.py", OUTPUT / "remote_campaign_runner.py")
    (OUTPUT / "remote_campaign_runner.py").chmod(0o755)
    (OUTPUT / "verify_package.py").write_text(VERIFY)
    (OUTPUT / "verify_package.py").chmod(0o755)
    (OUTPUT / "README.md").write_text(README)
    (OUTPUT / "run.sh").write_text('#!/usr/bin/env bash\nset -euo pipefail\ncd "$(dirname "$0")"\nexec python3 remote_campaign_runner.py run\n')
    (OUTPUT / "run.sh").chmod(0o755)

    r3_jobs = [prefix_job(job, R3.name) for job in r3["jobs"]]
    r12_jobs = [prefix_job(job, R12.name) for job in r12["jobs"]]
    jobs = interleave(r3_jobs, r12_jobs)
    artifacts = {"binaries": {}, "sources": {}}
    for package, manifest in ((R3, r3), (R12, r12)):
        for group in artifacts:
            for relative, expected in manifest[group].items():
                artifacts[group][f"../{package.name}/{relative}"] = expected
    manifest = {
        "schema": 1,
        "job_count": len(jobs),
        "max_workers": 17,
        "reserve_gib": 30,
        "launch_interval_seconds": 15,
        "poll_seconds": 10,
        "binaries": artifacts["binaries"],
        "sources": artifacts["sources"],
        "reuse": {
            "R12_controls": "R3 Baseline/PASTA/Neighbor/LATPC jobs in this same queue",
            "R4_R5_R6_controls": r3["reuse"],
        },
        "r7_scope": r3["r7_scope"],
        "component_packages": [R3.name, R12.name],
        "jobs": jobs,
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(OUTPUT), "jobs": len(jobs)}, indent=2))


if __name__ == "__main__":
    main()
