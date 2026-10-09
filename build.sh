#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ -f manifest.json ]]; then
    python3 remote_campaign_runner.py list >/dev/null
    exit 0
fi
mkdir -p bin
(cd akkalat && go build -buildvcs=false -o ../bin/simulator ./400latency)
python3 remote_campaign_runner.py prepare
