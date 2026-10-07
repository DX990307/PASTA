# R2/R3/R4 Remote Run

Requires Linux x86_64, Python 3.9+ and compatible glibc. The frozen binary,
source archive, plans and scripts are included; no Go build or local results
are needed to run the experiments.

## Commands

```bash
git clone --branch rebuttal-multi-machine-plan --single-branch git@github.com:DX990307/PASTA.git PASTA-rebuttal
cd PASTA-rebuttal/rebuttal
bash run_r2_r3_r4.sh --workers 15 --check
nohup bash run_r2_r3_r4.sh --workers 15 > r2-r3-r4.log 2>&1 &
```

Use `--workers 5` in both commands on the smaller server. Start only one
scheduler. Do not run separate R2/R3 and R4 wrappers simultaneously or run
the same points on two hosts. An existing checkout uses `git pull --ff-only`.
To switch a running scheduler, stop only that scheduler, leave detached
workers running, then start the combined wrapper. Its shared lock, unchanged
R2/R3 plan identity and job directories allow adoption without duplicates.

## Matrix

| Group | Newly Executed Configurations | Runs |
| --- | --- | ---: |
| R2 | Estimated B_Neq (B20) x FULL14 | 14 |
| R3 | Baseline16, PASTA16, Neighbor, LATPC x FULL14 | 56 |
| R4 | PASTA-no-PLT x FULL14 | 14 |
| Total | 6 executable configurations x FULL14 | 84 |

R3 controls run freshly to collect this round's data. R4 references these
same PASTA16 runs, not another new PASTA-Full sweep. Existing compatible
controls remain usable for R2. The preserved R2/R3 plan includes 14 excluded
M1 rows, so the combined check reports 98 rows, 14 exclusions and **84 runs**.
HDPAT is excluded. Estimated B20 is not measured complete-system equal area;
Neighbor/LATPC remain simplified models.

AkitaRTM stays enabled. Original FULL14 inputs, max-wg=76800, 48 GPMs,
16 local MSHRs (20 for B20), 64 shared MSHRs and native walker timing are
unchanged. One queue refills up to the requested parallelism. Admission
requires 30 GiB MemAvailable plus the incoming task's estimated 40-GiB peak;
insufficient memory pauses launches. This is not a guarantee against later
memory growth. Failed/unverified attempts are retained, not silently retried.

## R4 Qualification

Relative to each PASTA16 command, no-PLT changes only
`-gmmu-plt-disabled=false` to `true`, apart from output paths. It removes
local locator allocation/lookup/maintenance while retaining adaptive PTCL
MSHR grouping, representative downstream issue, bitmap replies, PTE-cache
replacement and Assist. Disabling Flex is not a pure PLT ablation.

The inherited model uses 32-cycle sector lookups and eight in-flight lookup
slots. No-PLT checks all ways per sector and retains its MSHR until the sector
work completes, including late arrivals/backpressure. It does **not** model
physical SRAM read/write port arbitration, maintenance contention or energy.
Tag comparisons are logical counts, not physical reads. Results answer PLT
removal under the existing aggregate simulator, not calibrated hardware cost.

`provenance/r4-aggregate.json` records normal/race PLT component tests and
all 14 actual runner configurations. These are CPU-only tests, not small
GPU trials. The published simulator and source archive are unchanged.

## Outputs

All groups share `results/r2-r3/` to preserve existing worker identities:

- `status.json`: live counts and memory availability.
- `summary.csv`: run state, driver time and host wall time.
- `<benchmark>__pasta_no_plt/`: R4 logs, metrics and completion records.
- Other job directories retain their original R2/R3 names.

No experiment results are uploaded to GitHub. Process exit alone is not
scientific qualification; validate complete workgroup populations and counters.
