# Combined R3-R7 and R12 Queue

This directory provides one global supervisor for both formal packages:

- R3-R7: 144 jobs.
- R12-Simple: 56 jobs.
- Total: 200 unique jobs.

Jobs from the two packages are interleaved so both campaigns make progress.
There is one global limit of 17 live simulations and one global 30-GiB
`MemAvailable` reserve. Do not also run either component package's `run.sh` on
the same machine while this combined supervisor is active.

The combined package references the binaries and source archives in the two
sibling directories; it does not modify or duplicate them.

```bash
cd /path/to/PASTA-rebuttal/rebuttal/r3-r7-r12-full14-20261007
python3 verify_package.py
./run.sh
```

Status:

```bash
python3 remote_campaign_runner.py status
```

Stop scheduling with `Ctrl-C`. Existing child simulations remain alive and
are rediscovered when `./run.sh` is started again.
