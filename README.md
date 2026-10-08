# PTW Sweep From The Pre-Rebuttal Source

This package is copied directly from the local
`PASTA-hyperscan-current-20260619` working tree, including its existing local
changes. It does not use the MI copy or the rebuttal candidate source.
`snapshot-provenance.json` records the original commit, working-tree status
and original file hashes. The original directory is untouched.

Only the attached document's experiment configurations are adopted, as the
user clarified. No barrier, CU, scheduler, coalescer or IOTLB correctness fixes
are imported. No new simulation tracing, workgroup reporting or print-only
mode is added. The only production changes parameterize walker counts,
IOMMU PW queue capacity and the PLT extra latency.

## Configurations

| Profile | GMMU walkers/GPM | Shared IOMMU walkers | IOMMU PW queue | PASTA PLT extra cycles | PASTA PTCL set lookup cycles |
| --- | ---: | ---: | ---: | ---: | ---: |
| 8/32 | 8 | 32 | 64 (default) | 32 | 96 |
| 16/64 | 16 | 64 | 64 (default) | 8 | 72 |
| 64/256 | 64 | 256 | 1024 | 4 | 68 |

The PLT extra term applies only to the existing PTCL set lookup path:
`2 * PTE lookup cycles + PLT extra cycles`. Baseline has extra=0 and does not
enable this Flex/PTCL lookup path. PTE lookup latency remains 32, lookup
slots remain 8. Each profile uses identical B/P walker and queue capacities.
Across profiles, queue capacity and PASTA PLT delay also change, so this is
not a pure walker-count-only sensitivity experiment.

84 jobs: FULL14 x three profiles x Baseline/PASTA. FULL14 names and all
workload arguments are preserved in `historical_commands.json`, extracted
directly from the old `akkalat/results/ablation_study` logs. Historical
performance results are not imported. MT is first, MM second, then the other
12 benchmarks. Each completed point is skipped on resume, and existing raw
logs are never overwritten automatically.

Unchanged: 48 GPM x 32 CU, original Cache/HBM/TLB organization and MSHRs;
L2 cache 4 MiB/GPM, L2 TLB 16x16; max-WG 76800; original benchmark inputs;
GMMU request width 128; PTE lookup 32 cycles and 8 slots; mesh bandwidth=48
and link latency=32; original B/P mechanism flags. AkitaRTM is enabled.
Neither B/P gains `-ptw-demand-pte-only`. Baseline keeps its original
`-mmutlb-demand-pte-only` flag. The old MT input is still 11520x11520.

No strict MT coordinate validation is added because the user requested only
config changes. The original source does not export the attachment's added
Driver workgroup counters or coordinate CSV. A successful exit with positive
Driver time is not proof of complete tile coverage or numerical correctness.
The old MT bugs may still occur; no claim of fixing them is made.

## Transfer And Run

The package is self-contained; the remote machine needs Linux, Go 1.22.4
(or automatic toolchain download), GCC, Python 3, tmux, and access to cached
or downloadable Go dependencies. Transfer the tar archive, then:

```bash
tar -xzf PASTA-ptw-sweep-pre-rebuttal-20261008.tar.gz
cd PASTA-ptw-sweep-pre-rebuttal-20261008
bash build.sh
python3 remote_campaign_runner.py list
tmux new-session -s ptw-sweep 'python3 remote_campaign_runner.py run --groups PTW --workers 16'
```

The archive omits generated binaries and manifests so `build.sh` builds and
prepares a fresh package on the destination. It launches no small benchmark
validation. All 84 commands/configurations are printed before formal launches
and saved in `configs-before-launch.json`. Scheduler default is 16 concurrent
jobs; `--workers 17` explicitly selects 17. New launches are spaced by at least
20 seconds and require MemAvailable >=30 GiB, checked every 10 seconds. The
reserve is an admission check, not a guarantee against later memory growth.
Use a machine dedicated to this campaign; this cap counts only this package.

```bash
python3 remote_campaign_runner.py status
python3 remote_campaign_runner.py summary
tmux attach-session -t ptw-sweep
```

Ctrl-b then d detaches tmux. Ctrl-C/SIGTERM stops admission and leaves durable
worker processes and simulations running. Restarting the supervisor adopts
live tasks and skips completed tasks. Stale/failed jobs are not automatically
rerun or accepted based on leftover metrics. Preserve them and inspect their
logs before deciding on another experiment directory.

Results: `results/<job-id>/state.json`, `stdout.log`, `metrics.csv`.
`summary` writes completed pairs to `results/speedups.csv`:
`speedup = Baseline Driver.total_time / PASTA Driver.total_time`, and
`performance improvement (%) = (speedup - 1) * 100`.
No partial or failed jobs are filled with synthetic values.

Source and binary hashes freeze this package against accidental edits;
different binary hashes alone are not grounds to rerun equivalent experiments.
No formal jobs in this new package have been launched locally.
