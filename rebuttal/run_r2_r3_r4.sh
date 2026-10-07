#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# One shared queue/lock; fresh R3 controls also serve the R4 comparison.
exec python3 run_remote.py --machine r2-r3-r4 --ownership-transferred "$@"
