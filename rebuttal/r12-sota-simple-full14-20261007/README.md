# R12 Simple SOTA FULL14

This package runs 56 new points: FULL14 x MPW-Simple, SoftWalker-Simple,
TransFW-Simple and PWS-Simple. It reuses the existing R3 FULL14
Baseline/PASTA/Neighbor/LATPC results as controls, so those 56 points are not
repeated here.

Every command is the exact benchmark-specific pre-rebuttal Baseline command
plus one `-sota-simple=<mode>` flag and its output path. No demand-PTE flag or
other simulator parameter is added or removed. Existing `-report-all` metrics
provide the same runtime, GMMU/IOMMU transaction, TLB, cache and DRAM traffic
fields as R3. The binary additionally reports mode-specific walker counters.

These are coarse mechanism models, not faithful paper reproductions. Plot and
paper labels must retain the `-Simple` suffix. See `MODEL_LIMITS.md`.

Run in tmux:

```bash
cd /path/to/r12-sota-simple-full14-20261007
python3 verify_package.py
./run.sh
```

Check progress:

```bash
python3 remote_campaign_runner.py status
```

The supervisor keeps at most 16 jobs active and launches only while at least
30 GiB of `MemAvailable` remains. AkitaRTM is required for every run.
