#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CPU="${CPU:-0}"
TRIALS="${TRIALS:-10000}"
PERF_REPS="${PERF_REPS:-200}"
MEASURE_PAGES="${MEASURE_PAGES:-65536}"
EVICT_PAGES="${EVICT_PAGES:-4096}"
RESULT_DIR="${RESULT_DIR:-$SCRIPT_DIR/results/$(date +%Y-%m-%d-%H-%M-%S)-pte-cacheline}"

MMU_EVENTS="${MMU_EVENTS:-l1_dtlb_misses,l2_dtlb_misses,ls_l1_d_tlb_miss.tlb_reload_4k_l2_hit,ls_l1_d_tlb_miss.tlb_reload_4k_l2_miss,ls_tablewalker.dc_type0,ls_tablewalker.dc_type1,ls_tablewalker.dside}"
CACHE_EVENTS="${CACHE_EVENTS:-cycles,instructions,cache-references,cache-misses}"

find_perf() {
	if [[ -n "${PERF:-}" ]]; then
		printf '%s\n' "$PERF"
		return
	fi
	if command -v perf >/dev/null 2>&1; then
		command -v perf
		return
	fi
	local kernel
	kernel="$(uname -r)"
	if [[ -x "/usr/lib/linux-tools/$kernel/perf" ]]; then
		printf '/usr/lib/linux-tools/%s/perf\n' "$kernel"
		return
	fi
	return 1
}

run_perf_mode() {
	local events="$1"
	local mode="$2"
	local out="$3"
	"$PERF_BIN" stat -x, -D 200 -o "$out" -e "$events" -- \
		taskset -c "$CPU" "$SCRIPT_DIR/ptcl_probe" \
			--mode "$mode" \
			--perf-workload \
			--perf-reps "$PERF_REPS" \
			--measure-pages "$MEASURE_PAGES" \
			--start-delay-ms 400 \
			--quiet
}

mkdir -p "$RESULT_DIR"
make -C "$SCRIPT_DIR" >/dev/null

PERF_BIN="$(find_perf || true)"
if [[ -z "$PERF_BIN" ]]; then
	echo "perf not found; running timing-only. Use ./setup_perf.sh to enable perf." >&2
else
	echo "perf binary: $PERF_BIN"
fi

echo "Running timing d=1..16. The PTE-cacheline boundary is d=8."
taskset -c "$CPU" "$SCRIPT_DIR/ptcl_probe" \
	--mode sweep \
	--trials "$TRIALS" \
	--max-d 16 \
	--measure-pages "$MEASURE_PAGES" \
	--evict-pages "$EVICT_PAGES" \
	--quiet > "$RESULT_DIR/timing_d1_d16.csv"

echo "Running explicit 8-VPN block timing modes."
for mode in vpn8_same vpn8_stride8 vpn8_random; do
	taskset -c "$CPU" "$SCRIPT_DIR/ptcl_probe" \
		--mode "$mode" \
		--trials "$TRIALS" \
		--measure-pages "$MEASURE_PAGES" \
		--evict-pages "$EVICT_PAGES" \
		--quiet > "$RESULT_DIR/timing_${mode}.csv"
done

if [[ -n "$PERF_BIN" ]]; then
	echo "Running MMU/page-walk perf events."
	for mode in same_ptcl_d1 same_ptcl_d7 next_ptcl_d8 vpn8_same vpn8_stride8 vpn8_random; do
		run_perf_mode "$MMU_EVENTS" "$mode" "$RESULT_DIR/perf_mmu_${mode}.csv"
	done

	echo "Running cache perf events separately to avoid multiplexing MMU counters."
	for mode in same_ptcl_d1 same_ptcl_d7 next_ptcl_d8 vpn8_same vpn8_stride8 vpn8_random; do
		run_perf_mode "$CACHE_EVENTS" "$mode" "$RESULT_DIR/perf_cache_${mode}.csv"
	done
fi

echo "Results: $RESULT_DIR"
echo
echo "Timing summary:"
awk -F, 'NR==1 {next} {printf "d=%-2s B1_after_A=%-6s hit=%-6s cold=%-6s\n", $2, $5, $6, $7}' \
	"$RESULT_DIR/timing_d1_d16.csv"

if [[ -n "$PERF_BIN" ]]; then
	echo
	echo "MMU counter summary:"
	awk -F, '
	BEGIN {
		printf "%-16s %12s %12s %12s %12s %12s %12s\n",
			"mode", "l1_miss", "l2_miss", "l2_hit_4k",
			"l2_miss_4k", "tw_dc0", "tw_dc1"
	}
	FNR==1 {
		mode=FILENAME
		sub(/^.*perf_mmu_/,"",mode)
		sub(/\.csv$/,"",mode)
	}
	$3=="l1_dtlb_misses"{l1=$1}
	$3=="l2_dtlb_misses"{l2=$1}
	$3=="ls_l1_d_tlb_miss.tlb_reload_4k_l2_hit"{h=$1}
	$3=="ls_l1_d_tlb_miss.tlb_reload_4k_l2_miss"{m=$1}
	$3=="ls_tablewalker.dc_type0"{dc0=$1}
	$3=="ls_tablewalker.dc_type1"{dc1=$1}
	ENDFILE {
		printf "%-16s %12s %12s %12s %12s %12s %12s\n",
			mode, l1, l2, h, m, dc0, dc1
		l1=l2=h=m=dc0=dc1=""
	}' "$RESULT_DIR"/perf_mmu_*.csv
fi
