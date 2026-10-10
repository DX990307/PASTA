# PLT latency: remote last50 tasks

Branch: `plt-latency-20261009`, repository: `DX990307/PASTA`.

PLT fixed32 rows per GMMU (16 sets x2 rows). Only latency varies:0/16/32/128 cycles, specified by `-gmmu-plt-extra-latency`. MT4096x4096, idle-IOMMU assist disabled, IOTLB set-as-line disabled. Simulator source is identical to the current PLT row campaign.

Tasks21-70: Fast Walsh Transform, FFT, FIR, Floyd–Warshall, im2col, K-means, PageRank, ReLU, Simple Convolution, SPMV. Each benchmark has1 Baseline and4 PASTA latency points,50 tasks total. Exact IDs: `job-partitions.json`. Original machine runs first20 only; no result files or generated binaries are uploaded.

Requirements: Go1.22.4 or compatible, GCC, Python3, tmux. On a separate machine:

```bash
git clone --single-branch --branch plt-latency-20261009 https://github.com/DX990307/PASTA.git PASTA-plt-latency
cd PASTA-plt-latency
bash build.sh
mkdir -p results
tmux new-session -d -s plt-latency-last50 'python3 remote_campaign_runner.py run --groups PTW --partition last50 --workers 17 > results/supervisor.log 2>&1'
python3 remote_campaign_runner.py status --partition last50
python3 remote_campaign_runner.py summary --partition last50
```

`--workers` can be reduced for available memory. Launches spaced20 seconds, MemAvailable must be at least30GiB. Fresh clone builds its own manifest; the last50 partition is selected by stable job order. Do not run `after_previous_sweeps.py` on a standalone machine. Omitting `--partition` on a fresh clone selects all70, so use `--partition last50` for this handoff.

The local-only `local-partition.json` is ignored by Git and defaults original machine operations to first20. Metrics: `results/<job-id>/metrics.csv`; paired summary:`results/speedups.csv`.
