#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# These are new remote-only jobs, not transfers of active local experiments.
exec python3 run_remote.py --machine r2-r3 --ownership-transferred "$@"
