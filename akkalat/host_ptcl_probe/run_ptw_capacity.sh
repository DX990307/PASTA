#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CPU="${CPU:-0}"
TRIALS="${TRIALS:-1000}"
MEASURE_PAGES="${MEASURE_PAGES:-131072}"
EVICT_PAGES="${EVICT_PAGES:-4096}"
PTW_STRIDE_PAGES="${PTW_STRIDE_PAGES:-512}"
CACHE_EVICT_MB="${CACHE_EVICT_MB:-64}"
K_LIST="${K_LIST:-1,2,4,8,12,16,20,24,32,48,64,96,128}"
RESULT_DIR="${RESULT_DIR:-$SCRIPT_DIR/results/$(date +%Y-%m-%d-%H-%M-%S)-ptw-capacity}"

mkdir -p "$RESULT_DIR"
make -C "$SCRIPT_DIR" >/dev/null

echo "Result dir: $RESULT_DIR"
echo "K_LIST: $K_LIST"
echo "PTW_STRIDE_PAGES: $PTW_STRIDE_PAGES"

echo "Running warm PTW capacity sweep..."
taskset -c "$CPU" "$SCRIPT_DIR/ptcl_probe" \
	--mode ptw_capacity \
	--k-list "$K_LIST" \
	--trials "$TRIALS" \
	--measure-pages "$MEASURE_PAGES" \
	--evict-pages "$EVICT_PAGES" \
	--ptw-stride-pages "$PTW_STRIDE_PAGES" \
	--quiet > "$RESULT_DIR/timing_ptw_capacity_warm.csv"

echo "Running cold PTW capacity sweep with cache eviction..."
taskset -c "$CPU" "$SCRIPT_DIR/ptcl_probe" \
	--mode ptw_capacity \
	--k-list "$K_LIST" \
	--trials "$TRIALS" \
	--measure-pages "$MEASURE_PAGES" \
	--evict-pages "$EVICT_PAGES" \
	--ptw-stride-pages "$PTW_STRIDE_PAGES" \
	--cache-evict-mb "$CACHE_EVICT_MB" \
	--quiet > "$RESULT_DIR/timing_ptw_capacity_cold.csv"

echo
echo "Warm summary:"
awk -F, 'NR==1 {next} {printf "K=%-4s total=%-6s cyc/load=%-6s p95=%-6s\n", $2, $5, $6, $8}' \
	"$RESULT_DIR/timing_ptw_capacity_warm.csv"

echo
echo "Cold summary:"
awk -F, 'NR==1 {next} {printf "K=%-4s total=%-6s cyc/load=%-6s p95=%-6s\n", $2, $5, $6, $8}' \
	"$RESULT_DIR/timing_ptw_capacity_cold.csv"

echo
echo "CSV files:"
echo "  $RESULT_DIR/timing_ptw_capacity_warm.csv"
echo "  $RESULT_DIR/timing_ptw_capacity_cold.csv"
