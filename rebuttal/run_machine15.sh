#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec python3 -u run_remote.py --machine machine15 "$@"
