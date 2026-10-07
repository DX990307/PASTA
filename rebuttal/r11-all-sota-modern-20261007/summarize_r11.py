#!/usr/bin/env python3
import csv, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
m = json.loads((ROOT / "manifest.json").read_text())

def driver_total(path):
    with path.open(newline="") as f:
        for row in csv.reader(f, skipinitialspace=True):
            if len(row) >= 4 and row[1].strip() == "Driver" and row[2].strip() == "total_time":
                return float(row[3])
    raise ValueError(f"Driver.total_time missing: {path}")

expanded, unique = [], []
for job in m["jobs"]:
    state_path = RESULTS / job["id"] / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if state.get("status") != "completed": raise RuntimeError(f"R11 incomplete: {job['id']}")
    value = driver_total(RESULTS / job["id"] / "metrics.csv")
    unique.append({"variant": job["variant"], "job_id": job["id"], "command_signature": job["command_signature"],
                   "logical_occurrences": len(job["logical_occurrences"]), "driver_total_time": value})
    for occurrence in job["logical_occurrences"]:
        expanded.append({"model": occurrence["model"], "profile": occurrence["profile"],
                         "variant": job["variant"], "op_index": occurrence["op_index"],
                         "op_name": occurrence["op_name"], "driver_total_time": value,
                         "representative_job": job["id"]})

def write(path, rows):
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)

write(ROOT / "r11_unique_results.csv", unique)
write(ROOT / "r11_per_op_results.csv", sorted(expanded, key=lambda r: (r["model"], r["variant"], r["op_index"])))
groups = {}
for row in expanded:
    key = (row["model"], row["profile"], row["variant"])
    group = groups.setdefault(key, [0, 0.0]); group[0] += 1; group[1] += row["driver_total_time"]
summary = [{"model": k[0], "profile": k[1], "variant": k[2], "logical_ops": v[0], "driver_total_time_sum": f"{v[1]:.12f}"}
           for k, v in sorted(groups.items())]
with (ROOT / "historical_reference/llm_decomposed_summary.csv").open(newline="") as f:
    for row in csv.DictReader(f):
        summary.append({"model": row["model"], "profile": row["profile"], "variant": row["config"],
                        "logical_ops": row["ops"], "driver_total_time_sum": row["driver_total_time_sum"]})
write(ROOT / "r11_summary.csv", sorted(summary, key=lambda r: (r["model"], r["variant"])))
baseline = {r["model"]: float(r["driver_total_time_sum"]) for r in summary if r["variant"] == "baseline"}
speedups = [{"model": r["model"], "variant": r["variant"], "speedup_vs_baseline": f"{baseline[r['model']] / float(r['driver_total_time_sum']):.9f}"}
            for r in summary if r["variant"] != "baseline"]
write(ROOT / "r11_speedup.csv", sorted(speedups, key=lambda r: (r["model"], r["variant"])))
print(json.dumps({"passed": True, "new_unique_runs": len(unique), "expanded_new_points": len(expanded),
                  "summary": str(ROOT / "r11_summary.csv"), "speedup": str(ROOT / "r11_speedup.csv")}, indent=2))
