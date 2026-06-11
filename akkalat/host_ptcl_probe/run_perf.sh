#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CPU="${CPU:-0}"
PERF_REPS="${PERF_REPS:-200}"
MEASURE_PAGES="${MEASURE_PAGES:-65536}"
PERF_DELAY_MS="${PERF_DELAY_MS:-200}"
START_DELAY_MS="${START_DELAY_MS:-400}"
RESULT_DIR="${RESULT_DIR:-$SCRIPT_DIR/results/$(date +%Y-%m-%d-%H-%M-%S)}"
EVENTS="${EVENTS:-cycles,instructions,dTLB-loads,dTLB-load-misses,cache-references,cache-misses}"
MODES=(same_ptcl_d1 same_ptcl_d7 next_ptcl_d8 far_random hit_only vpn8_same vpn8_stride8 vpn8_random)

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

PERF_BIN="$(find_perf || true)"
if [[ -z "$PERF_BIN" ]]; then
	cat >&2 <<'EOF'
perf was not found. Install the kernel-matched tools first, for example:

  sudo apt update
  sudo apt install -y linux-tools-common linux-tools-$(uname -r)
  sudo sysctl kernel.perf_event_paranoid=1

Then rerun this script.
EOF
	exit 1
fi

mkdir -p "$RESULT_DIR"
make -C "$SCRIPT_DIR" >/dev/null

if [[ -r /proc/sys/kernel/perf_event_paranoid ]]; then
	paranoid="$(cat /proc/sys/kernel/perf_event_paranoid)"
	if [[ "$paranoid" -gt 1 ]]; then
		echo "warning: perf_event_paranoid=$paranoid may block useful counters" >&2
		echo "         try: sudo sysctl kernel.perf_event_paranoid=1" >&2
	fi
fi

echo "perf binary: $PERF_BIN"
echo "events: $EVENTS"
echo "result dir: $RESULT_DIR"

for mode in "${MODES[@]}"; do
	out="$RESULT_DIR/perf_${mode}.csv"
	echo "running $mode -> $out"
	"$PERF_BIN" stat -x, -D "$PERF_DELAY_MS" -o "$out" -e "$EVENTS" -- \
		taskset -c "$CPU" "$SCRIPT_DIR/ptcl_probe" \
			--mode "$mode" \
			--perf-workload \
			--perf-reps "$PERF_REPS" \
			--measure-pages "$MEASURE_PAGES" \
			--start-delay-ms "$START_DELAY_MS" \
			--quiet
done

echo "perf CSV files are in $RESULT_DIR"
