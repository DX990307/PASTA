package translationtrace

import (
	"fmt"
	"os"
	"sort"
	"sync"
	"sync/atomic"

	"github.com/sarchlab/akita/v3/sim"
)

const defaultWindowCycles = uint64(10000)

type componentSample struct {
	initialized bool
	lastCycle   uint64
	lastValue   float64
	lastFull    bool
}

type windowStat struct {
	start uint64
	end   uint64

	iommuMSHRUtilCycles float64
	iommuMSHRCycles     uint64
	iommuMSHRFullCycles uint64

	gmmuMSHRUtilCycles float64
	gmmuMSHRCycles     uint64
	gmmuMSHRFullCycles uint64

	sharedPTWUtilCycles float64
	sharedPTWCycles     uint64
	sharedPTWFullCycles uint64
	sharedPWQueueCycles uint64
	sharedPWQueueTime   uint64

	localPTWUtilCycles float64
	localPTWCycles     uint64
	localPTWFullCycles uint64

	gmmuPTELookupWaitingCycles  uint64
	gmmuPTELookupWaitingTime    uint64
	gmmuPTELookupInflightCycles uint64
	gmmuPTELookupInflightTime   uint64

	gmmuLocalIssue int
	gmmuIOMMUIssue int
	iommuIncoming  int
	iommuToMMU     int
}

type requestState struct {
	path        string
	children    []string
	activeStage map[string]uint64
	stageCycles map[string]uint64
}

type breakdownKey struct {
	path  string
	stage string
}

type breakdownStat struct {
	count int
	total uint64
}

type accessKey struct {
	component    string
	deviceID     uint64
	pid          uint32
	ptclID       uint64
	log2PageSize uint64
	lineSize     int
}

type accessLineStat struct {
	bitmap     uint8
	accesses   uint64
	firstCycle uint64
	lastCycle  uint64
}

type accessSummaryKey struct {
	scope        string
	component    string
	deviceID     string
	pid          string
	log2PageSize uint64
	lineSize     int
}

type accessSummaryStat struct {
	lines         uint64
	totalAccesses uint64
	totalUsed     uint64
	hist          [9]uint64
}

type Tracer struct {
	sync.Mutex

	enabled      bool
	windowCycles uint64
	filePrefix   string
	ptclLineSize int

	samples map[string]*componentSample
	windows map[uint64]*windowStat

	parent     map[string]string
	requests   map[string]*requestState
	breakdowns map[breakdownKey]*breakdownStat
	accesses   map[accessKey]*accessLineStat
}

var global = &Tracer{}
var enabledFast uint32

func Configure(enabled bool, filePrefix string, windowCycles uint64) {
	global.Lock()
	defer global.Unlock()

	if windowCycles == 0 {
		windowCycles = defaultWindowCycles
	}

	global.enabled = enabled
	global.windowCycles = windowCycles
	global.filePrefix = filePrefix
	global.ptclLineSize = defaultPTCLLineSize()
	global.samples = make(map[string]*componentSample)
	global.windows = make(map[uint64]*windowStat)
	global.parent = make(map[string]string)
	global.requests = make(map[string]*requestState)
	global.breakdowns = make(map[breakdownKey]*breakdownStat)
	global.accesses = make(map[accessKey]*accessLineStat)
	if enabled {
		atomic.StoreUint32(&enabledFast, 1)
	} else {
		atomic.StoreUint32(&enabledFast, 0)
	}
}

func ConfigureAccessPattern(ptclLineSize int) {
	global.Lock()
	defer global.Unlock()

	global.ptclLineSize = normalizePTCLLineSize(ptclLineSize)
}

func Enabled() bool {
	global.Lock()
	defer global.Unlock()
	return global.enabled
}

func cycle(now sim.VTimeInSec) uint64 {
	if now <= 0 {
		return 0
	}
	return uint64(float64(now) * 1e9)
}

func (t *Tracer) window(start uint64) *windowStat {
	windowStart := (start / t.windowCycles) * t.windowCycles
	stat := t.windows[windowStart]
	if stat == nil {
		stat = &windowStat{
			start: windowStart,
			end:   windowStart + t.windowCycles,
		}
		t.windows[windowStart] = stat
	}
	return stat
}

