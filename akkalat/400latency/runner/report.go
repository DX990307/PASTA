package runner

import (
	"fmt"
	"regexp"
	"sort"
	"strconv"

	"github.com/sarchlab/akita/v3/mem/vm/tlb_gmmu"
	"github.com/sarchlab/mgpusim/v3/timing/cu"
)

type unusedPTCLRange struct {
	PageBlock  uint64
	PTCLStart  uint64
	PTCLEnd    uint64
	UniquePTCL int
	TotalCount int
}

func buildUnusedPTCLRanges(stats []tlb_gmmu.PrefetchUnusedPTCLStat) []unusedPTCLRange {
	if len(stats) == 0 {
		return nil
	}

	ranges := make([]unusedPTCLRange, 0)
	current := unusedPTCLRange{
		PageBlock:  stats[0].PageBlock,
		PTCLStart:  stats[0].PTCL,
		PTCLEnd:    stats[0].PTCL,
		UniquePTCL: 1,
		TotalCount: stats[0].Count,
	}

	for _, stat := range stats[1:] {
		if stat.PageBlock == current.PageBlock && stat.PTCL == current.PTCLEnd+1 {
			current.PTCLEnd = stat.PTCL
			current.UniquePTCL++
			current.TotalCount += stat.Count
			continue
		}

		ranges = append(ranges, current)
		current = unusedPTCLRange{
			PageBlock:  stat.PageBlock,
			PTCLStart:  stat.PTCL,
			PTCLEnd:    stat.PTCL,
			UniquePTCL: 1,
			TotalCount: stat.Count,
		}
	}

	ranges = append(ranges, current)
	return ranges
}

func (r *Runner) reportStats() {
	r.reportExecutionTime()
	r.reportInstCount()
	r.reportCPIStack()
	r.reportCacheLatency()
	r.reportRDMALatency()
	r.reportGMMULatency()
	r.reportMMULatency()
	r.reportCacheHitRate()
	r.reportTLBHitRate()
	r.reportGMMUCacheHitRate()
	r.reportTLBLatency()
	r.reportGMMUCacheLatency()
	r.reportGMMUCachePrefetchStats()
	r.reportRDMATransactionCount()
	r.reportGMMUTransactionCount()
	r.reportMMUTransactionCount()
	r.reportDRAMTransactionCount()
	r.reportIOMMUTLBStats()
	r.reportMMUCoalescingStats()
	// r.reportGMMUCounts()
	// r.reportGMMUCacheCounts()
	r.dumpMetrics()
}

func (r *Runner) reportInstCount() {
	// kernelTime := float64(r.kernelTimeCounter.BusyTime())
	for _, t := range r.instCountTracers {
		// kernelTime := float64(r.kernelTimeCounter.BusyTime())
		// float64(r.kernelTimeCounter.BusyTime())
		cuName := t.cu.Name()
		gpuID := regexp.MustCompile(`GPU\[(\d+)\]`)
		match := gpuID.FindStringSubmatch(cuName)
		num, err := strconv.Atoi(match[1])
		if err != nil {
			return
		}
		if num > 23 {
			num = num - 1
		}
		kernelTime := float64(r.perGPUKernelTimeCounter[num].BusyTime())

		cuFreq := float64(t.cu.(*cu.ComputeUnit).Freq)
		numCycle := kernelTime * cuFreq

		r.metricsCollector.Collect(
			t.cu.Name(), "cu_inst_count", float64(t.tracer.count))

		r.metricsCollector.Collect(
			t.cu.Name(), "cu_CPI", numCycle/float64(t.tracer.count))
	}
}

func (r *Runner) reportCPIStack() {
	for _, t := range r.cuCPITraces {
		cu := t.cu
		hook := t.tracer

		r.reportCPIStackEntries(hook, cu, false)
		// r.reportCPIStackEntries(hook, cu, true)
	}
}

