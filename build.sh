#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$ROOT/manifest.json" ]]; then
    python3 "$ROOT/remote_campaign_runner.py" list >/dev/null
    printf 'Existing experiment package retained; binary was not rebuilt.\n'
    exit 0
fi
export GOCACHE="${GOCACHE:-$ROOT/.gocache}"
mkdir -p "$ROOT/bin"
(
    cd "$ROOT/src/akkalat"
    go build -buildvcs=false -o "$ROOT/bin/simulator" ./400latency
)
python3 "$ROOT/remote_campaign_runner.py" prepare
