#!/usr/bin/env bash
set -euo pipefail

kernel="$(uname -r)"

sudo apt update
sudo apt install -y linux-tools-common "linux-tools-$kernel"
sudo sysctl kernel.perf_event_paranoid=1

if command -v perf >/dev/null 2>&1; then
	perf --version
else
	"/usr/lib/linux-tools/$kernel/perf" --version
fi
