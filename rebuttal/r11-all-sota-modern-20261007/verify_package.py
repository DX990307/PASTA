#!/usr/bin/env python3
import hashlib, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXPECTED = {"hdpat_simple", "latpc", "neighbor"}

def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""): h.update(chunk)
    return h.hexdigest()

m = json.loads((ROOT / "manifest.json").read_text())
for group in ("binaries", "sources", "references"):
    for relative, expected in m[group].items():
        path = ROOT / relative
        if not path.is_file() or digest(path) != expected: raise ValueError(f"changed artifact: {relative}")
jobs = m["jobs"]
if len(jobs) != 36 or len({j["id"] for j in jobs}) != 36: raise ValueError("wrong job count")
if {j["variant"] for j in jobs} != EXPECTED: raise ValueError("wrong variants")
logical = {v: {"bert": 0, "gpt": 0, "resnet": 0} for v in EXPECTED}
for j in jobs:
    command = j["command"]
    policy = [a for a in command if a.startswith(("-hdpat-simple", "-latpc-simple", "-neighbor-abstract"))]
    if len(policy) != 1 or policy != j["target_flag_changes"]: raise ValueError(f"bad policy: {j['id']}")
    target = [a for a in command[1:] if not a.startswith("-metric-file-name=") and a not in policy]
    if target != j["historical_arguments"]: raise ValueError(f"historical drift: {j['id']}")
    if "-ptw-demand-pte-only" in command: raise ValueError(f"invented PTW flag: {j['id']}")
    if any(a in {"-disable-servers", "--disable-servers"} for a in command): raise ValueError(f"AkitaRTM disabled: {j['id']}")
    for occurrence in j["logical_occurrences"]: logical[j["variant"]][occurrence["model"]] += 1
for counts in logical.values():
    if counts != {"bert": 449, "gpt": 481, "resnet": 1}: raise ValueError(f"wrong weights: {counts}")
print(json.dumps({"passed": True, "jobs": 36, "unique_shapes": 12, "logical_counts": logical}, indent=2, sort_keys=True))
