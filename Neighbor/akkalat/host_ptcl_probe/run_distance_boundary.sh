#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CPU="${CPU:-0}"
TRIALS="${TRIALS:-2000}"
MEASURE_PAGES="${MEASURE_PAGES:-131072}"
EVICT_PAGES="${EVICT_PAGES:-4096}"
CACHE_EVICT_MB="${CACHE_EVICT_MB:-64}"
D_LIST="${D_LIST:-1,2,3,4,5,6,7,8,9,16,32,64,128,256,511,512,513,1024,2048,4096,8192}"
RESULT_DIR="${RESULT_DIR:-$SCRIPT_DIR/results/$(date +%Y-%m-%d-%H-%M-%S)-distance-boundary}"

mkdir -p "$RESULT_DIR"
make -C "$SCRIPT_DIR" >/dev/null

echo "Result dir: $RESULT_DIR"
echo "D_LIST: $D_LIST"

echo "Running warm-cache distance sweep..."
taskset -c "$CPU" "$SCRIPT_DIR/ptcl_probe" \
	--mode sweep \
	--d-list "$D_LIST" \
	--trials "$TRIALS" \
	--measure-pages "$MEASURE_PAGES" \
	--evict-pages "$EVICT_PAGES" \
	--quiet > "$RESULT_DIR/timing_distance_warm.csv"

echo "Running cold-cache distance sweep with cache eviction..."
taskset -c "$CPU" "$SCRIPT_DIR/ptcl_probe" \
	--mode sweep \
	--d-list "$D_LIST" \
	--trials "$TRIALS" \
	--measure-pages "$MEASURE_PAGES" \
	--evict-pages "$EVICT_PAGES" \
	--cache-evict-mb "$CACHE_EVICT_MB" \
	--quiet > "$RESULT_DIR/timing_distance_cold.csv"

echo
echo "Warm summary:"
awk -F, 'NR==1 {next} {printf "d=%-5s B1=%-6s hit=%-6s cold=%-6s\n", $2, $5, $6, $7}' \
	"$RESULT_DIR/timing_distance_warm.csv"

echo
echo "Cold summary:"
awk -F, 'NR==1 {next} {printf "d=%-5s B1=%-6s hit=%-6s cold=%-6s\n", $2, $5, $6, $7}' \
	"$RESULT_DIR/timing_distance_cold.csv"

echo
echo "CSV files:"
echo "  $RESULT_DIR/timing_distance_warm.csv"
echo "  $RESULT_DIR/timing_distance_cold.csv"