func (r *Runner) reportCPIStackEntries(
	hook *cu.CPIStackTracer,
	cu TraceableComponent,
	simdStack bool,
) {
	cpiStack := hook.GetCPIStack()
	if simdStack {
		cpiStack = hook.GetSIMDCPIStack()
	}

	keys := make([]string, 0, len(cpiStack))
	for k := range cpiStack {
		keys = append(keys, k)
	}
	sort.Strings(keys)

	stackTypeName := "CPIStack"
	if simdStack {
		stackTypeName = "SIMDCPIStack"
	}

	for _, name := range keys {
		value := cpiStack[name]
		r.metricsCollector.Collect(cu.Name(), stackTypeName+"."+name, value)
	}
}

func (r *Runner) reportExecutionTime() {
	if r.Timing {
		r.metricsCollector.Collect(
			r.platform.Driver.Name(),
			"kernel_time", float64(r.kernelTimeCounter.BusyTime()))
		r.metricsCollector.Collect(
			r.platform.Driver.Name(),
			"total_time", float64(r.platform.Engine.CurrentTime()))

		for i, c := range r.perGPUKernelTimeCounter {
			r.metricsCollector.Collect(
				r.platform.GPUs[i].CommandProcessor.Name(),
				"kernel_time", float64(c.BusyTime()))
		}
	}
}

func (r *Runner) reportCacheLatency() {
	for _, tracer := range r.cacheLatencyTracers {
		if tracer.tracer.AverageTime() == 0 {
			continue
		}

		r.metricsCollector.Collect(
			tracer.cache.Name(),
			"req_average_latency",
			float64(tracer.tracer.AverageTime()),
		)
	}
}

func (r *Runner) reportRDMALatency() {
	for _, tracer := range r.rdmaLatencyTracers {
		if tracer.tracer.AverageTime() == 0 {
			continue
		}

		r.metricsCollector.Collect(
			tracer.rdma.Name(),
			"req_average_latency",
			float64(tracer.tracer.AverageTime()),
		)
	}
}

func (r *Runner) reportTLBLatency() {
	for _, tracer := range r.tlbLatencyTracers {
		if tracer.tracer.AverageTime() == 0 {
			continue
		}

		r.metricsCollector.Collect(
			tracer.tlb.Name(),
			"req_average_latency",
			float64(tracer.tracer.AverageTime()),
		)
	}
}

func (r *Runner) reportCacheHitRate() {
	for _, tracer := range r.cacheHitRateTracers {
		readHit := tracer.tracer.GetStepCount("read-hit")
		readMiss := tracer.tracer.GetStepCount("read-miss")
		readMSHRHit := tracer.tracer.GetStepCount("read-mshr-miss")
		writeHit := tracer.tracer.GetStepCount("write-hit")
		writeMiss := tracer.tracer.GetStepCount("write-miss")
		writeMSHRHit := tracer.tracer.GetStepCount("write-mshr-miss")

		totalTransaction := readHit + readMiss + readMSHRHit +
			writeHit + writeMiss + writeMSHRHit

		if totalTransaction == 0 {
			continue
		}

		r.metricsCollector.Collect(
			tracer.cache.Name(), "read-hit", float64(readHit))
		r.metricsCollector.Collect(
			tracer.cache.Name(), "read-miss", float64(readMiss))
		r.metricsCollector.Collect(
			tracer.cache.Name(), "read-mshr-hit", float64(readMSHRHit))
		r.metricsCollector.Collect(
			tracer.cache.Name(), "write-hit", float64(writeHit))
		r.metricsCollector.Collect(
			tracer.cache.Name(), "write-miss", float64(writeMiss))
		r.metricsCollector.Collect(
			tracer.cache.Name(), "write-mshr-hit", float64(writeMSHRHit))
	}
}

func (r *Runner) reportTLBHitRate() {
	for _, tracer := range r.tlbHitRateTracers {
		hit := tracer.tracer.GetStepCount("hit")
		miss := tracer.tracer.GetStepCount("miss")
		mshrHit := tracer.tracer.GetStepCount("mshr-hit")

		totalTransaction := hit + miss + mshrHit

		if totalTransaction == 0 {
			continue
		}

		r.metricsCollector.Collect(
			tracer.tlb.Name(), "hit", float64(hit))
		r.metricsCollector.Collect(
			tracer.tlb.Name(), "miss", float64(miss))
		r.metricsCollector.Collect(
			tracer.tlb.Name(), "mshr-hit", float64(mshrHit))
	}
}