func (t *Tracer) integrate(
	start, end uint64,
	value float64,
	full bool,
	add func(*windowStat, uint64, float64, bool),
) {
	if end <= start {
		return
	}

	for start < end {
		stat := t.window(start)
		next := stat.end
		if next > end {
			next = end
		}
		delta := next - start
		add(stat, delta, value, full)
		start = next
	}
}

func (t *Tracer) observeValue(
	key string,
	now sim.VTimeInSec,
	value float64,
	full bool,
	add func(*windowStat, uint64, float64, bool),
) {
	if !t.enabled {
		return
	}

	nowCycle := cycle(now)
	sample := t.samples[key]
	if sample == nil {
		sample = &componentSample{}
		t.samples[key] = sample
	}

	if sample.initialized {
		t.integrate(sample.lastCycle, nowCycle, sample.lastValue, sample.lastFull, add)
	}

	sample.initialized = true
	sample.lastCycle = nowCycle
	sample.lastValue = value
	sample.lastFull = full
}

func ObserveGMMUTLB(
	now sim.VTimeInSec,
	name string,
	occupied int,
	capacity int,
	full bool,
	pteWaiting int,
	pteInflight int,
) {
	global.Lock()
	defer global.Unlock()

	util := 0.0
	if capacity > 0 {
		util = float64(occupied) / float64(capacity)
	}

	global.observeValue("gmmu-mshr:"+name, now, util, full,
		func(stat *windowStat, delta uint64, value float64, full bool) {
			stat.gmmuMSHRUtilCycles += value * float64(delta)
			stat.gmmuMSHRCycles += delta
			if full {
				stat.gmmuMSHRFullCycles += delta
			}
		})

	global.observeValue("gmmu-pte-wait:"+name, now, float64(pteWaiting), false,
		func(stat *windowStat, delta uint64, value float64, _ bool) {
			stat.gmmuPTELookupWaitingCycles += uint64(value * float64(delta))
			stat.gmmuPTELookupWaitingTime += delta
		})

	global.observeValue("gmmu-pte-inflight:"+name, now, float64(pteInflight), false,
		func(stat *windowStat, delta uint64, value float64, _ bool) {
			stat.gmmuPTELookupInflightCycles += uint64(value * float64(delta))
			stat.gmmuPTELookupInflightTime += delta
		})
}

func ObserveIOMMUTLB(
	now sim.VTimeInSec,
	name string,
	occupied int,
	capacity int,
	full bool,
) {
	global.Lock()
	defer global.Unlock()

	util := 0.0
	if capacity > 0 {
		util = float64(occupied) / float64(capacity)
	}

	global.observeValue("iommu-mshr:"+name, now, util, full,
		func(stat *windowStat, delta uint64, value float64, full bool) {
			stat.iommuMSHRUtilCycles += value * float64(delta)
			stat.iommuMSHRCycles += delta
			if full {
				stat.iommuMSHRFullCycles += delta
			}
		})
}

func ObserveSharedMMU(
	now sim.VTimeInSec,
	name string,
	inflight int,
	capacity int,
	pwQueueLen int,
	full bool,
) {
	global.Lock()
	defer global.Unlock()

	util := 0.0
	if capacity > 0 {
		util = float64(inflight) / float64(capacity)
	}

	global.observeValue("shared-ptw:"+name, now, util, full,
		func(stat *windowStat, delta uint64, value float64, full bool) {
			stat.sharedPTWUtilCycles += value * float64(delta)
			stat.sharedPTWCycles += delta
			if full {
				stat.sharedPTWFullCycles += delta
			}
		})

	global.observeValue("shared-pwqueue:"+name, now, float64(pwQueueLen), false,
		func(stat *windowStat, delta uint64, value float64, _ bool) {
			stat.sharedPWQueueCycles += uint64(value * float64(delta))
			stat.sharedPWQueueTime += delta
		})
}

