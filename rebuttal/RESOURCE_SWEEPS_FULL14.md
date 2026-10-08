# Resource sweeps FULL14

Isolated simulator source; the ongoing local campaign is not modified.
Commands are taken from the historical Baseline/PASTA FULL14 command manifest.
AkitaRTM stays enabled. No ptw-demand-pte-only flag is added.

PTW: (8,32), (16,64), (64,256), both designs, 84 jobs.
The ideal profile increases the IOMMU pending walk queue from 64 to 1024.
The local GMMU uses admission into its active-walk slots rather than the
same pending-queue structure; its upstream port buffers are unchanged.

GPM: 8/24/80, both designs, 84 jobs, max-wg=76800 throughout.
PTW and memory profiles keep the historical max-wg=76800.
80-GPM jobs launch first. Default configurations are not rerun.

Memory: 30-CU resource ratios, while physical CUs remain 32 per GPM.
L2 sizes round to nearest 16 KiB to preserve 16 banks and 16 ways.
HBM targets use dram-clock-mhz with dram-preserve-timing-ns;
DRAM timing cycles round upward to preserve physical latency.
TLBs remain unchanged by user request: L2 TLB 16 sets x 16 ways;
L1 TLB and shared IOTLB keep the historical defaults.

On a Linux x86-64 machine:

```bash
tar -xzf resource-sweeps-full14-20261007.tar.gz
cd resource-sweeps-full14-20261007
# The archive includes bin/simulator. Rebuild only when needed:
# bash build.sh
python3 prepare.py --groups GPM PTW MEM -j 17
tmux new -s resource-sweeps
python3 remote_campaign_runner.py run --groups PTW MEM
```

Detach with Ctrl-b d. Inspect from another terminal:

```bash
python3 remote_campaign_runner.py status
```

The supervisor skips completed jobs and preserves active jobs on shutdown.
It admits jobs only when MemAvailable is at least 30 GiB. Running jobs can
grow beyond that threshold; this is not a guaranteed 30-GiB reservation.
Results include exact commands, stdout with AkitaRTM URL, state timestamps,
and metrics.csv. Binary changes or matrix changes are refused after results exist.

Prepare all three groups before starting:

```bash
python3 prepare.py --groups GPM PTW MEM -j 17
```

This produces 252 jobs. PTW/MEM run together, at most 17 concurrent jobs.
GPM jobs remain queued until explicitly launched separately:

```bash
python3 remote_campaign_runner.py run --groups GPM
```

80-GPM jobs run at most 8 concurrently. While any managed 80-GPM job remains
active, total managed concurrency stays at most 8. For 8/24 GPM, it rises to 17.
Use one supervisor at a time; stop the previous supervisor with Ctrl-C first.
Stopping retains active simulations. Group filtering does not stop previously
launched simulations from other groups. No SOTA LLM command is included.

Do not regenerate a different matrix in a directory
that already contains running or completed jobs.