func (r *Runner) reportRDMATransactionCount() {
	for _, t := range r.rdmaTransactionCounters {
		r.metricsCollector.Collect(
			t.rdmaEngine.Name(),
			"outgoing_trans_count",
			float64(t.outgoingTracer.TotalCount()),
		)
		r.metricsCollector.Collect(
			t.rdmaEngine.Name(),
			"incoming_trans_count",
			float64(t.incomingTracer.TotalCount()),
		)
	}
}

func (r *Runner) reportGMMUTransactionCount() {
	for _, t := range r.gmmuTransactionCounters {
		r.metricsCollector.Collect(
			t.gmmuEngine.Name(),
			"outgoing_trans_count",
			float64(t.outgoingTracer.TotalCount()),
		)
		r.metricsCollector.Collect(
			t.gmmuEngine.Name(),
			"incoming_trans_count",
			float64(t.incomingTracer.TotalCount()),
		)
	}
}

func (r *Runner) reportMMUTransactionCount() {
	for _, t := range r.mmuTransactionCounters {
		r.metricsCollector.Collect(
			t.mmuEngine.Name(),
			"outgoing_trans_count",
			float64(t.outgoingTracer.TotalCount()),
		)
		r.metricsCollector.Collect(
			t.mmuEngine.Name(),
			"incoming_trans_count",
			float64(t.incomingTracer.TotalCount()),
		)
	}
}

func (r *Runner) reportDRAMTransactionCount() {
	for _, t := range r.dramTracers {
		r.metricsCollector.Collect(
			t.dram.Name(),
			"read_trans_count",
			float64(t.tracer.readCount),
		)
		r.metricsCollector.Collect(
			t.dram.Name(),
			"write_trans_count",
			float64(t.tracer.writeCount),
		)
		r.metricsCollector.Collect(
			t.dram.Name(),
			"read_avg_latency",
			float64(t.tracer.readAvgLatency),
		)
		r.metricsCollector.Collect(
			t.dram.Name(),
			"write_avg_latency",
			float64(t.tracer.writeAvgLatency),
		)
		r.metricsCollector.Collect(
			t.dram.Name(),
			"read_size",
			float64(t.tracer.readSize),
		)
		r.metricsCollector.Collect(
			t.dram.Name(),
			"write_size",
			float64(t.tracer.writeSize),
		)
	}
}

func (r *Runner) reportGMMUCacheHitRate() {
	for _, tracer := range r.gmmuCacheHitRateTracers {
		low, high := tracer.gmmuCache.PTCLThresholds()
		toPTCL, toPTE := tracer.gmmuCache.ModeSwitchCounts()
		totalDownstream, localDownstream, iommuDownstream :=
			tracer.gmmuCache.DownstreamRequestCounts()
		ptclModeEnabled := 0.0
		if tracer.gmmuCache.PTCLModeEnabled() {
			ptclModeEnabled = 1.0
		}
		vpnMSHRBaselineEnabled := 0.0
		if tracer.gmmuCache.VPNMSHRBaselineEnabled() {
			vpnMSHRBaselineEnabled = 1.0
		}

		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(), "ptcl_mode_enabled", ptclModeEnabled)
		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(),
			"vpn_mshr_baseline_enabled",
			vpnMSHRBaselineEnabled,
		)
		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(),
			"ptcl_coalescing_counter",
			float64(tracer.gmmuCache.CoalescingCounter()),
		)
		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(), "ptcl_threshold_low", float64(low))
		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(), "ptcl_threshold_high", float64(high))
		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(), "ptcl_switch_to_ptcl", float64(toPTCL))
		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(), "ptcl_switch_to_pte", float64(toPTE))
		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(),
			"downstream_req_count",
			float64(totalDownstream),
		)
		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(),
			"local_req_count",
			float64(localDownstream),
		)
		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(),
			"iommu_req_count",
			float64(iommuDownstream),
		)

		hit := tracer.tracer.GetStepCount("hit")
		miss := tracer.tracer.GetStepCount("miss")
		mshrHit := tracer.tracer.GetStepCount("mshr-hit")

		totalTransaction := hit + miss + mshrHit

		if totalTransaction == 0 {
			continue
		}

		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(), "hit", float64(hit))
		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(), "miss", float64(miss))
		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(), "mshr-hit", float64(mshrHit))
	}
}