func ObserveLocalGMMU(
	now sim.VTimeInSec,
	name string,
	inflight int,
	capacity int,
	full bool,
) {
	global.Lock()
	defer global.Unlock()

	util := 0.0
	if capacity > 0 {
		util = float64(inflight) / float64(capacity)
	}

	global.observeValue("local-ptw:"+name, now, util, full,
		func(stat *windowStat, delta uint64, value float64, full bool) {
			stat.localPTWUtilCycles += value * float64(delta)
			stat.localPTWCycles += delta
			if full {
				stat.localPTWFullCycles += delta
			}
		})
}

func event(now sim.VTimeInSec, add func(*windowStat)) {
	global.Lock()
	defer global.Unlock()
	if !global.enabled {
		return
	}

	add(global.window(cycle(now)))
}

func RecordGMMULocalIssue(now sim.VTimeInSec) {
	event(now, func(stat *windowStat) { stat.gmmuLocalIssue++ })
}

func RecordGMMUIOMMUIssue(now sim.VTimeInSec) {
	event(now, func(stat *windowStat) { stat.gmmuIOMMUIssue++ })
}

func RecordIOMMUIncoming(now sim.VTimeInSec) {
	event(now, func(stat *windowStat) { stat.iommuIncoming++ })
}

func RecordIOMMUToMMU(now sim.VTimeInSec) {
	event(now, func(stat *windowStat) { stat.iommuToMMU++ })
}

func RecordPageAccess(
	now sim.VTimeInSec,
	component string,
	pid uint32,
	deviceID uint64,
	pageVAddr uint64,
	log2PageSize uint64,
) {
	if atomic.LoadUint32(&enabledFast) == 0 {
		return
	}

	global.Lock()
	defer global.Unlock()
	if !global.enabled {
		return
	}

	lineSize := normalizePTCLLineSize(global.ptclLineSize)
	if component == "" {
		component = "unknown"
	}

	vpn := pageVAddr >> log2PageSize
	ptclID := vpn / uint64(lineSize)
	bit := uint(vpn % uint64(lineSize))
	key := accessKey{
		component:    component,
		deviceID:     deviceID,
		pid:          pid,
		ptclID:       ptclID,
		log2PageSize: log2PageSize,
		lineSize:     lineSize,
	}

	stat := global.accesses[key]
	if stat == nil {
		nowCycle := cycle(now)
		stat = &accessLineStat{
			firstCycle: nowCycle,
			lastCycle:  nowCycle,
		}
		global.accesses[key] = stat
	}
	stat.bitmap |= uint8(1 << bit)
	stat.accesses++
	stat.lastCycle = cycle(now)
}

func StartRequest(id string) {
	global.Lock()
	defer global.Unlock()
	global.ensureRequestLocked(id)
}

func LinkRequest(childID, parentID string) {
	global.Lock()
	defer global.Unlock()
	if !global.enabled || childID == "" || parentID == "" {
		return
	}

	root := global.rootLocked(parentID)
	global.parent[childID] = root
	req := global.ensureRequestLocked(root)
	req.children = append(req.children, childID)
}

func SetPath(id string, path string) {
	global.Lock()
	defer global.Unlock()
	if !global.enabled || id == "" || path == "" {
		return
	}

	req := global.ensureRequestLocked(global.rootLocked(id))
	if req.path == "" || req.path == "unknown" {
		req.path = path
	}
}

func BeginStage(id string, stage string, now sim.VTimeInSec) {
	global.Lock()
	defer global.Unlock()
	if !global.enabled || id == "" || stage == "" {
		return
	}

	req := global.ensureRequestLocked(global.rootLocked(id))
	if _, active := req.activeStage[stage]; active {
		return
	}
	req.activeStage[stage] = cycle(now)
}

func EndStage(id string, stage string, now sim.VTimeInSec) {
	global.Lock()
	defer global.Unlock()
	if !global.enabled || id == "" || stage == "" {
		return
	}

	req := global.ensureRequestLocked(global.rootLocked(id))
	start, active := req.activeStage[stage]
	if !active {
		return
	}
	delete(req.activeStage, stage)

	nowCycle := cycle(now)
	if nowCycle > start {
		req.stageCycles[stage] += nowCycle - start
	}
}

func AddStageCycles(id string, stage string, cycles uint64) {
	global.Lock()
	defer global.Unlock()
	if !global.enabled || id == "" || stage == "" || cycles == 0 {
		return
	}

	req := global.ensureRequestLocked(global.rootLocked(id))
	req.stageCycles[stage] += cycles
}

