#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec python3 remote_campaign_runner.py run