func (r *Runner) reportGMMUCacheLatency() {
	for _, tracer := range r.gmmuCacheLatencyTracers {
		if tracer.tracer.AverageTime() == 0 {
			continue
		}

		r.metricsCollector.Collect(
			tracer.gmmuCache.Name(),
			"req_average_latency",
			float64(tracer.tracer.AverageTime()),
		)
	}
}

func (r *Runner) reportGMMUCachePrefetchStats() {
	if r.platform == nil {
		return
	}

	for _, gpu := range r.platform.GPUs {
		if gpu == nil || gpu.GMMUTLB == nil {
			continue
		}

		inserted, useful, usefulHit, lateUseful, unused, residentPending :=
			gpu.GMMUTLB.PrefetchOutcomeStats()
		blockStats := gpu.GMMUTLB.PrefetchBlockStats()
		unusedPTCLStats := gpu.GMMUTLB.PrefetchUnusedPTCLStats()

		r.metricsCollector.Collect(gpu.GMMUTLB.Name(), "prefetch_exact_inserted", float64(inserted))
		r.metricsCollector.Collect(gpu.GMMUTLB.Name(), "prefetch_exact_useful", float64(useful))
		r.metricsCollector.Collect(gpu.GMMUTLB.Name(), "prefetch_exact_useful_hit", float64(usefulHit))
		r.metricsCollector.Collect(gpu.GMMUTLB.Name(), "prefetch_exact_late_useful", float64(lateUseful))
		r.metricsCollector.Collect(gpu.GMMUTLB.Name(), "prefetch_exact_unused", float64(unused))
		r.metricsCollector.Collect(gpu.GMMUTLB.Name(), "prefetch_exact_resident_pending", float64(residentPending))

		usefulRate := 0.0
		if inserted > 0 {
			usefulRate = float64(useful) / float64(inserted)
		}
		unusedRate := 0.0
		if inserted > 0 {
			unusedRate = float64(unused) / float64(inserted)
		}
		r.metricsCollector.Collect(gpu.GMMUTLB.Name(), "prefetch_exact_useful_rate", usefulRate)
		r.metricsCollector.Collect(gpu.GMMUTLB.Name(), "prefetch_exact_unused_rate", unusedRate)

		if inserted == 0 && useful == 0 && lateUseful == 0 && unused == 0 && residentPending == 0 {
			continue
		}

		fmt.Printf(
			"[PF][gpu-summary] component=%s inserted=%d useful=%d useful_hit=%d late_useful=%d unused=%d resident_pending=%d useful_rate=%.6f unused_rate=%.6f\n",
			gpu.GMMUTLB.Name(), inserted, useful, usefulHit, lateUseful, unused, residentPending, usefulRate, unusedRate,
		)

		for _, block := range blockStats {
			if block.Inserted == 0 && block.Useful == 0 && block.LateUseful == 0 && block.Unused == 0 && block.ResidentPending == 0 {
				continue
			}

			blockUsefulRate := 0.0
			if block.Inserted > 0 {
				blockUsefulRate = float64(block.Useful) / float64(block.Inserted)
			}
			blockUnusedRate := 0.0
			if block.Inserted > 0 {
				blockUnusedRate = float64(block.Unused) / float64(block.Inserted)
			}

			fmt.Printf(
				"[PF][gpu-bo-summary] component=%s page_block=%d inserted=%d useful=%d useful_hit=%d late_useful=%d unused=%d resident_pending=%d useful_rate=%.6f unused_rate=%.6f\n",
				gpu.GMMUTLB.Name(), block.PageBlock, block.Inserted, block.Useful, block.UsefulHit, block.LateUseful, block.Unused, block.ResidentPending, blockUsefulRate, blockUnusedRate,
			)
		}

		for _, ptclRange := range buildUnusedPTCLRanges(unusedPTCLStats) {
			fmt.Printf(
				"[PF][gpu-unused-ptcl-range] component=%s page_block=%d ptcl_start=%d ptcl_end=%d unique_ptcls=%d total_unused=%d\n",
				gpu.GMMUTLB.Name(), ptclRange.PageBlock, ptclRange.PTCLStart, ptclRange.PTCLEnd, ptclRange.UniquePTCL, ptclRange.TotalCount,
			)
		}
	}
}