func CompleteRequest(id string, now sim.VTimeInSec) {
	global.Lock()
	defer global.Unlock()
	if !global.enabled || id == "" {
		return
	}

	root := global.rootLocked(id)
	req := global.requests[root]
	if req == nil {
		return
	}

	nowCycle := cycle(now)
	for stage, start := range req.activeStage {
		if nowCycle > start {
			req.stageCycles[stage] += nowCycle - start
		}
	}

	path := req.path
	if path == "" {
		path = "unknown"
	}

	for stage, cycles := range req.stageCycles {
		if cycles == 0 {
			continue
		}
		key := breakdownKey{path: path, stage: stage}
		stat := global.breakdowns[key]
		if stat == nil {
			stat = &breakdownStat{}
			global.breakdowns[key] = stat
		}
		stat.count++
		stat.total += cycles
	}

	for _, child := range req.children {
		delete(global.parent, child)
	}
	delete(global.requests, root)
}

func (t *Tracer) ensureRequestLocked(id string) *requestState {
	if id == "" {
		id = "unknown"
	}

	root := t.rootLocked(id)
	req := t.requests[root]
	if req == nil {
		req = &requestState{
			path:        "unknown",
			activeStage: make(map[string]uint64),
			stageCycles: make(map[string]uint64),
		}
		t.requests[root] = req
	}
	return req
}

func (t *Tracer) rootLocked(id string) string {
	if id == "" {
		return "unknown"
	}

	root := id
	seen := make(map[string]struct{})
	for {
		parent, ok := t.parent[root]
		if !ok || parent == "" {
			return root
		}
		if _, loop := seen[parent]; loop {
			return root
		}
		seen[parent] = struct{}{}
		root = parent
	}
}

func Dump() error {
	global.Lock()
	defer global.Unlock()
	if !global.enabled || global.filePrefix == "" {
		return nil
	}

	if err := global.dumpPressureLocked(); err != nil {
		return err
	}
	if err := global.dumpBreakdownLocked(); err != nil {
		return err
	}
	if err := global.dumpPTCLAccessLocked(); err != nil {
		return err
	}
	return global.dumpPTCLAccessSummaryLocked()
}

func (t *Tracer) dumpPressureLocked() error {
	name := t.filePrefix + "_translation_pressure.csv"
	f, err := os.Create(name)
	if err != nil {
		return err
	}
	defer f.Close()

	fmt.Fprintln(f, "cycle_start,cycle_end,iommutlb_mshr_util,gmmutlb_avg_mshr_util,shared_mmu_ptw_util,local_gmmu_avg_ptw_util,iommutlb_mshr_full_cycles,gmmutlb_mshr_full_component_cycles,shared_mmu_ptw_full_cycles,local_gmmu_ptw_full_component_cycles,shared_mmu_pwqueue_avg_len,gmmu_pte_lookup_waiting_avg_len,gmmu_pte_lookup_inflight_avg_len,gmmu_local_issue,gmmu_iommu_issue,iommu_incoming,iommu_to_mmu")

	starts := make([]uint64, 0, len(t.windows))
	for start := range t.windows {
		starts = append(starts, start)
	}
	sort.Slice(starts, func(i, j int) bool { return starts[i] < starts[j] })

	for _, start := range starts {
		stat := t.windows[start]
		fmt.Fprintf(
			f,
			"%d,%d,%.6f,%.6f,%.6f,%.6f,%d,%d,%d,%d,%.6f,%.6f,%.6f,%d,%d,%d,%d\n",
			stat.start,
			stat.end,
			avgFloat(stat.iommuMSHRUtilCycles, stat.iommuMSHRCycles),
			avgFloat(stat.gmmuMSHRUtilCycles, stat.gmmuMSHRCycles),
			avgFloat(stat.sharedPTWUtilCycles, stat.sharedPTWCycles),
			avgFloat(stat.localPTWUtilCycles, stat.localPTWCycles),
			stat.iommuMSHRFullCycles,
			stat.gmmuMSHRFullCycles,
			stat.sharedPTWFullCycles,
			stat.localPTWFullCycles,
			avgUint(stat.sharedPWQueueCycles, stat.sharedPWQueueTime),
			avgUint(stat.gmmuPTELookupWaitingCycles, stat.gmmuPTELookupWaitingTime),
			avgUint(stat.gmmuPTELookupInflightCycles, stat.gmmuPTELookupInflightTime),
			stat.gmmuLocalIssue,
			stat.gmmuIOMMUIssue,
			stat.iommuIncoming,
			stat.iommuToMMU,
		)
	}

	return nil
}

