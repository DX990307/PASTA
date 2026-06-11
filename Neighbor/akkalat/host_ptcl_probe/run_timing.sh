#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CPU="${CPU:-0}"
TRIALS="${TRIALS:-50000}"
MAX_D="${MAX_D:-16}"
MEASURE_PAGES="${MEASURE_PAGES:-65536}"
EVICT_PAGES="${EVICT_PAGES:-16384}"
RESULT_DIR="${RESULT_DIR:-$SCRIPT_DIR/results/$(date +%Y-%m-%d-%H-%M-%S)}"

mkdir -p "$RESULT_DIR"
make -C "$SCRIPT_DIR" >/dev/null

OUT="$RESULT_DIR/timing_sweep.csv"

if command -v taskset >/dev/null 2>&1; then
	taskset -c "$CPU" "$SCRIPT_DIR/ptcl_probe" \
		--mode sweep \
		--trials "$TRIALS" \
		--max-d "$MAX_D" \
		--measure-pages "$MEASURE_PAGES" \
		--evict-pages "$EVICT_PAGES" \
		--quiet > "$OUT"
else
	"$SCRIPT_DIR/ptcl_probe" \
		--mode sweep \
		--trials "$TRIALS" \
		--max-d "$MAX_D" \
		--measure-pages "$MEASURE_PAGES" \
		--evict-pages "$EVICT_PAGES" \
		--cpu "$CPU" \
		--quiet > "$OUT"
fi

echo "timing CSV: $OUT"
column -s, -t "$OUT" 2>/dev/null || cat "$OUT"
