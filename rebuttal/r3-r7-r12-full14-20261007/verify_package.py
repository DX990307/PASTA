#!/usr/bin/env python3
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