func (t *Tracer) dumpBreakdownLocked() error {
	name := t.filePrefix + "_translation_breakdown.csv"
	f, err := os.Create(name)
	if err != nil {
		return err
	}
	defer f.Close()

	fmt.Fprintln(f, "path,stage,count,total_cycles,avg_cycles")

	keys := make([]breakdownKey, 0, len(t.breakdowns))
	for key := range t.breakdowns {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		if keys[i].path == keys[j].path {
			return keys[i].stage < keys[j].stage
		}
		return keys[i].path < keys[j].path
	})

	for _, key := range keys {
		stat := t.breakdowns[key]
		avg := 0.0
		if stat.count > 0 {
			avg = float64(stat.total) / float64(stat.count)
		}
		fmt.Fprintf(f, "%s,%s,%d,%d,%.6f\n",
			key.path, key.stage, stat.count, stat.total, avg)
	}

	return nil
}

func (t *Tracer) dumpPTCLAccessLocked() error {
	name := t.filePrefix + "_ptcl_access.csv"
	f, err := os.Create(name)
	if err != nil {
		return err
	}
	defer f.Close()

	fmt.Fprintln(f, "component,device_id,pid,ptcl_id,ptcl_base_vaddr,ptcl_line_size,log2_page_size,used_pte_count,used_pte_bitmap,access_count,first_cycle,last_cycle")

	keys := make([]accessKey, 0, len(t.accesses))
	for key := range t.accesses {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		if keys[i].component != keys[j].component {
			return keys[i].component < keys[j].component
		}
		if keys[i].deviceID != keys[j].deviceID {
			return keys[i].deviceID < keys[j].deviceID
		}
		if keys[i].pid != keys[j].pid {
			return keys[i].pid < keys[j].pid
		}
		if keys[i].log2PageSize != keys[j].log2PageSize {
			return keys[i].log2PageSize < keys[j].log2PageSize
		}
		if keys[i].lineSize != keys[j].lineSize {
			return keys[i].lineSize < keys[j].lineSize
		}
		return keys[i].ptclID < keys[j].ptclID
	})

	for _, key := range keys {
		stat := t.accesses[key]
		baseVAddr := (key.ptclID * uint64(key.lineSize)) << key.log2PageSize
		fmt.Fprintf(
			f,
			"%s,%d,%d,%d,0x%x,%d,%d,%d,0x%02x,%d,%d,%d\n",
			key.component,
			key.deviceID,
			key.pid,
			key.ptclID,
			baseVAddr,
			key.lineSize,
			key.log2PageSize,
			countBits8(stat.bitmap),
			stat.bitmap,
			stat.accesses,
			stat.firstCycle,
			stat.lastCycle,
		)
	}

	return nil
}

