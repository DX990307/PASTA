#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec python3 run_remote.py --machine r4 --ownership-transferred "$@"
