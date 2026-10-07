# R2/R3 Remote Package

This package contains new remote-only FULL14 jobs. It does not stop, replace,
or modify experiments running on the original machine. All jobs enable
AkitaRTM, use 48 GPMs and max-wg=76800, and retain the frozen full-size inputs
and corrected PageRank launch planner. The source snapshot derives from v12;
unrelated baseline timing, allocation and network models are unchanged.

## Run

Linux x86_64 and Python 3 standard library are required. No Go build is needed.

```bash
git clone --branch rebuttal-multi-machine-plan git@github.com:DX990307/PASTA.git PASTA-rebuttal
cd PASTA-rebuttal/rebuttal
bash run_r2_r3.sh --check
nohup bash run_r2_r3.sh --workers 15 > r2-r3.log 2>&1 &
```

Use `--workers 5` on the smaller machine. Run this package on ONE assigned
machine, not on both machines concurrently. Parallelism is an upper bound;
new jobs start at least 60 seconds apart and only when MemAvailable is at
least 30 GiB plus the candidate's 40 GiB peak estimate. No growth reservation
is added for existing jobs, so later memory growth may exceed this estimate.
No experiment is automatically killed. Restarting the same command adopts
live processes using their PID/start-time identity and a scheduler lock.
Failed or unverified tasks are retained, never silently retried or overwritten.

## Matrix

84 unique runs: six configurations times all 14 benchmarks.

| Configuration | R2 | R3 | Local L2 MSHRs | Shared IOTLB MSHRs |
| --- | --- | --- | ---: | ---: |
| Baseline16 | main | shared control | 16 | 64 |
| Baseline estimated20 | estimated-budget control | no | 20 | 64 |
| PASTA16 | main | shared comparison | 16 | 64 |
| M1 demand-only | auxiliary | no | 16 | 64 |
| Neighbor abstract | no | comparison | 16 | 64 |
| LATPC simple | no | comparison | 16 | 64 |

R2 therefore has 56 logical points, R3 has 56 logical points, and the 28
Baseline16/PASTA16 points are shared rather than executed twice.
**B20 is the user-approved provisional local-state estimate, not measured
complete-system equal area.** Measured N_eq remains unknown. M1 is a nested
demand-only substrate control, not an independent full-PASTA removal.
**HDPAT is not implemented in this snapshot; full five-way R3 remains incomplete.**

## Simplified Models

LATPC follows the paper's lane-ordered unique-VPN stride detector, BaseVPN,
modulo-512 stride, and 32-bit mask. Native 64-lane waves are divided at the
32-unique-VPN limit; VPNs are not sorted. The detector resets per memory
instruction. L1 vector TLB MSHRs compress matching demands; the baseline's
four nominal L1 entries are retained. Each distinct missing VPN still sends
an ordinary L2 request; L2 and IOTLB retain separate per-VPN MSHR admission.
Same-bit hit-under-miss requests share the corresponding response.

LATPC uses two aggregate cycles per L1 request for detector/tagging; this is
not a fully pipelined implementation of the paper's throughput. Local GMMU
and shared IOMMU walkers batch concurrent matching BaseVPN/Stride requests
within one 512-page region, at most 32 VPNs. They retain the leader's native
walk countdown and add one optimistic cycle per additional batched leaf.
**This extra-leaf cost is an assumption, not a measured DRAM row-hit latency.**
There is no new detailed DRAM, physical per-level walker, or row-buffer model.
Only demanded translations are returned, with each request's own ID and
normal response bandwidth/backpressure. No PASTA flags, PLT, idle assist,
front-end PTCL MSHR compression, speculative future fills, or shortcuts are
enabled for Neighbor/LATPC. Scalar/instruction L1 paths remain baseline.

Neighbor runs only in the shared IOMMU walk-request queue. It uses PID and
level-specific eight-PTE neighborhoods in a five-level abstraction. Upper
prefix credits are granted at the native aggregate upper-path boundary,
reducing remaining upper countdowns. Leaf completion wakes only concurrent
pending requests in the same leaf neighborhood; all eight entries of that
leaf line are free, but replies still consume normal response resources.
Coalescing-aware scheduling includes a 100000-cycle starvation guard.
Local GMMU and front-end MSHR admission remain baseline for Neighbor.

These are **simplified simulator comparisons, not cycle-accurate reproductions
of the published SOTA implementations**. Do not present their timer counters
as physical page-table memory traffic or use them for hardware area claims.
LATPC stage-service accounting is approximate when requests join a batch late;
driver time is the primary comparison metric.

Paper: Shin et al., MICRO 2018; LATPC, MICRO 2025,
DOI 10.1145/3725843.3756069. The user-supplied PDF was read locally and is not
redistributed. Its fingerprint is recorded in source provenance.

## Results and Evidence

Progress: `results/r2-r3/status.json`.
Excel-readable rolling summary: `results/r2-r3/summary.csv`.
Each job retains command, launch, state, completion, stdout, metrics, request
traces and workgroup identity evidence. Driver simulated seconds and host
wall seconds are separate columns. Exit zero is **not** strict scientific
qualification: join workgroup populations and validate configuration/counters
before reporting speedup. Transfer the entire results directory for that audit.

Binary/source fingerprints, changed-file provenance and CPU-only normal/race
tests are under `provenance/r23-simple-v13.json`; the rebuildable source snapshot
is `sources/source-r23-simple-v13.tar.gz`. No formal GPU pilot was run locally.
Inherited MMU/old TLB mock-fixture gaps are recorded; focused production-file
tests cover the new paths without modifying those unrelated fixtures.

Configuration identity is determined by hardware/model/input settings, not
binary filename alone. Different binaries may represent the same experiment
when all relevant configuration and modeled semantics match; recompilation
by itself is not a reason to rerun or a license to reuse incompatible results.