func (r *Runner) reportGMMULatency() {
	for _, tracer := range r.gmmuLatencyTracers {
		if tracer.tracer.AverageTime() == 0 {
			continue
		}

		r.metricsCollector.Collect(
			tracer.gmmu.Name(),
			"req_average_latency",
			float64(tracer.tracer.AverageTime()),
		)
	}
}

func (r *Runner) reportMMULatency() {
	for _, tracer := range r.mmuLatencyTracers {
		if tracer.tracer.AverageTime() == 0 {
			continue
		}

		r.metricsCollector.Collect(
			"MMU",
			"req_average_latency",
			float64(tracer.tracer.AverageTime()),
		)
	}
}

func (r *Runner) reportIOMMUTLBStats() {
	if r.platform == nil || r.platform.IOMMUTLB == nil {
		return
	}

	prefetchEnabled, demandPTCLReturn, generated, enqueued, dropped, rejectedByPrefix,
		rejectedByDuplicate, rejectedByInvalid, noClearPatternSkips, admitted, promoted :=
		r.platform.IOMMUTLB.PrefetchStats()
	completed, useful, late, lostBeforeUse := r.platform.IOMMUTLB.PrefetchOutcomeStats()
	blockStats := r.platform.IOMMUTLB.PrefetchBlockStats()
	prefetchEnabledFloat := 0.0
	if prefetchEnabled {
		prefetchEnabledFloat = 1.0
	}
	demandPTCLReturnFloat := 0.0
	if demandPTCLReturn {
		demandPTCLReturnFloat = 1.0
	}

	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"incoming_req_count",
		float64(r.platform.IOMMUTLB.IncomingRequestCount()),
	)
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"req_to_mmu_count",
		float64(r.platform.IOMMUTLB.DownstreamRequestCount()),
	)
	demandPTEOnlyFloat := 0.0
	if r.platform.IOMMUTLB.DemandPTEOnlyEnabled() {
		demandPTEOnlyFloat = 1.0
	}
	vpnMSHRBaselineFloat := 0.0
	if r.platform.IOMMUTLB.VPNMSHRBaselineEnabled() {
		vpnMSHRBaselineFloat = 1.0
	}
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"demand_pte_only_enabled",
		demandPTEOnlyFloat,
	)
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"vpn_mshr_baseline_enabled",
		vpnMSHRBaselineFloat,
	)
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"lookup_latency_cycles_per_bit",
		float64(r.platform.IOMMUTLB.LookupLatencyCycles()),
	)
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"prefetch_enabled",
		prefetchEnabledFloat,
	)
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"prefetch_demand_ptcl_return_enabled",
		demandPTCLReturnFloat,
	)
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"prefetch_generated_candidates",
		float64(generated),
	)
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"prefetch_enqueued_candidates",
		float64(enqueued),
	)
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"prefetch_dropped_candidates",
		float64(dropped),
	)
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"prefetch_rejected_by_prefix_filter",
		float64(rejectedByPrefix),
	)
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"prefetch_rejected_by_duplicate_filter",
		float64(rejectedByDuplicate),
	)
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"prefetch_rejected_by_invalid_target",
		float64(rejectedByInvalid),
	)
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"prefetch_admitted_learners",
		float64(admitted),
	)
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"prefetch_promoted_demands",
		float64(promoted),
	)
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"prefetch_no_clear_pattern_skips",
		float64(noClearPatternSkips),
	)
	// Keep the old metric name for compatibility with existing analysis scripts.
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"prefetch_pattern_resets",
		float64(noClearPatternSkips),
	)
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"prefetch_completed_fills",
		float64(completed),
	)
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"prefetch_useful_hits",
		float64(useful),
	)
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"prefetch_late_demands",
		float64(late),
	)
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"prefetch_lost_before_use",
		float64(lostBeforeUse),
	)
	usefulRateByEnqueued := 0.0
	if enqueued > 0 {
		usefulRateByEnqueued = float64(useful) / float64(enqueued)
	}
	usefulRateByCompleted := 0.0
	if completed > 0 {
		usefulRateByCompleted = float64(useful) / float64(completed)
	}
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"prefetch_useful_rate_by_enqueued",
		usefulRateByEnqueued,
	)
	r.metricsCollector.Collect(
		r.platform.IOMMUTLB.Name(),
		"prefetch_useful_rate_by_completed",
		usefulRateByCompleted,
	)

	if !prefetchEnabled {
		return
	}

	fmt.Printf(
		"[PF][summary] component=%s enqueued=%d completed=%d useful=%d late=%d lost_before_use=%d useful_rate_enqueued=%.6f useful_rate_completed=%.6f no_clear_pattern_skips=%d\n",
		r.platform.IOMMUTLB.Name(),
		enqueued,
		completed,
		useful,
		late,
		lostBeforeUse,
		usefulRateByEnqueued,
		usefulRateByCompleted,
		noClearPatternSkips,
	)

	for _, block := range blockStats {
		if block.Enqueued == 0 && block.Completed == 0 && block.Useful == 0 && block.Late == 0 && block.LostBeforeUse == 0 {
			continue
		}

		blockUsefulRateByEnqueued := 0.0
		if block.Enqueued > 0 {
			blockUsefulRateByEnqueued = float64(block.Useful) / float64(block.Enqueued)
		}
		blockUsefulRateByCompleted := 0.0
		if block.Completed > 0 {
			blockUsefulRateByCompleted = float64(block.Useful) / float64(block.Completed)
		}

		fmt.Printf(
			"[PF][bo-summary] component=%s page_block=%d enqueued=%d completed=%d useful=%d late=%d lost_before_use=%d useful_rate_enqueued=%.6f useful_rate_completed=%.6f\n",
			r.platform.IOMMUTLB.Name(),
			block.PageBlock,
			block.Enqueued,
			block.Completed,
			block.Useful,
			block.Late,
			block.LostBeforeUse,
			blockUsefulRateByEnqueued,
			blockUsefulRateByCompleted,
		)
	}
}

