# R11 Modern Workload SOTA

This package preserves the pre-rebuttal Figure-17 workload commands exactly.
The logical population is GPT-7B (481 operators), BERT-7B (449 operators), and
ResNet-50 (one full-network point). Exact command deduplication leaves 12
unique shapes. This package runs only 36 new points: those 12 shapes times
Neighbor, LATPC, and HDPAT-Simple. Historical Baseline and PASTA are bundled
as references and are not rerun.

The only command change is one named policy flag. In particular, the package
does not add `-ptw-demand-pte-only`; it retains the historical sampling flags,
warmup/granularity, `max-wg=76800`, operator shapes, and ResNet parameters.

`HDPAT-Simple` is not a faithful full HDPAT port. It models the published
1024-entry LRU redirection table and N through N+3 proactive delivery at the
shared IOMMU. It does not model concentric peer probes, cache/filter contention,
access-count selection, placement, or peer-network timing. Keep the `-Simple`
suffix in figures and rebuttal text.

Run inside tmux:

```bash
cd /path/to/r11-all-sota-modern-20261007
python3 verify_package.py
./run.sh
```

Check progress and generate the weighted result after all 36 jobs finish:

```bash
python3 remote_campaign_runner.py status
python3 summarize_r11.py
```

The supervisor uses at most 17 concurrent simulations, keeps 30 GiB
`MemAvailable`, starts AkitaRTM for every job, and resumes without repeating
completed jobs.
