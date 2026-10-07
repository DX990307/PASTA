# R3-R7 Remote Formal Runs

This package contains 144 unique executions:

- R3: FULL14 x Baseline/PASTA/Neighbor/LATPC = 56.
- R4: FULL14 x PASTA-no-PLT = 14; R3 PASTA is the reference.
- R5: FULL14 x Baseline/PASTA x (8,16)/(4,32) = 56; the default
  (4,16) points reuse R3 Baseline/PASTA.
- R6: FULL14 x PASTA-no-Assist = 14; Assist-ON reuses R3 PASTA.
- R7: two controlled request-flow scenarios x Assist OFF/ON = 4.

R7 is intentionally not multiplied by FULL14. Its executable has no benchmark
input and models a fixed open-loop translation stream; duplicating each point
under fourteen benchmark labels would repeat the same experiment. A true
FULL14 R7 requires a separately specified workload-trace capture/replay model.

Every GPU command is reconstructed from that benchmark's pre-rebuttal stdout.
Only the named sensitivity flag, binary path and output path change. In
particular, this package neither adds nor removes `-ptw-demand-pte-only` or
`-mmutlb-demand-pte-only`. All jobs start AkitaRTM, use a random free port,
and must print the monitor URL before they can qualify as completed.

Run inside tmux:

```bash
tmux new -s r3r7
cd /path/to/r3-r7-full14-20261007
python3 verify_package.py
./run.sh
```

Detach with `Ctrl-b d`. Check progress from another shell:

```bash
cd /path/to/r3-r7-full14-20261007
python3 remote_campaign_runner.py status
```

The supervisor keeps at most 16 simulations live and launches another only
when at least 30 GiB of host `MemAvailable` remains. It detects stale state on
restart and resumes unfinished jobs without rerunning completed points.
