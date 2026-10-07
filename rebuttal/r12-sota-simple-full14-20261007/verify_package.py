#!/usr/bin/env python3
import hashlib, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FULL14 = {"aes", "bitonicsort", "fastwalshtransform", "fft", "fir", "floydwarshall", "im2col", "kmeans", "matrixmultiplication-ptw", "matrixtranspose", "pagerank", "relu", "simpleconvolution", "spmv"}
MODES = {"mpw_simple", "softwalker_simple", "transfw_simple", "pws_simple"}

def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""): h.update(chunk)
    return h.hexdigest()

m = json.loads((ROOT / "manifest.json").read_text())
for group in ("binaries", "sources"):
    for relative, expected in m[group].items():
        path = ROOT / relative
        if not path.is_file() or digest(path) != expected: raise ValueError(f"changed artifact: {relative}")
jobs = m["jobs"]
if len(jobs) != 56 or len({j["id"] for j in jobs}) != 56: raise ValueError("wrong job count")
if {j["benchmark"] for j in jobs} != FULL14 or {j["variant"] for j in jobs} != MODES: raise ValueError("incomplete matrix")
for j in jobs:
    target = [a for a in j["command"][1:] if not a.startswith("-metric-file-name=") and not a.startswith("-sota-simple=")]
    if target != j["historical_arguments"]: raise ValueError(f"historical drift: {j['id']}")
    if sum(a.startswith("-sota-simple=") for a in j["command"]) != 1: raise ValueError(f"bad mode: {j['id']}")
    if "-max-wg=76800" not in j["command"] or "-report-all" not in j["command"]: raise ValueError(f"bad run contract: {j['id']}")
    if any(a in {"-disable-servers", "--disable-servers"} for a in j["command"]): raise ValueError(f"AkitaRTM disabled: {j['id']}")
print(json.dumps({"passed": True, "jobs": len(jobs), "full14": len(FULL14), "modes": sorted(MODES)}, indent=2))