func (r *Runner) reportMMUCoalescingStats() {
	if r.platform == nil || len(r.platform.GPUs) == 0 || r.platform.GPUs[0].MMUEngine == nil {
		return
	}

	enabled := 0.0
	if r.platform.GPUs[0].MMUEngine.WalkCoalescingEnabled() {
		enabled = 1.0
	}
	lastLevel, twoLevel := r.platform.GPUs[0].MMUEngine.CoalescingStats()
	r.metricsCollector.Collect(
		"MMU",
		"coalescing_enabled",
		enabled,
	)
	r.metricsCollector.Collect(
		"MMU",
		"last_level_coalesced_reqs",
		float64(lastLevel),
	)
	r.metricsCollector.Collect(
		"MMU",
		"two_level_coalesced_reqs",
		float64(twoLevel),
	)
}

// func (r *Runner) reportGMMUCounts() {
// 	for _, t := range r.gmmuCountTracers {
// 		r.metricsCollector.Collect(
// 			t.gmmu.Name(),
// 			"total_ats_count",
// 			float64(t.tracer.GetTotalATSCount()),
// 		)
// 		r.metricsCollector.Collect(
// 			t.gmmu.Name(),
// 			"local_ats_count",
// 			float64(t.tracer.GetLocalATSCount()),
// 		)
// 		r.metricsCollector.Collect(
// 			t.gmmu.Name(),
// 			"remote_ats_count",
// 			float64(t.tracer.GetRemoteATSCount()),
// 		)
// 	}
// }

// func (r *Runner) reportGMMUCacheCounts() {
// 	for _, t := range r.GMMUTLBTracers {
// 		r.metricsCollector.Collect(
// 			t.tlb.Name(),
// 			"AverageLocalAccessCounts",
// 			float64(t.tracer.ReportAverageLocalAccessCounts()),
// 		)
// 		r.metricsCollector.Collect(
// 			t.tlb.Name(),
// 			"AverageRemoteAccessCounts",
// 			float64(t.tracer.ReportAverageRemoteAccessCounts()),
// 		)
// 	}
// }