func (t *Tracer) dumpPTCLAccessSummaryLocked() error {
	name := t.filePrefix + "_ptcl_access_summary.csv"
	f, err := os.Create(name)
	if err != nil {
		return err
	}
	defer f.Close()

	summaries := make(map[accessSummaryKey]*accessSummaryStat)
	for key, line := range t.accesses {
		used := countBits8(line.bitmap)
		if used < 0 {
			used = 0
		}
		if used > 8 {
			used = 8
		}

		addAccessSummary(
			summaries,
			accessSummaryKey{
				scope:        "cu",
				component:    key.component,
				deviceID:     fmt.Sprintf("%d", key.deviceID),
				pid:          fmt.Sprintf("%d", key.pid),
				log2PageSize: key.log2PageSize,
				lineSize:     key.lineSize,
			},
			used,
			line.accesses,
		)
		addAccessSummary(
			summaries,
			accessSummaryKey{
				scope:        "all",
				component:    "ALL",
				deviceID:     "all",
				pid:          "all",
				log2PageSize: key.log2PageSize,
				lineSize:     key.lineSize,
			},
			used,
			line.accesses,
		)
	}

	fmt.Fprintln(f, "scope,component,device_id,pid,ptcl_line_size,log2_page_size,ptcl_line_count,total_accesses,avg_used_pte_per_line,p50_used_pte,p90_used_pte,p99_used_pte,full_line_fraction,single_pte_fraction,used_1_lines,used_2_lines,used_3_lines,used_4_lines,used_5_lines,used_6_lines,used_7_lines,used_8_lines")

	keys := make([]accessSummaryKey, 0, len(summaries))
	for key := range summaries {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		if keys[i].scope != keys[j].scope {
			return keys[i].scope < keys[j].scope
		}
		if keys[i].component != keys[j].component {
			return keys[i].component < keys[j].component
		}
		if keys[i].deviceID != keys[j].deviceID {
			return keys[i].deviceID < keys[j].deviceID
		}
		if keys[i].pid != keys[j].pid {
			return keys[i].pid < keys[j].pid
		}
		if keys[i].log2PageSize != keys[j].log2PageSize {
			return keys[i].log2PageSize < keys[j].log2PageSize
		}
		return keys[i].lineSize < keys[j].lineSize
	})

	for _, key := range keys {
		stat := summaries[key]
		fullLineFraction := 0.0
		singlePTEFraction := 0.0
		if stat.lines > 0 {
			fullLineFraction = float64(stat.hist[key.lineSize]) / float64(stat.lines)
			singlePTEFraction = float64(stat.hist[1]) / float64(stat.lines)
		}

		fmt.Fprintf(
			f,
			"%s,%s,%s,%s,%d,%d,%d,%d,%.6f,%d,%d,%d,%.6f,%.6f,%d,%d,%d,%d,%d,%d,%d,%d\n",
			key.scope,
			key.component,
			key.deviceID,
			key.pid,
			key.lineSize,
			key.log2PageSize,
			stat.lines,
			stat.totalAccesses,
			avgUint(stat.totalUsed, stat.lines),
			percentileUsed(stat.hist, stat.lines, 50),
			percentileUsed(stat.hist, stat.lines, 90),
			percentileUsed(stat.hist, stat.lines, 99),
			fullLineFraction,
			singlePTEFraction,
			stat.hist[1],
			stat.hist[2],
			stat.hist[3],
			stat.hist[4],
			stat.hist[5],
			stat.hist[6],
			stat.hist[7],
			stat.hist[8],
		)
	}

	return nil
}

func addAccessSummary(
	summaries map[accessSummaryKey]*accessSummaryStat,
	key accessSummaryKey,
	used int,
	accesses uint64,
) {
	stat := summaries[key]
	if stat == nil {
		stat = &accessSummaryStat{}
		summaries[key] = stat
	}
	stat.lines++
	stat.totalAccesses += accesses
	stat.totalUsed += uint64(used)
	stat.hist[used]++
}

func avgFloat(total float64, count uint64) float64 {
	if count == 0 {
		return 0
	}
	return total / float64(count)
}

func avgUint(total uint64, count uint64) float64 {
	if count == 0 {
		return 0
	}
	return float64(total) / float64(count)
}

func percentileUsed(hist [9]uint64, lines uint64, percentile uint64) int {
	if lines == 0 {
		return 0
	}

	rank := (lines*percentile + 99) / 100
	if rank == 0 {
		rank = 1
	}

	var seen uint64
	for used := 0; used < len(hist); used++ {
		seen += hist[used]
		if seen >= rank {
			return used
		}
	}
	return len(hist) - 1
}

func countBits8(value uint8) int {
	count := 0
	for value != 0 {
		count += int(value & 1)
		value >>= 1
	}
	return count
}

func defaultPTCLLineSize() int {
	return 8
}

func normalizePTCLLineSize(lineSize int) int {
	if lineSize < 1 || lineSize > 8 {
		return defaultPTCLLineSize()
	}
	return lineSize
}
